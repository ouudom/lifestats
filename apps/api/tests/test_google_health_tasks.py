import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from src.modules.google_health import tasks
from src.modules.google_health.sync import SyncOutcome


def test_task_runner_disposes_engine_in_each_task_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_loops: list[asyncio.AbstractEventLoop] = []
    dispose_loops: list[asyncio.AbstractEventLoop] = []

    async def operation() -> str:
        task_loops.append(asyncio.get_running_loop())
        return "completed"

    async def dispose() -> None:
        dispose_loops.append(asyncio.get_running_loop())

    monkeypatch.setattr(tasks, "engine", SimpleNamespace(dispose=dispose))

    assert tasks._run_async_task(operation()) == "completed"
    assert tasks._run_async_task(operation()) == "completed"
    assert task_loops == dispose_loops
    assert task_loops[0] is not task_loops[1]


def test_task_runner_disposes_engine_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispose = AsyncMock()
    monkeypatch.setattr(tasks, "engine", SimpleNamespace(dispose=dispose))

    async def operation() -> None:
        raise RuntimeError("sync failed")

    with pytest.raises(RuntimeError, match="sync failed"):
        tasks._run_async_task(operation())

    dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_sync_publishes_user_scoped_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection_id = uuid4()
    service = SimpleNamespace(
        sync_type=AsyncMock(
            return_value=SyncOutcome(
                user_id=42,
                data_type="hydration-log",
                record_count=2,
                status="completed",
            )
        )
    )
    publisher = AsyncMock()

    class SessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(tasks, "SessionFactory", SessionContext)
    monkeypatch.setattr(tasks, "SyncService", lambda *_args, **_kwargs: service)
    monkeypatch.setattr(tasks, "publish_sync_completed", publisher)

    await tasks._sync_google_health_type(
        connection_id,
        "hydration-log",
        "webhook",
        None,
        None,
    )

    publisher.assert_awaited_once_with(
        tasks.settings.redis_url,
        user_id=42,
        data_type="hydration-log",
        record_count=2,
        trigger="webhook",
    )
