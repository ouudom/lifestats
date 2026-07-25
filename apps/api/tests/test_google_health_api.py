from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from src.core.errors import ConflictError
from src.modules.google_health import router as api
from src.modules.google_health import service as google_health_service
from src.modules.google_health.schemas import SyncRequest
from src.modules.google_health.service import GoogleHealthService


def test_sync_request_preserves_days_and_accepts_selected_types() -> None:
    request = SyncRequest.model_validate({"days": 7, "dataTypes": ["sleep", "steps", "sleep"]})
    assert request.days == 7
    assert request.data_types == ["sleep", "steps"]


def test_sync_request_requires_bounded_aware_range() -> None:
    with pytest.raises(ValidationError):
        SyncRequest.model_validate({"startAt": "2026-07-01T00:00:00Z"})
    with pytest.raises(ValidationError):
        SyncRequest.model_validate(
            {
                "startAt": "2025-01-01T00:00:00Z",
                "endAt": "2026-07-01T00:00:00Z",
            }
        )
    request = SyncRequest.model_validate(
        {
            "startAt": "2026-07-01T00:00:00+07:00",
            "endAt": "2026-07-02T00:00:00+07:00",
        }
    )
    assert request.start_at == datetime(2026, 6, 30, 17, tzinfo=UTC)
    assert request.end_at == datetime(2026, 7, 1, 17, tzinfo=UTC)


def test_router_exposes_v2_sync_and_connection_surface() -> None:
    methods_by_path = {
        (route.path, method) for route in api.router.routes for method in (route.methods or set())
    }
    assert {
        ("/integrations/google-health", "GET"),
        ("/integrations/google-health/disconnect", "POST"),
        ("/sync", "GET"),
        ("/sync", "POST"),
        ("/sync/events", "GET"),
        ("/sync/{data_type}", "GET"),
        ("/sync/{data_type}", "POST"),
    } <= methods_by_path


@pytest.mark.asyncio
async def test_manual_sync_queues_only_selected_enabled_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(id=uuid4())
    job = SimpleNamespace(data_type="sleep", enabled=True)
    queued: list[tuple[object, ...]] = []

    async def seed(*_: object, **__: object) -> None:
        return None

    async def jobs(*_: object, **__: object) -> list[object]:
        return [job]

    service = GoogleHealthService(object())  # type: ignore[arg-type]
    service.repository = SimpleNamespace(jobs_for_connection=jobs)  # type: ignore[assignment]
    monkeypatch.setattr(google_health_service, "seed_sync_jobs", seed)
    monkeypatch.setattr(
        google_health_service.sync_google_health_type,
        "delay",
        lambda *args: queued.append(args),
    )

    payload = SyncRequest.model_validate(
        {
            "dataTypes": ["sleep"],
            "startAt": "2026-07-01T00:00:00Z",
            "endAt": "2026-07-02T00:00:00Z",
        }
    )
    selected = await service._queue_manual_sync(connection, payload)

    assert selected == ["sleep"]
    assert queued == [
        (
            str(connection.id),
            "sleep",
            "manual",
            "2026-07-01T00:00:00+00:00",
            "2026-07-02T00:00:00+00:00",
        )
    ]


@pytest.mark.asyncio
async def test_manual_sync_rejects_disabled_type(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = SimpleNamespace(id=uuid4())

    async def seed(*_: object, **__: object) -> None:
        return None

    async def jobs(*_: object, **__: object) -> list[object]:
        return [SimpleNamespace(data_type="sleep", enabled=False)]

    service = GoogleHealthService(object())  # type: ignore[arg-type]
    service.repository = SimpleNamespace(jobs_for_connection=jobs)  # type: ignore[assignment]
    monkeypatch.setattr(google_health_service, "seed_sync_jobs", seed)

    with pytest.raises(ConflictError):
        await service._queue_manual_sync(
            connection,
            SyncRequest(data_types=["sleep"]),
        )


@pytest.mark.asyncio
async def test_disconnect_clears_tokens_disables_jobs_and_retains_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(
        id=uuid4(),
        access_token_ciphertext="encrypted",
        refresh_token_ciphertext="encrypted",
        token_expires_at=datetime.now(UTC),
        status="active",
    )
    job = SimpleNamespace(
        enabled=True,
        lease_until=datetime.now(UTC),
        next_page_token="cursor",
        status="running",
        error=None,
    )

    async def connection_for_user(*_: object, **__: object) -> object:
        return connection

    async def jobs(*_: object, **__: object) -> list[object]:
        return [job]

    class Database:
        committed = False

        async def commit(self) -> None:
            self.committed = True

    class OAuth:
        def __init__(self, *_: object) -> None:
            pass

        async def revoke_token(self, _: object) -> None:
            pass

    db = Database()
    service = GoogleHealthService(db, object())  # type: ignore[arg-type]
    service.repository = SimpleNamespace(  # type: ignore[assignment]
        connection_for_user=connection_for_user,
        jobs_for_connection=jobs,
    )
    monkeypatch.setattr(google_health_service, "OAuthService", OAuth)

    response = await service.disconnect(1)

    assert response == {"status": "revoked", "cacheRetained": True}
    assert connection.access_token_ciphertext == ""
    assert connection.refresh_token_ciphertext is None
    assert connection.status == "revoked"
    assert job.enabled is False
    assert job.error == "connection_disconnected"
    assert db.committed is True
