from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from datetime import date
from urllib.parse import parse_qs
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.core.config import get_settings
from src.core.database import SessionFactory
from src.core.errors import AuthenticationError
from src.modules.agent.schemas import (
    Capabilities,
    ConnectionStart,
    ConnectionStatus,
    DateRange,
    DisconnectResult,
    ExerciseExport,
    FreshnessReport,
    HealthRecord,
    Profile,
    RecordPage,
    RecordQuery,
    Summary,
    SyncCommand,
    SyncItem,
    SyncQueued,
    SyncStatus,
    Timeline,
    Today,
    Trend,
    TrendQuery,
)
from src.modules.agent.service import AgentService
from src.modules.agent_access.schemas import AgentScope
from src.modules.agent_access.service import AgentPrincipal, McpTokenService
from src.modules.google_health.registry import (
    ACTIVITY_SCOPE,
    DATA_TYPE_REGISTRY,
    ECG_SCOPE,
    HEALTH_SCOPE,
    IRN_SCOPE,
    NUTRITION_SCOPE,
    SLEEP_SCOPE,
)

_principal: ContextVar[AgentPrincipal | None] = ContextVar("mcp_principal", default=None)
MCP_EXCLUDED_DATA_TYPES = frozenset({"swim-lengths-data"})
mcp = FastMCP(
    "LifeStats",
    instructions=(
        "Private, user-scoped Google Health companion. Results are synced projections; "
        "their source, freshness, availability, and derivation fields must be preserved."
    ),
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["mcp:*", "localhost:*", "127.0.0.1:*", "[::1]:*"],
        allowed_origins=[],
    ),
)


def _current_principal() -> AgentPrincipal:
    principal = _principal.get()
    if principal is None:
        raise ToolError("Agent authentication context is unavailable")
    return principal


def _require_scope(principal: AgentPrincipal, scope: AgentScope) -> None:
    if not principal.has_scope(scope):
        raise ToolError(f"MCP token lacks required scope: {scope.value}")


async def _run[T](
    scope: AgentScope,
    operation: Callable[[AgentService, int], Awaitable[T]],
) -> T:
    principal = _current_principal()
    _require_scope(principal, scope)
    async with SessionFactory() as db:
        service = AgentService(db, get_settings())
        return await operation(service, principal.user_id)


def _require_query_scopes(principal: AgentPrincipal, data_types: list[str] | None) -> None:
    selected = _mcp_data_types(data_types)
    unknown = set(selected) - DATA_TYPE_REGISTRY.keys()
    if unknown:
        raise ToolError(f"Unsupported Google Health data type: {', '.join(sorted(unknown))}")
    scope_map = {
        ACTIVITY_SCOPE: AgentScope.FITNESS_READ,
        SLEEP_SCOPE: AgentScope.SLEEP_READ,
        HEALTH_SCOPE: AgentScope.HEALTH_READ,
        NUTRITION_SCOPE: AgentScope.NUTRITION_READ,
        ECG_SCOPE: AgentScope.ECG_READ,
        IRN_SCOPE: AgentScope.IRN_READ,
    }
    for data_type in selected:
        required = scope_map[DATA_TYPE_REGISTRY[data_type].scope]
        _require_scope(principal, required)


def _mcp_data_types(data_types: list[str] | None) -> list[str]:
    if data_types is None:
        return [
            data_type
            for data_type in DATA_TYPE_REGISTRY
            if data_type not in MCP_EXCLUDED_DATA_TYPES
        ]
    excluded = set(data_types) & MCP_EXCLUDED_DATA_TYPES
    if excluded:
        detail = ", ".join(sorted(excluded))
        raise ToolError(f"Google Health data type is not exposed through MCP: {detail}")
    return data_types


def _filter_capabilities(capabilities: Capabilities) -> Capabilities:
    return capabilities.model_copy(
        update={
            "items": [
                item for item in capabilities.items if item.data_type not in MCP_EXCLUDED_DATA_TYPES
            ]
        }
    )


