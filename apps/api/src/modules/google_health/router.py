from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.dependencies import database_session
from src.core.errors import ConflictError, NotFoundError
from src.modules.auth.dependencies import (
    Principal,
    current_principal,
    require_csrf,
)
from src.modules.google_health.dependencies import (
    get_google_health_repository,
    get_google_health_service,
)
from src.modules.google_health.events import stream_sync_events
from src.modules.google_health.oauth import OAuthService
from src.modules.google_health.repository import GoogleHealthRepository
from src.modules.google_health.schemas import SyncRequest
from src.modules.google_health.service import GoogleHealthService
from src.modules.google_health.tasks import process_google_health_webhook
from src.modules.google_health.webhooks import (
    SIGNATURE_HEADER,
    GoogleHealthSignatureVerifier,
    WebhookNotification,
    WebhookSignatureError,
    authorization_matches,
    is_verification_request,
)

router = APIRouter(tags=["google-health"])


@lru_cache
def _signature_verifier(keyset_url: str, ttl_seconds: int) -> GoogleHealthSignatureVerifier:
    return GoogleHealthSignatureVerifier(keyset_url, ttl_seconds=ttl_seconds)


def get_signature_verifier(
    settings: Annotated[Settings, Depends(get_settings)],
) -> GoogleHealthSignatureVerifier:
    return _signature_verifier(
        settings.google_health_webhook_keyset_url,
        settings.google_health_webhook_keyset_ttl_seconds,
    )


def translate_service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.get("/integrations/google-health/connect")
async def connect(
    principal: Annotated[Principal, Depends(current_principal)],
    db: AsyncSession = Depends(database_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    url = await OAuthService(db, settings).authorization_url(principal.user_id)
    return RedirectResponse(url)


@router.get("/oauth/google-health/callback")
async def callback(
    state: str = Query(min_length=1),
    code: str = Query(min_length=1),
    db: AsyncSession = Depends(database_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    try:
        await OAuthService(db, settings).callback(state, code)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return RedirectResponse("/?connected=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/integrations/google-health")
async def integration_status(
    principal: Annotated[Principal, Depends(current_principal)],
    service: Annotated[GoogleHealthService, Depends(get_google_health_service)],
) -> dict[str, object]:
    return await service.integration_status(principal.user_id)


@router.post("/integrations/google-health/disconnect")
async def disconnect(
    principal: Annotated[Principal, Depends(require_csrf)],
    service: Annotated[GoogleHealthService, Depends(get_google_health_service)],
) -> dict[str, object]:
    try:
        return await service.disconnect(principal.user_id)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Google token revocation failed; connection retained for retry",
        ) from exc


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
async def queue_sync(
    payload: SyncRequest,
    principal: Annotated[Principal, Depends(require_csrf)],
    service: Annotated[GoogleHealthService, Depends(get_google_health_service)],
) -> dict[str, object]:
    try:
        selected = await service.queue_sync(principal.user_id, payload)
    except (ConflictError, NotFoundError) as exc:
        raise translate_service_error(exc) from exc
    return {"status": "queued", "dataTypes": selected}


@router.get("/sync")
async def list_sync_jobs(
    principal: Annotated[Principal, Depends(current_principal)],
    service: Annotated[GoogleHealthService, Depends(get_google_health_service)],
) -> dict[str, object]:
    try:
        return await service.sync_jobs(principal.user_id)
    except NotFoundError as exc:
        raise translate_service_error(exc) from exc


@router.get("/sync/events", response_class=StreamingResponse, include_in_schema=False)
async def sync_events(
    request: Request,
    principal: Annotated[Principal, Depends(current_principal)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    return StreamingResponse(
        stream_sync_events(
            settings.redis_url,
            user_id=principal.user_id,
            is_disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/sync/{data_type}")
async def sync_status(
    data_type: str,
    principal: Annotated[Principal, Depends(current_principal)],
    service: Annotated[GoogleHealthService, Depends(get_google_health_service)],
) -> dict[str, object]:
    try:
        return await service.sync_status(principal.user_id, data_type)
    except NotFoundError as exc:
        raise translate_service_error(exc) from exc


@router.post("/sync/{data_type}", status_code=status.HTTP_202_ACCEPTED)
async def queue_data_type_sync(
    data_type: str,
    principal: Annotated[Principal, Depends(require_csrf)],
    service: Annotated[GoogleHealthService, Depends(get_google_health_service)],
    payload: SyncRequest = Body(default_factory=SyncRequest),
) -> dict[str, str]:
    try:
        await service.queue_sync(
            principal.user_id,
            payload,
            requested_type=data_type,
        )
    except (ConflictError, NotFoundError) as exc:
        raise translate_service_error(exc) from exc
    return {"status": "queued", "dataType": data_type}


@router.post(
    "/webhooks/google-health",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={200: {"description": "Endpoint verification accepted"}},
)
async def google_health_webhook(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    repository: Annotated[
        GoogleHealthRepository,
        Depends(get_google_health_repository),
    ],
    verifier: Annotated[GoogleHealthSignatureVerifier, Depends(get_signature_verifier)],
    authorization: Annotated[str | None, Header()] = None,
    google_health_api_signature: Annotated[str | None, Header(alias=SIGNATURE_HEADER)] = None,
) -> Response:
    if not settings.google_health_webhook_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    raw_body = await request.body()
    authorized = authorization_matches(authorization, settings.google_health_webhook_auth_secret)
    if is_verification_request(raw_body):
        if not authorized:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)
        return Response(status_code=status.HTTP_200_OK)

    if not authorized:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    if not google_health_api_signature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing webhook signature")
    try:
        await verifier.verify(raw_body, google_health_api_signature)
        notification = WebhookNotification.parse(raw_body)
    except WebhookSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    event_id = await repository.store_webhook_event(notification)
    if event_id is not None:
        process_google_health_webhook.delay(str(event_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
