"""HTTP contract of POST /v1/holds, with the booking service replaced by a stub.

The database path (23P01 -> SlotConflictError) is covered by integration tests;
here we check the API maps domain outcomes to the error envelope and statuses.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from gorgona_booking.api.app import create_app
from gorgona_booking.api.holds import get_booking_service, get_tenant_id
from gorgona_booking.booking.models import (
    BookingResult,
    IdempotencyKeyReusedError,
    ReservationRequest,
    SlotConflictError,
)
from gorgona_booking.catalog.quote import ServiceNotBookableError
from gorgona_booking.config import Settings
from gorgona_booking.errors import DomainError

pytestmark = pytest.mark.anyio

TENANT = uuid4()
RESOURCE = uuid4()
BODY: dict[str, Any] = {
    "resource_id": str(RESOURCE),
    "variant_id": str(uuid4()),
    "add_on_ids": [],
    "start_at": "2030-05-01T10:00:00-04:00",
}
KEY = {"Idempotency-Key": "test-key-0001"}


class StubService:
    def __init__(self, outcome: BookingResult | DomainError) -> None:
        self.outcome = outcome
        self.calls: list[tuple[UUID, ReservationRequest, dict[str, Any]]] = []

    async def create_hold(
        self, tenant_id: UUID, request: ReservationRequest, **kwargs: Any
    ) -> BookingResult:
        self.calls.append((tenant_id, request, kwargs))
        if isinstance(self.outcome, DomainError):
            raise self.outcome
        return self.outcome


def _result(replayed: bool = False) -> BookingResult:
    return BookingResult(
        booking_id=uuid4(),
        status="HOLD",
        resource_id=RESOURCE,
        starts_at=datetime(2030, 5, 1, 14, tzinfo=UTC),
        ends_at=datetime(2030, 5, 1, 15, tzinfo=UTC),
        hold_expires_at=datetime(2030, 5, 1, 13, 10, tzinfo=UTC),
        total_cents=5000,
        currency="USD",
        quote={"version": 1, "total_cents": 5000},
        replayed=replayed,
    )


def _app(service: StubService) -> FastAPI:
    app = create_app(Settings())

    async def tenant() -> UUID:
        return TENANT

    app.dependency_overrides[get_booking_service] = lambda: service
    app.dependency_overrides[get_tenant_id] = tenant
    return app


async def _post(app: FastAPI, body: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/v1/holds", json=body, headers=headers)


async def test_created_hold_returns_201_and_passes_trusted_context() -> None:
    service = StubService(_result())
    response = await _post(_app(service), BODY, KEY)
    assert response.status_code == 201
    assert response.json()["status"] == "HOLD"
    assert "idempotent-replayed" not in response.headers
    tenant_id, request, kwargs = service.calls[0]
    assert tenant_id == TENANT
    assert request.starts_at == datetime(2030, 5, 1, 14, tzinfo=UTC)
    assert kwargs["idempotency_key"] == "test-key-0001"
    assert kwargs["actor"] == "anonymous"


async def test_replay_is_marked() -> None:
    response = await _post(_app(StubService(_result(replayed=True))), BODY, KEY)
    assert response.status_code == 201
    assert response.headers["idempotent-replayed"] == "true"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (SlotConflictError("taken"), 409, "SLOT_CONFLICT"),
        (IdempotencyKeyReusedError("reused"), 422, "IDEMPOTENCY_KEY_REUSED"),
        (
            ServiceNotBookableError("no", reason="booking_duration_unknown"),
            422,
            "SERVICE_NOT_BOOKABLE",
        ),
    ],
)
async def test_domain_errors_map_to_statuses(error: DomainError, status: int, code: str) -> None:
    response = await _post(_app(StubService(error)), BODY, KEY)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["request_id"]


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        (BODY, {}),  # missing Idempotency-Key
        (BODY, {"Idempotency-Key": "short"}),
        ({**BODY, "start_at": "2030-05-01T10:00:00"}, KEY),  # no UTC offset
        ({**BODY, "tenant_id": str(uuid4())}, KEY),  # clients cannot pick a tenant
        ({**BODY, "add_on_ids": [str(uuid4()) for _ in range(11)]}, KEY),
    ],
)
async def test_invalid_requests_are_rejected_before_the_service(
    body: dict[str, Any], headers: dict[str, str]
) -> None:
    service = StubService(_result())
    response = await _post(_app(service), body, headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert service.calls == []


async def test_without_a_database_booking_is_unavailable() -> None:
    response = await _post(create_app(Settings()), BODY, KEY)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