def _filter_freshness(report: FreshnessReport) -> FreshnessReport:
    return report.model_copy(
        update={
            "items": [
                item for item in report.items if item.data_type not in MCP_EXCLUDED_DATA_TYPES
            ]
        }
    )


def _filter_summary(summary: Summary) -> Summary:
    return summary.model_copy(
        update={
            "metrics": [
                metric
                for metric in summary.metrics
                if metric.data_type not in MCP_EXCLUDED_DATA_TYPES
            ]
        }
    )


def _filter_today(today: Today) -> Today:
    return today.model_copy(
        update={
            "sync": [item for item in today.sync if item.data_type not in MCP_EXCLUDED_DATA_TYPES],
        }
    )


def _filter_sync_status(status: SyncStatus) -> SyncStatus:
    return status.model_copy(
        update={
            "items": [
                item for item in status.items if item.data_type not in MCP_EXCLUDED_DATA_TYPES
            ],
        }
    )


@mcp.custom_route(  # type: ignore[untyped-decorator]
    "/healthz", methods=["GET"], include_in_schema=False
)
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.tool()
async def get_profile() -> Profile:
    """Return the authenticated user's display name and timezone."""
    return await _run(AgentScope.PROFILE_READ, lambda service, user: service.get_profile(user))


@mcp.tool()
async def list_capabilities() -> Capabilities:
    """List Google Health data types, grants, availability, and supported operations."""
    return _filter_capabilities(
        await _run(AgentScope.PROFILE_READ, lambda service, user: service.list_capabilities(user))
    )


@mcp.tool()
async def get_google_health_status() -> ConnectionStatus:
    """Return Google Health connection and grant status."""
    return await _run(
        AgentScope.INTEGRATION_READ,
        lambda service, user: service.get_google_health_status(user),
    )


@mcp.tool()
async def get_connection_status() -> ConnectionStatus:
    """Return Google Health connection status for connection-management flows."""
    return await _run(
        AgentScope.INTEGRATION_READ,
        lambda service, user: service.get_connection_status(user),
    )


@mcp.tool()
async def start_google_health_connection() -> ConnectionStart:
    """Create a browser authorization URL for the authenticated user."""
    return await _run(
        AgentScope.INTEGRATION_WRITE,
        lambda service, user: service.start_google_health_connection(user),
    )


@mcp.tool()
async def disconnect_google_health(confirm: bool = False) -> DisconnectResult:
    """Revoke Google Health after explicit confirmation; retain the synced cache."""
    if not confirm:
        raise ToolError("Set confirm=true to disconnect Google Health")
    return await _run(
        AgentScope.INTEGRATION_WRITE,
        lambda service, user: service.disconnect_google_health(user),
    )


@mcp.tool()
async def get_data_freshness() -> FreshnessReport:
    """Return freshness and record counts for every supported Google Health type."""
    return _filter_freshness(
        await _run(AgentScope.SYNC_READ, lambda service, user: service.get_data_freshness(user))
    )


@mcp.tool()
async def get_today(day: date | None = None) -> Today:
    """Return Google Health-backed Today data for one local calendar date."""
    return _filter_today(
        await _run(AgentScope.TODAY_READ, lambda service, user: service.get_today(user, day))
    )


@mcp.tool()
async def get_timeline(day: date | None = None) -> Timeline:
    """Return the authenticated user's timeline for one local calendar date."""
    return await _run(AgentScope.TODAY_READ, lambda service, user: service.get_timeline(user, day))


@mcp.tool()
async def get_fitness_summary(request: DateRange) -> Summary:
    """Summarize synced fitness data over a date range."""
    return _filter_summary(
        await _run(
            AgentScope.FITNESS_READ,
            lambda service, user: service.get_fitness_summary(user, request),
        )
    )


@mcp.tool()
async def list_exercises(request: RecordQuery) -> RecordPage:
    """List paginated exercise records over a date range."""
    return await _run(
        AgentScope.FITNESS_READ, lambda service, user: service.list_exercises(user, request)
    )


@mcp.tool()
async def get_exercise(record_id: UUID) -> HealthRecord:
    """Return one exercise belonging to the authenticated user."""
    return await _run(
        AgentScope.FITNESS_READ, lambda service, user: service.get_exercise(user, record_id)
    )


