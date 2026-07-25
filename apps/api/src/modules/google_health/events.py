import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from redis.asyncio import Redis

SYNC_COMPLETED_EVENT = "sync-completed"
HEARTBEAT_SECONDS = 15.0


def _channel(user_id: int) -> str:
    return f"lifestats:google-health:sync-events:{user_id}"


async def publish_sync_completed(
    redis_url: str,
    *,
    user_id: int,
    data_type: str,
    record_count: int,
    trigger: str,
) -> None:
    client = Redis.from_url(redis_url, decode_responses=True)
    try:
        await client.publish(
            _channel(user_id),
            json.dumps(
                {
                    "dataType": data_type,
                    "recordCount": record_count,
                    "trigger": trigger,
                },
                separators=(",", ":"),
            ),
        )
    finally:
        await client.aclose()


async def stream_sync_events(
    redis_url: str,
    *,
    user_id: int,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[str]:
    client = Redis.from_url(redis_url, decode_responses=True)
    pubsub = client.pubsub()
    channel = _channel(user_id)
    await pubsub.subscribe(channel)
    try:
        yield "event: ready\ndata: {}\n\n"
        while not await is_disconnected():
            message: dict[str, Any] | None = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=HEARTBEAT_SECONDS,
            )
            if message is None:
                yield ": keep-alive\n\n"
                continue
            payload = message.get("data")
            if isinstance(payload, str):
                yield f"event: {SYNC_COMPLETED_EVENT}\ndata: {payload}\n\n"
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await client.aclose()
