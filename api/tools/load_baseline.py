"""Load baseline for the public customer booking API (HTTP only).

Measures throughput, p50/p95/p99 latency, errors and slot contention through the same
public contract a salon site uses (`/v1/customer/*`, tenant resolved from the Host).
Three phases:

1. read: bootstrap + availability for `--duration` seconds at `--concurrency`;
2. contention: `--contenders` clients hold the same slot at once; exactly one may win,
   every other attempt must be a clean 409 SLOT_CONFLICT;
3. bookings: hold + confirm up to `--bookings` distinct slots at `--concurrency`.

Safety:
- It only creates bookings for a tenant whose public name starts with "FAKE ".
- A non-loopback target needs `--allow-remote`. Pointing this at staging still needs the
  owner's approval of the staging window; production is out of scope.
- The report holds no customer data, tokens or booking IDs.

Exit status 1 when any request fails with 5xx or a transport error, or when contention
does not produce exactly one winner.

    uv run python tools/load_baseline.py --base-url http://127.0.0.1:8000 \\
        --host salon-a.example.test --day 2026-10-02 --out load-report.json
"""

import argparse
import asyncio
import ipaddress
import json
import secrets
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

FAKE_PREFIX = "FAKE "
DETAILS = {
    "name": "FAKE Load Guest",
    "email": "fake-load@example.test",
    "phone": "+1 555 010 0000",
    "accept_policy": True,
}


@dataclass
class Recorder:
    latencies: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    statuses: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    codes: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    elapsed: dict[str, float] = field(default_factory=dict)

    async def call(
        self, client: httpx.AsyncClient, op: str, method: str, url: str, **kwargs: Any
    ) -> httpx.Response | None:
        started = time.perf_counter()
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            self.latencies[op].append((time.perf_counter() - started) * 1000)
            self.statuses[op][f"transport:{type(exc).__name__}"] += 1
            return None
        self.latencies[op].append((time.perf_counter() - started) * 1000)
        self.statuses[op][str(response.status_code)] += 1
        if response.status_code >= 400:
            try:
                self.codes[op][str(response.json()["error"]["code"])] += 1
            except ValueError, KeyError, TypeError:
                self.codes[op]["unparseable"] += 1
        return response

    def failures(self) -> int:
        return sum(
            n
            for counter in self.statuses.values()
            for status, n in counter.items()
            if status.startswith("transport:") or status.startswith("5")
        )


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; 0.0 for an empty sample."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * pct // 100))
    return round(ordered[int(rank) - 1], 3)


def summarize(recorder: Recorder) -> dict[str, Any]:
    operations: dict[str, Any] = {}
    for op, values in recorder.latencies.items():
        window = recorder.elapsed.get(op)
        operations[op] = {
            "count": len(values),
            "throughput_rps": round(len(values) / window, 2) if window else None,
            "p50_ms": percentile(values, 50),
            "p95_ms": percentile(values, 95),
            "p99_ms": percentile(values, 99),
            "max_ms": round(max(values), 3) if values else 0.0,
            "statuses": dict(recorder.statuses[op]),
            "error_codes": dict(recorder.codes[op]),
        }
    return operations