@mcp.tool()
async def get_activity_trend(request: TrendQuery) -> Trend:
    """Return a fitness data-type trend over a date range."""
    _mcp_data_types([request.data_type])
    return await _run(
        AgentScope.FITNESS_READ,
        lambda service, user: service.get_activity_trend(user, request),
    )


@mcp.tool()
async def get_heart_rate_zones(request: DateRange) -> Summary:
    """Summarize synced heart-rate-zone records over a date range."""
    return await _run(
        AgentScope.FITNESS_READ,
        lambda service, user: service.get_heart_rate_zones(user, request),
    )


@mcp.tool()
async def get_exercise_export(record_id: UUID) -> ExerciseExport:
    """Return a TCX exercise export when Google Health supplied one."""
    return await _run(
        AgentScope.FITNESS_READ,
        lambda service, user: service.get_exercise_export(user, record_id),
    )


@mcp.tool()
async def get_sleep_summary(request: DateRange) -> Summary:
    """Summarize synced sleep data without inventing an official score."""
    return await _run(
        AgentScope.SLEEP_READ,
        lambda service, user: service.get_sleep_summary(user, request),
    )


@mcp.tool()
async def list_sleep_sessions(request: RecordQuery) -> RecordPage:
    """List paginated sleep sessions over a date range."""
    return await _run(
        AgentScope.SLEEP_READ,
        lambda service, user: service.list_sleep_sessions(user, request),
    )


@mcp.tool()
async def get_sleep_session(record_id: UUID) -> HealthRecord:
    """Return one sleep session belonging to the authenticated user."""
    return await _run(
        AgentScope.SLEEP_READ,
        lambda service, user: service.get_sleep_session(user, record_id),
    )


@mcp.tool()
async def get_sleep_trend(request: DateRange) -> Trend:
    """Return sleep record trends over a date range."""
    return await _run(
        AgentScope.SLEEP_READ,
        lambda service, user: service.get_sleep_trend(user, request),
    )


@mcp.tool()
async def get_health_summary(request: DateRange) -> Summary:
    """Summarize non-sensitive health measurements over a date range."""
    return await _run(
        AgentScope.HEALTH_READ,
        lambda service, user: service.get_health_summary(user, request),
    )


@mcp.tool()
async def get_measurement_latest(data_type: str) -> HealthRecord:
    """Return the latest synced record for a non-sensitive measurement type."""
    return await _run(
        AgentScope.HEALTH_READ,
        lambda service, user: service.get_measurement_latest(user, data_type),
    )


@mcp.tool()
async def get_measurement_trend(request: TrendQuery) -> Trend:
    """Return a non-sensitive health measurement trend."""
    return await _run(
        AgentScope.HEALTH_READ,
        lambda service, user: service.get_measurement_trend(user, request),
    )


@mcp.tool()
async def query_health_data(request: RecordQuery) -> RecordPage:
    """Query synced types allowed by the token's category and sensitive-data scopes."""
    principal = _current_principal()
    selected = _mcp_data_types(request.data_types)
    _require_query_scopes(principal, selected)
    effective_request = request.model_copy(update={"data_types": selected})
    async with SessionFactory() as db:
        return await AgentService(db, get_settings()).query_health_data(
            principal.user_id, effective_request
        )


@mcp.tool()
async def list_irregular_rhythm_notifications(request: RecordQuery) -> RecordPage:
    """List irregular-rhythm notifications with the explicit IRN scope."""
    return await _run(
        AgentScope.IRN_READ,
        lambda service, user: service.list_irregular_rhythm_notifications(user, request),
    )


@mcp.tool()
async def list_electrocardiograms(request: RecordQuery) -> RecordPage:
    """List electrocardiograms with the explicit ECG scope."""
    return await _run(
        AgentScope.ECG_READ,
        lambda service, user: service.list_electrocardiograms(user, request),
    )


