"""Event intake: move evidence messages from a queue into the durable job store.

At-least-once and idempotent: a message is acknowledged only after its job run is
committed; a redelivered message maps to the same idempotency key (SHA-256 of the
body). A body that is not a JSON object is dead-lettered and never retried.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg

from gorgona_ai.jobs.runtime import enqueue


@dataclass(frozen=True, slots=True)
class Message:
    body: bytes
    handle: Any = None


class MessageSource(Protocol):
    def receive(self, max_messages: int) -> list[Message]: ...
    def complete(self, message: Message) -> None: ...
    def dead_letter(self, message: Message, reason: str) -> None: ...


def pump(
    conn: psycopg.Connection, source: MessageSource, *, batch: int = 50, max_batches: int = 20
) -> dict[str, int]:
    counts = {"queued": 0, "duplicate": 0, "dead_lettered": 0}
    for _ in range(max_batches):
        messages = source.receive(batch)
        if not messages:
            break
        for message in messages:
            try:
                raw = json.loads(message.body)
                if not isinstance(raw, dict):
                    raise ValueError("not an object")
            except ValueError, UnicodeDecodeError:
                source.dead_letter(message, "malformed_json")
                counts["dead_lettered"] += 1
                continue
            key = "ingest:" + hashlib.sha256(message.body).hexdigest()
            with conn.transaction():
                created = enqueue(conn, "ingest", key, payload=raw)
            source.complete(message)
            counts["queued" if created else "duplicate"] += 1
    return counts


class ServiceBusSource:
    """Azure Service Bus queue receiver authenticated with the job's managed identity."""

    def __init__(self, fully_qualified_namespace: str, queue: str, client_id: str | None) -> None:
        from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
        from azure.servicebus import ServiceBusClient

        credential = (
            ManagedIdentityCredential(client_id=client_id)
            if client_id
            else DefaultAzureCredential()
        )
        self._client = ServiceBusClient(fully_qualified_namespace, credential=credential)
        self._receiver = self._client.get_queue_receiver(queue_name=queue, max_wait_time=5)

    def receive(self, max_messages: int) -> list[Message]:
        return [
            Message(b"".join(bytes(part) for part in m.body), m)
            for m in self._receiver.receive_messages(
                max_message_count=max_messages, max_wait_time=5
            )
        ]

    def complete(self, message: Message) -> None:
        self._receiver.complete_message(message.handle)

    def dead_letter(self, message: Message, reason: str) -> None:
        self._receiver.dead_letter_message(message.handle, reason=reason)

    def close(self) -> None:
        self._receiver.close()
        self._client.close()


class ListSource:
    """In-memory source with the same semantics (local runs and tests)."""

    def __init__(self, bodies: Iterable[bytes]) -> None:
        self.pending = [Message(b) for b in bodies]
        self.completed: list[Message] = []
        self.dead: list[tuple[Message, str]] = []

    def receive(self, max_messages: int) -> list[Message]:
        taken, self.pending = self.pending[:max_messages], self.pending[max_messages:]
        return taken

    def complete(self, message: Message) -> None:
        self.completed.append(message)

    def dead_letter(self, message: Message, reason: str) -> None:
        self.dead.append((message, reason))