def is_loopback(base_url: str) -> bool:
    host = urlsplit(base_url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _selection(boot: dict[str, Any]) -> dict[str, Any]:
    location = boot["locations"][0]
    variant = boot["variants"][0]
    return {"location_id": location["id"], "variant_id": variant["id"], "add_on_ids": []}


async def _slots(
    client: httpx.AsyncClient, recorder: Recorder, selection: dict[str, Any], day: str
) -> list[dict[str, Any]]:
    response = await recorder.call(
        client,
        "availability",
        "POST",
        "/v1/customer/availability",
        json={**selection, "day": day, "resource_id": None},
    )
    if response is None or response.status_code != 200:
        return []
    slots: list[dict[str, Any]] = response.json()["slots"]
    return slots


async def _hold(
    client: httpx.AsyncClient, recorder: Recorder, selection: dict[str, Any], slot: dict[str, Any]
) -> tuple[httpx.Response | None, str]:
    token = secrets.token_urlsafe(32)
    response = await recorder.call(
        client,
        "hold",
        "POST",
        "/v1/customer/holds",
        headers={"Booking-Token": token, "Idempotency-Key": secrets.token_hex(16)},
        json={**selection, "resource_id": slot["resource_id"], "start_at": slot["start_at"]},
    )
    return response, token


async def read_phase(
    client: httpx.AsyncClient,
    recorder: Recorder,
    selection: dict[str, Any],
    day: str,
    concurrency: int,
    duration: float,
) -> None:
    deadline = time.monotonic() + duration

    async def worker(n: int) -> None:
        while time.monotonic() < deadline:
            if n % 2:
                await recorder.call(client, "bootstrap", "GET", "/v1/customer/bootstrap")
            else:
                await _slots(client, recorder, selection, day)

    started = time.perf_counter()
    await asyncio.gather(*(worker(n) for n in range(concurrency)))
    elapsed = time.perf_counter() - started
    recorder.elapsed["bootstrap"] = recorder.elapsed["availability"] = elapsed


async def contention_phase(
    client: httpx.AsyncClient,
    recorder: Recorder,
    selection: dict[str, Any],
    slot: dict[str, Any],
    contenders: int,
) -> dict[str, Any]:
    results = await asyncio.gather(
        *(_hold(client, recorder, selection, slot) for _ in range(contenders))
    )
    outcome: Counter[str] = Counter()
    for response, _ in results:
        if response is None:
            outcome["transport_error"] += 1
        elif response.status_code == 201:
            outcome["winner"] += 1
        elif response.status_code == 409 and response.json()["error"]["code"] == "SLOT_CONFLICT":
            outcome["slot_conflict"] += 1
        else:
            outcome[f"other:{response.status_code}"] += 1
    return {
        "contenders": contenders,
        "winners": outcome["winner"],
        "slot_conflicts": outcome["slot_conflict"],
        "other": {k: v for k, v in outcome.items() if k not in ("winner", "slot_conflict")},
        "correct": outcome["winner"] == 1 and outcome["slot_conflict"] == contenders - 1,
    }


async def booking_phase(
    client: httpx.AsyncClient,
    recorder: Recorder,
    selection: dict[str, Any],
    slots: list[dict[str, Any]],
    concurrency: int,
) -> dict[str, int]:
    gate = asyncio.Semaphore(concurrency)
    counts: Counter[str] = Counter()

    async def book(slot: dict[str, Any]) -> None:
        async with gate:
            held, token = await _hold(client, recorder, selection, slot)
            if held is None or held.status_code != 201:
                counts["hold_rejected"] += 1
                return
            confirmed = await recorder.call(
                client,
                "confirm",
                "POST",
                f"/v1/customer/bookings/{held.json()['booking_id']}/confirm",
                headers={"Booking-Token": token, "Idempotency-Key": secrets.token_hex(16)},
                json=DETAILS,
            )
            counts[
                "confirmed"
                if confirmed is not None and confirmed.status_code == 200
                else "confirm_rejected"
            ] += 1

    started = time.perf_counter()
    await asyncio.gather(*(book(slot) for slot in slots))
    recorder.elapsed["hold"] = recorder.elapsed["confirm"] = time.perf_counter() - started
    return {"attempted": len(slots), **counts}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    recorder = Recorder()
    headers = {"host": args.host} if args.host else {}
    limits = httpx.Limits(max_connections=max(args.concurrency, args.contenders) + 4)
    async with httpx.AsyncClient(
        base_url=args.base_url, headers=headers, timeout=args.timeout, limits=limits
    ) as client:
        boot_response = await client.get("/v1/customer/bootstrap")
        boot_response.raise_for_status()
        boot = boot_response.json()
        if not str(boot["name"]).startswith(FAKE_PREFIX):
            raise SystemExit("refusing: the target tenant is not a FAKE tenant")
        selection = _selection(boot)

        await read_phase(client, recorder, selection, args.day, args.concurrency, args.duration)
        slots = await _slots(client, recorder, selection, args.day)
        if len(slots) < 2:
            raise SystemExit("not enough open FAKE availability on --day for the baseline")
        contention = await contention_phase(client, recorder, selection, slots[0], args.contenders)
        bookings = await booking_phase(
            client, recorder, selection, slots[1 : 1 + args.bookings], args.concurrency
        )

    failures = recorder.failures()
    return {
        "version": 1,
        "config": {
            "loopback_target": is_loopback(args.base_url),
            "day": args.day,
            "concurrency": args.concurrency,
            "duration_s": args.duration,
            "contenders": args.contenders,
            "bookings": args.bookings,
        },
        "operations": summarize(recorder),
        "contention": contention,
        "bookings": bookings,
        "failures": failures,
        "verdict": "PASS" if failures == 0 and contention["correct"] else "FAIL",
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--host", help="Host header selecting the FAKE tenant")
    parser.add_argument("--day", required=True, help="ISO date with open FAKE availability")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration", type=float, default=30.0, help="read phase seconds")
    parser.add_argument("--contenders", type=int, default=10)
    parser.add_argument("--bookings", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument("--out", help="write the JSON report here as well as stdout")
    args = parser.parse_args(argv)
    if not is_loopback(args.base_url) and not args.allow_remote:
        parser.error("non-loopback target: pass --allow-remote (staging window approved)")
    if min(args.concurrency, args.contenders, args.bookings) < 1 or args.contenders < 2:
        parser.error("concurrency, bookings >= 1 and contenders >= 2 are required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = asyncio.run(run(args))
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)  # noqa: T201
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
