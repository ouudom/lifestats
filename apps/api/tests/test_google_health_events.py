import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from src.modules.google_health import events


@pytest.mark.asyncio
async def test_publish_sync_completed_uses_user_scoped_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(publish=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(events.Redis, "from_url", lambda *_args, **_kwargs: client)

    await events.publish_sync_completed(
        "redis://example",
        user_id=42,
        data_type="hydration-log",
        record_count=2,
        trigger="webhook",
    )

    client.publish.assert_awaited_once()
    channel, raw_payload = client.publish.await_args.args
    assert channel == "lifestats:google-health:sync-events:42"
    assert json.loads(raw_payload) == {
        "dataType": "hydration-log",
        "recordCount": 2,
        "trigger": "webhook",
    }
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_emits_named_event_and_closes_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pubsub = SimpleNamespace(
        subscribe=AsyncMock(),
        get_message=AsyncMock(return_value={"data": '{"dataType":"hydration-log"}'}),
        unsubscribe=AsyncMock(),
        aclose=AsyncMock(),
    )
    client = SimpleNamespace(pubsub=lambda: pubsub, aclose=AsyncMock())
    monkeypatch.setattr(events.Redis, "from_url", lambda *_args, **_kwargs: client)
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))

    stream = events.stream_sync_events(
        "redis://example",
        user_id=42,
        is_disconnected=request.is_disconnected,
    )
    assert await anext(stream) == "event: ready\ndata: {}\n\n"
    assert await anext(stream) == ('event: sync-completed\ndata: {"dataType":"hydration-log"}\n\n')
    await stream.aclose()

    pubsub.subscribe.assert_awaited_once_with("lifestats:google-health:sync-events:42")
    pubsub.unsubscribe.assert_awaited_once_with("lifestats:google-health:sync-events:42")
    pubsub.aclose.assert_awaited_once()
    client.aclose.assert_awaited_once()