@mcp.tool()
async def get_electrocardiogram(record_id: UUID) -> HealthRecord:
    """Return one electrocardiogram belonging to the authenticated user."""
    return await _run(
        AgentScope.ECG_READ,
        lambda service, user: service.get_electrocardiogram(user, record_id),
    )


@mcp.tool()
async def get_nutrition_summary(request: DateRange) -> Summary:
    """Summarize synced nutrition data over a date range."""
    return await _run(
        AgentScope.NUTRITION_READ,
        lambda service, user: service.get_nutrition_summary(user, request),
    )


@mcp.tool()
async def list_nutrition_logs(request: RecordQuery) -> RecordPage:
    """List paginated nutrition logs over a date range."""
    return await _run(
        AgentScope.NUTRITION_READ,
        lambda service, user: service.list_nutrition_logs(user, request),
    )


@mcp.tool()
async def list_hydration_logs(request: RecordQuery) -> RecordPage:
    """List paginated hydration logs over a date range."""
    return await _run(
        AgentScope.NUTRITION_READ,
        lambda service, user: service.list_hydration_logs(user, request),
    )


@mcp.tool()
async def get_body_measurements(request: RecordQuery) -> RecordPage:
    """List body-fat, height, and weight records over a date range."""
    return await _run(
        AgentScope.HEALTH_READ,
        lambda service, user: service.get_body_measurements(user, request),
    )


@mcp.tool()
async def get_sync_status() -> SyncStatus:
    """Return synchronization status for all Google Health data types."""
    return _filter_sync_status(
        await _run(AgentScope.SYNC_READ, lambda service, user: service.get_sync_status(user))
    )


@mcp.tool()
async def get_data_type_sync_status(data_type: str) -> SyncItem:
    """Return synchronization status for one Google Health data type."""
    _mcp_data_types([data_type])
    return await _run(
        AgentScope.SYNC_READ,
        lambda service, user: service.get_data_type_sync_status(user, data_type),
    )


@mcp.tool()
async def trigger_sync(command: SyncCommand) -> SyncQueued:
    """Queue a bounded Google Health synchronization for the authenticated user."""
    if command.data_types is not None:
        _mcp_data_types(command.data_types)
    result = await _run(
        AgentScope.SYNC_WRITE,
        lambda service, user: service.trigger_sync(user, command),
    )
    return result.model_copy(
        update={
            "data_types": [
                data_type
                for data_type in result.data_types
                if data_type not in MCP_EXCLUDED_DATA_TYPES
            ]
        }
    )


class QueryTokenAuthMiddleware:
    """Authenticate MCP requests from one token query parameter."""

    def __init__(self, application: ASGIApp) -> None:
        self.application = application

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http" or scope.get("path") == "/healthz":
            await self.application(scope, receive, send)
            return
        raw_token = _query_token(scope)
        if raw_token is None:
            await _unauthorized(scope, receive, send)
            return
        try:
            async with SessionFactory() as db:
                principal = await McpTokenService(db).authenticate(raw_token)
        except AuthenticationError:
            await _unauthorized(scope, receive, send)
            return
        context_token: Token[AgentPrincipal | None] = _principal.set(principal)
        try:
            sanitized_scope = dict(scope)
            sanitized_scope["query_string"] = b""
            await self.application(sanitized_scope, receive, send)
        finally:
            _principal.reset(context_token)


def _query_token(scope: Scope) -> str | None:
    if any(name.lower() == b"authorization" for name, _value in scope.get("headers", [])):
        return None
    try:
        query = parse_qs(
            scope.get("query_string", b"").decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2,
        )
    except (UnicodeDecodeError, ValueError):
        return None
    values = query.get("token")
    if set(query) != {"token"} or values is None or len(values) != 1:
        return None
    token = str(values[0])
    if not token or len(token) > 256 or token.strip() != token:
        return None
    return token


async def _unauthorized(
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    response = JSONResponse(
        {"detail": "Invalid or missing MCP token"},
        status_code=401,
        headers={"Cache-Control": "no-store"},
    )
    await response(scope, receive, send)


app = QueryTokenAuthMiddleware(mcp.streamable_http_app())
