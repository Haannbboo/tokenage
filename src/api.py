import asyncio
import json
import logging
import re
import shlex
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from protocol import MAX_SUPPORTED_GENERATION, MIN_SUPPORTED_GENERATION
from protocol.device_status import DeviceStatusReport
from protocol.evaluation import EvaluationCompletedReport, EvaluationStartedReport
from protocol.usage_report import UsageReport
from src.config.app import CONFIG, _config_lock
from src.config.server_config import resolve_server_urls

from ._version import get_commit, get_version
from .auth import (
    _resolve_request_user,
    get_current_user,
)
from .auth import router as auth_router
from .auth.tokens import list_user_devices, set_device_status
from .database import (
    VALID_OUTCOMES,
    VALID_SOURCES,
    Device,
    User,
    aggregate_daily_by_dimension,
    aggregate_model_effectiveness,
    aggregate_usage_by_period,
    count_sessions,
    count_usage,
    daily_session_effectiveness_report,
    delete_model_price,
    delete_session_evaluation,
    distinct_client_sources,
    distinct_tool_names,
    fetch_recent_usage,
    fetch_session_selector_rows,
    fetch_sessions,
    fetch_tool_calls,
    get_evaluation_job,
    get_session_evaluation,
    get_usage_high_watermark_ts,
    init_db,
    list_active_evaluation_jobs,
    list_model_prices,
    list_session_evaluation_jobs,
    load_model_price_overrides,
    recalculate_usage_cost,
    record_evaluation_completed,
    record_evaluation_started,
    reprice_estimated_rows,
    summarize_session_tool_calls,
    summarize_sessions,
    summarize_tool_calls,
    summarize_usage_by_model,
    summarize_usage_by_provider,
    summarize_usage_by_source,
    summarize_usage_window,
    upsert_model_price,
    upsert_session_evaluation,
)
from .ingest_queue import (
    TRANSIENT_DB_ERRORS,
    IngestQueueFull,
    consumer_lifespan,
    publish,
    queue_full_response,
    store_unavailable_response,
)
from .metrics import MetricsMiddleware
from .metrics import configure as configure_metrics
from .otlp import app as otlp_app
from .pricing.costs import resolve_cost_match
from .pricing.models import ResolvedCost
from .pricing.snapshots import enrich_rows
from .ratelimit import rate_limit_hit, resolve_ingest_request
from .utils import normalize_model_name

logger = logging.getLogger(__name__)
USAGE_QUERY_LIMIT_MAX = 1000
LOCAL_CORS_ORIGIN_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")

AUTH_GATE_LOGIN_REQUIRED = {"detail": "login required"}

# API path prefixes. Requests under these prefixes are never rewritten to the
# SPA index, and they are the surface the auth gate classifies.
_SPA_API_PREFIXES = (
    "/auth/",
    "/usage",
    "/sessions",
    "/devices",
    "/evaluation-jobs",
    "/model-effectiveness",
    "/pricing",
    "/version",
    "/install.sh",
    "/ingest/",
    "/otlp/",
)

# Public: sign-in endpoints, /auth/me (the frontend probes it before login),
# and /version. Everything else API-shaped requires a resolved user.
AUTH_GATE_PUBLIC_PATHS = ("/auth/me", "/version", "/install.sh")

# FastAPI's interactive docs and schema are not in the public allowlist, so
# they are gated like any other API surface.
AUTH_GATE_EXTRA_GATED_PATHS = ("/docs", "/redoc", "/openapi.json")


def _request_user_id(request: Request) -> str:
    return get_current_user(request).id


class SessionEvaluationUpdate(BaseModel):
    outcome: str
    source: str = "manual"
    confidence: float | None = None
    task_title: str | None = None
    task_title_zh: str | None = None
    summary: str | None = None
    evidence: list[str] = []
    failure_reason: str | None = None
    project: str | None = None


def _parse_device_status_json(raw: str | None) -> dict | None:
    if raw is None:
        return None
    try:
        status = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return status if isinstance(status, dict) else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from .pricing.refresh import pricing_refresh_lifespan

    async with consumer_lifespan(), pricing_refresh_lifespan():
        yield


app = FastAPI(title="tokenage-api", lifespan=lifespan)
app.exception_handler(IngestQueueFull)(queue_full_response)
for error in TRANSIENT_DB_ERRORS:
    app.exception_handler(error)(store_unavailable_response)
app.include_router(auth_router)
# One process, one port: the collector keeps its own app (still runnable as
# `uvicorn src.otlp:app`). Starlette skips a mounted app's lifespan; ours runs
# init_db() and the shared ingest consumer.
app.mount("/otlp", otlp_app)


def _is_public_path(path: str) -> bool:
    """Public: sign-in endpoints, /auth/me, /version, SPA."""
    if not any(path.startswith(prefix) for prefix in _SPA_API_PREFIXES):
        return path not in AUTH_GATE_EXTRA_GATED_PATHS
    if path.startswith(("/ingest/", "/otlp/")):
        # Ingest routes and the mounted OTLP collector authenticate themselves:
        # ingest tokens only, which the user gate (cli/web tokens, loopback
        # owner) deliberately rejects, and they count against the ingest limit.
        return True
    if path.startswith(("/auth/google/", "/auth/cli/", "/auth/local/")):
        # CLI login endpoints are the credential-issuing surface: the
        # one-time code + PKCE verifier (or the Google flow itself) are the
        # credentials.
        return True
    return path in AUTH_GATE_PUBLIC_PATHS


class AuthGateMiddleware(BaseHTTPMiddleware):
    """Require a resolved user (token, or the local owner on direct loopback)
    for all API routes except the public allowlist. Data routes pass the user
    ID to database queries for per-user scoping."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            # CORS preflights carry no credentials (browsers never attach
            # them), so they can never authenticate. Let them through so the
            # usage_read_cors middleware can answer them; OPTIONS never
            # reaches route handlers with data.
            return await call_next(request)
        if _is_public_path(request.url.path):
            return await call_next(request)
        # Token lookup hits the database: keep it off the event loop, which the
        # mounted OTLP collector shares.
        try:
            resolved = await asyncio.to_thread(_resolve_request_user, request)
        except TRANSIENT_DB_ERRORS as exc:
            # Middleware runs outside FastAPI's route exception handlers.
            return store_unavailable_response(request, exc)
        if resolved is None:
            return JSONResponse(status_code=401, content=AUTH_GATE_LOGIN_REQUIRED)
        user, auth_token = resolved
        if auth_token is not None:  # the loopback owner (no token) is exempt
            try:
                retry_after = await asyncio.to_thread(
                    rate_limit_hit, f"api:{user.id}", "api_per_minute"
                )
            except TRANSIENT_DB_ERRORS as exc:
                # Middleware runs outside FastAPI's route exception handlers.
                return store_unavailable_response(request, exc)
            if retry_after is not None:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "rate limit exceeded"},
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)


def _is_usage_read_path(path: str) -> bool:
    return path == "/usage" or path.startswith("/usage/")


def _add_usage_cors_headers(response: Response, origin: str) -> Response:
    response.headers["Access-Control-Allow-Origin"] = origin
    vary = response.headers.get("Vary")
    if vary:
        vary_tokens = [token.strip() for token in vary.split(",") if token.strip()]
        if "Origin" not in vary_tokens:
            vary_tokens.append("Origin")
        response.headers["Vary"] = ", ".join(vary_tokens)
    else:
        response.headers["Vary"] = "Origin"
    return response


@app.middleware("http")
async def usage_read_cors(request: Request, call_next):
    origin = request.headers.get("origin")
    allow_origin = origin is not None and LOCAL_CORS_ORIGIN_RE.fullmatch(origin)
    if not allow_origin or not _is_usage_read_path(request.url.path):
        return await call_next(request)

    if request.method == "OPTIONS":
        requested_method = request.headers.get("access-control-request-method", "")
        if requested_method.upper() == "GET":
            response = Response(status_code=204)
            _add_usage_cors_headers(response, origin)  # type: ignore[arg-type]
            response.headers["Access-Control-Allow-Methods"] = "GET"
            response.headers["Access-Control-Allow-Headers"] = request.headers.get(
                "access-control-request-headers",
                "",
            )
            return response
        return await call_next(request)

    response = await call_next(request)
    if request.method == "GET":
        _add_usage_cors_headers(response, origin)  # type: ignore[arg-type]
    return response


@app.get("/usage")
async def get_usage(
    request: Request,
    limit: int = Query(100, ge=0, le=USAGE_QUERY_LIMIT_MAX),
    offset: int = Query(0, ge=0),
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    session_id: str | None = None,
    tool_name: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    user_id = _request_user_id(request)
    return await asyncio.to_thread(
        lambda: enrich_rows(
            fetch_recent_usage(
                user_id=user_id,
                limit=limit,
                offset=offset,
                provider=provider,
                model=model,
                client_source=client_source,
                device_id=device_id,
                session_id=session_id,
                tool_name=tool_name,
                since=since,
                until=until,
            )
        )
    )


@app.get("/usage/count")
def get_usage_count(
    request: Request,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    session_id: str | None = None,
    tool_name: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    return {
        "total": count_usage(
            user_id=_request_user_id(request),
            provider=provider,
            model=model,
            client_source=client_source,
            device_id=device_id,
            session_id=session_id,
            tool_name=tool_name,
            since=since,
            until=until,
        )
    }


@app.get("/usage/high-watermark")
def usage_high_watermark(request: Request):
    return {"ts": get_usage_high_watermark_ts(user_id=_request_user_id(request))}


@app.get("/usage/sources")
def usage_sources(
    request: Request,
    since: str | None = None,
    until: str | None = None,
):
    return distinct_client_sources(
        user_id=_request_user_id(request), since=since, until=until
    )


@app.get("/usage/tools")
def usage_tools(
    request: Request,
    since: str | None = None,
    until: str | None = None,
):
    return distinct_tool_names(
        user_id=_request_user_id(request), since=since, until=until
    )


@app.get("/usage/run-summary")
def usage_run_summary(
    request: Request,
    after_ts: int = 0,
    until_ts: int | None = None,
    since: str | None = None,
    until: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    session_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    include_rows: bool = False,
):
    return summarize_usage_window(
        user_id=_request_user_id(request),
        after_ts=after_ts,
        until_ts=until_ts,
        since=since,
        until=until,
        client_source=client_source,
        device_id=device_id,
        session_id=session_id,
        provider=provider,
        model=model,
        include_rows=include_rows,
    )


@app.get("/usage/summary")
def usage_summary(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
):
    return summarize_usage_by_model(
        user_id=_request_user_id(request),
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
    )


@app.get("/usage/by-source")
def usage_by_source(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
):
    return summarize_usage_by_source(
        user_id=_request_user_id(request),
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
    )


@app.get("/usage/by-tool")
def usage_by_tool(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
):
    return summarize_tool_calls(
        user_id=_request_user_id(request),
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
    )


@app.get("/usage/by-provider")
def usage_by_provider(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
):
    return summarize_usage_by_provider(
        user_id=_request_user_id(request),
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
    )


@app.get("/usage/daily")
def usage_daily(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    granularity: str = "day",
    tz_offset: str = "+00:00",
):
    return aggregate_usage_by_period(
        user_id=_request_user_id(request),
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        granularity=granularity,
        tz_offset=tz_offset,
    )


@app.get("/usage/daily-by-dimension")
def usage_daily_by_dimension(
    request: Request,
    dimension: str = "model",
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    granularity: str = "day",
    tz_offset: str = "+00:00",
):
    return aggregate_daily_by_dimension(
        user_id=_request_user_id(request),
        dimension=dimension,
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        granularity=granularity,
        tz_offset=tz_offset,
    )


@app.get("/sessions")
def get_sessions(
    request: Request,
    client_source: str | None = None,
    since: str | None = None,
    until: str | None = None,
    view: str = "summary",
    sort_by: str = "ended",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    hide_noop: bool = False,
):
    user_id = _request_user_id(request)
    if view not in {"summary", "selector"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid view. Must be one of ['summary', 'selector']",
        )

    if view == "selector":
        return {
            "sessions": fetch_session_selector_rows(
                user_id=user_id,
                client_source=client_source,
                since=since,
                until=until,
                sort_by=sort_by,
                sort_order=sort_order,
                limit=limit,
                offset=offset,
                hide_noop=hide_noop,
            ),
            "total": None,
        }

    sessions = fetch_sessions(
        user_id=user_id,
        client_source=client_source,
        since=since,
        until=until,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
        hide_noop=hide_noop,
    )
    total = count_sessions(
        user_id=user_id,
        client_source=client_source,
        since=since,
        until=until,
        hide_noop=hide_noop,
    )
    return {"sessions": sessions, "total": total}


@app.get("/sessions/summary")
def get_sessions_summary(
    request: Request,
    client_source: str | None = None,
    since: str | None = None,
    until: str | None = None,
    hide_noop: bool = False,
):
    return summarize_sessions(
        user_id=_request_user_id(request),
        client_source=client_source,
        since=since,
        until=until,
        hide_noop=hide_noop,
    )


@app.get("/model-effectiveness")
def model_effectiveness(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    client_source: str | None = None,
    group_by: str = "model",
    hide_noop: bool = False,
):
    if group_by not in {"model", "source", "provider"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid group_by. Must be one of ['model', 'provider', 'source']",
        )
    return aggregate_model_effectiveness(
        user_id=_request_user_id(request),
        group_by=group_by,
        since=since,
        until=until,
        client_source=client_source,
        hide_noop=hide_noop,
    )


@app.get("/sessions/daily-effectiveness")
def sessions_daily_effectiveness(request: Request, date: str):
    try:
        return daily_session_effectiveness_report(
            date=date, user_id=_request_user_id(request)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _device_status_entry(device: Device) -> dict:
    """One device as the GET response entry, tolerating bad JSON."""
    status = _parse_device_status_json(device.status_json)
    return {
        "device_id": device.id,
        "device_name": device.device_name,
        "client_version": (status or {}).get("client_version"),
        "client_commit": (status or {}).get("client_commit"),
        "reported_at": device.status_reported_at,
        "status": status,
    }


@app.post("/devices/status", status_code=204)
def post_device_status(
    request: Request,
    report: DeviceStatusReport,
    user: User = Depends(get_current_user),
):
    """Store the latest status report from a device.

    The caller's device token is the identity; a token that isn't linked to a
    device is rejected.
    """
    auth_token = getattr(request.state, "auth_token", None)
    device_id = auth_token.device_id if auth_token is not None else None
    device = next(
        (row for row in list_user_devices(user.id) if row.id == device_id), None
    )
    if device is None:
        raise HTTPException(status_code=400, detail="device token required")
    set_device_status(
        device.id,
        json.dumps(report.model_dump(mode="json"), sort_keys=True),
    )
    return Response(status_code=204)


@app.post("/ingest/v1/usage", status_code=204)
def ingest_usage(request: Request, report: UsageReport):
    """Record one client-proxy usage report. Ingest tokens only: no cookie,
    bearer or loopback-owner fallback. The server prices; the client sends
    metadata only."""
    user_id, device_id = resolve_ingest_request(request)
    fields = report.model_dump(exclude={"tool_calls", "base_url"})
    if fields["ts"] is None:
        fields["ts"] = time.time_ns() // 1000
    fields.update(
        base_url=report.base_url,
        base_url_provider=report.provider,
        base_url_source="proxy_config",
        tool_calls=[(tc.tool_use_id, tc.tool_name) for tc in report.tool_calls],
    )
    publish(user_id, [{"kind": "usage", "device_id": device_id, "fields": fields}])
    return Response(status_code=204)


@app.get("/devices/status")
def get_devices_status(user: User = Depends(get_current_user)):
    """Latest report per device, for the caller's devices only."""
    entries = [
        _device_status_entry(device)
        for device in list_user_devices(user.id)
        if device.status_json is not None
    ]
    entries.sort(key=lambda entry: entry["reported_at"], reverse=True)
    return {"devices": entries}


def _require_device_cli_token(request: Request) -> tuple[str, str]:
    """Report routes accept device-bound CLI credentials only.

    The web token (browser cookie/bearer), ingest tokens, and the tokenless
    loopback owner all authenticate the API gate but are not report
    credentials. Returns (user_id, device_id).
    """
    auth_token = getattr(request.state, "auth_token", None)
    if auth_token is None or auth_token.kind != "cli" or auth_token.device_id is None:
        raise HTTPException(status_code=403, detail="device-bound cli token required")
    return get_current_user(request).id, auth_token.device_id


@app.post("/evaluation-jobs/started", status_code=204)
def report_evaluation_started(request: Request, report: EvaluationStartedReport):
    """Record a client run start. Informational: a completed report may create
    the run on its own."""
    user_id, device_id = _require_device_cli_token(request)
    try:
        outcome = record_evaluation_started(
            job_id=report.job_id,
            session_id=report.session_id,
            client_source=report.client_source,
            evaluator_type=report.evaluator_type,
            started_at=report.started_at,
            user_id=user_id,
            device_id=device_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if outcome == "conflict":
        raise HTTPException(
            status_code=409, detail="job id conflicts with an existing run"
        )
    return Response(status_code=204)


@app.post("/evaluation-jobs/completed", status_code=204)
def report_evaluation_completed(request: Request, report: EvaluationCompletedReport):
    """Record a client run outcome. A successful report writes the result onto
    the session; the job row keeps run metadata only."""
    user_id, device_id = _require_device_cli_token(request)
    try:
        outcome = record_evaluation_completed(
            job_id=report.job_id,
            session_id=report.session_id,
            client_source=report.client_source,
            evaluator_type=report.evaluator_type,
            started_at=report.started_at,
            finished_at=report.finished_at,
            status=report.status,
            result=report.result.model_dump(mode="json") if report.result else None,
            error=report.error,
            user_id=user_id,
            device_id=device_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if outcome == "conflict":
        raise HTTPException(
            status_code=409, detail="job id conflicts with an existing run"
        )
    return Response(status_code=204)


@app.get("/evaluation-jobs/active")
def active_evaluation_jobs(request: Request, session_ids: str | None = None):
    """Running attempts for the caller, keyed by session. There is no server
    queue, so a run never has a queue position."""
    parsed_session_ids = [
        item for item in (session_ids or "").split(",") if item
    ] or None
    jobs = list_active_evaluation_jobs(
        session_ids=parsed_session_ids, user_id=_request_user_id(request)
    )
    # The query is newest-first; preserve that attempt if an earlier client
    # start report for the same session has not received its completion yet.
    return {"jobs": {job["session_id"]: job for job in reversed(jobs)}}


@app.get("/evaluation-jobs/{job_id}")
def get_evaluation_job_route(request: Request, job_id: str):
    job = get_evaluation_job(job_id, user_id=_request_user_id(request))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/sessions/{session_id}/evaluation-jobs")
def session_evaluation_jobs(request: Request, session_id: str):
    jobs = list_session_evaluation_jobs(session_id, user_id=_request_user_id(request))
    return {"jobs": jobs}


@app.put("/sessions/{session_id}/evaluation")
def put_session_evaluation(
    request: Request, session_id: str, update: SessionEvaluationUpdate
):
    if update.outcome not in VALID_OUTCOMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid outcome: {update.outcome}. Must be one of {sorted(VALID_OUTCOMES)}",
        )
    if update.source not in VALID_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source: {update.source}. Must be one of {sorted(VALID_SOURCES)}",
        )
    if update.source != "manual":
        raise HTTPException(
            status_code=400,
            detail="Manual evaluation endpoint only accepts source 'manual'.",
        )
    try:
        upsert_session_evaluation(
            session_id=session_id,
            outcome=update.outcome,
            source=update.source,
            confidence=update.confidence,
            task_title=update.task_title,
            task_title_zh=update.task_title_zh,
            summary=update.summary,
            evidence=update.evidence,
            failure_reason=update.failure_reason,
            project=update.project,
            user_id=_request_user_id(request),
        )
    except ValueError as e:
        if "Session not found" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise
    return {"status": "success"}


@app.get("/sessions/{session_id}/evaluation")
def get_evaluation(request: Request, session_id: str):
    evaluation = get_session_evaluation(session_id, user_id=_request_user_id(request))
    return {"evaluation": evaluation}


@app.delete("/sessions/{session_id}/evaluation")
def delete_evaluation(request: Request, session_id: str):
    deleted = delete_session_evaluation(session_id, user_id=_request_user_id(request))
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}


@app.get("/usage/{usage_id}/tool-calls")
def get_usage_tool_calls(request: Request, usage_id: str):
    return fetch_tool_calls(usage_id=usage_id, user_id=_request_user_id(request))


@app.get("/sessions/{session_id}/tool-calls")
def get_session_tool_calls(request: Request, session_id: str):
    return fetch_tool_calls(session_id=session_id, user_id=_request_user_id(request))


@app.get("/sessions/{session_id}/tool-calls/summary")
def get_session_tool_calls_summary(request: Request, session_id: str):
    summary = summarize_session_tool_calls(
        session_id, user_id=_request_user_id(request)
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return summary


def _pricing_entry(resolved_cost) -> dict:
    cost = resolved_cost.cost
    return {
        "input": cost.input,
        "output": cost.output,
        "cache_read": cost.cache_read,
        "cache_write": cost.cache_write,
        "tiers": [
            {
                "min_tokens": tier.min_tokens,
                "max_tokens": tier.max_tokens,
                "input": tier.input,
                "output": tier.output,
                "cache_read": tier.cache_read,
                "cache_write": tier.cache_write,
            }
            for tier in cost.tiers
        ],
        "time_rates": [
            {
                "days": sorted(rate.days) if rate.days is not None else None,
                "start_minute": rate.start_minute,
                "end_minute": rate.end_minute,
                "input": rate.cost.input,
                "output": rate.cost.output,
                "cache_read": rate.cost.cache_read,
                "cache_write": rate.cost.cache_write,
            }
            for rate in cost.time_rates
        ],
        "source": resolved_cost.source,
    }


def _fetch_live_sources(config_snapshot):
    """Fetch configured sources with normal TTLs unless auto_fetch is disabled."""
    from src.pricing.sources.registry import fetch_sources

    if not config_snapshot.get("pricing", {}).get("auto_fetch", True):
        return []
    return fetch_sources(config_snapshot)


async def _resolve_live_cost_maps(force=False):
    """Resolve local maps for pricing reads; manual repricing refreshes live maps.

    Returns ``(resolved, model_costs, model_cost_sources)``.
    """
    from src.pricing.maps import (
        MAPS_LOCK,
        MODEL_COST_SOURCES,
        MODEL_COSTS,
        resolve_all_costs,
    )
    from src.pricing.refresh import refresh_live_maps

    with _config_lock:
        config_snapshot = dict(CONFIG)
    if force:
        await asyncio.to_thread(
            refresh_live_maps, config_snapshot, force=True, manual=True
        )
        with MAPS_LOCK:
            model_costs = dict(MODEL_COSTS)
            model_cost_sources = dict(MODEL_COST_SOURCES)
        resolved = {
            key: ResolvedCost(cost=cost, source=model_cost_sources[key])
            for key, cost in model_costs.items()
        }
    else:
        resolved = resolve_all_costs(
            config_snapshot,
            await asyncio.to_thread(_fetch_live_sources, config_snapshot),
        )
        model_costs = {key: rc.cost for key, rc in resolved.items()}
        model_cost_sources = {key: rc.source for key, rc in resolved.items()}
    return resolved, model_costs, model_cost_sources


# Listing order: the caller's overrides, server config, then the fetched
# sources in resolution priority; anything else sorts after, by name.
_PRICING_SOURCE_ORDER = ("user", "server", "openrouter", "litellm")


@app.get("/pricing")
async def get_pricing(
    request: Request,
    source: str = Query("", description="comma-separated sources; empty = all"),
    q: str = "",
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """One page of models with resolved pricing, the caller's own overrides on
    top (source "user"; ``override`` names the stored override that priced
    the row, which a Reset must delete). ``counts`` covers every source
    matching ``q``; ``total`` is the match count for ``source`` + ``q``."""
    overrides = await asyncio.to_thread(
        load_model_price_overrides, user_id=_request_user_id(request)
    )
    resolved, _costs, _sources = await _resolve_live_cost_maps()
    override_sources = dict.fromkeys(overrides, "user")
    entries: dict[str, ResolvedCost] = {}
    override_of: dict[str, str] = {}
    for key, rc in resolved.items():
        # Same segment matching as record time, so e.g. an override for
        # "proxy/zen/gpt-4o" shows on "gpt-4o" too.
        # ponytail: rebuilds the tiny override segment index per key; hoist
        # it if users ever carry thousands of overrides.
        match = (
            resolve_cost_match(key, overrides, override_sources) if overrides else None
        )
        if match is None:
            entries[key] = rc
        else:
            entries[key] = ResolvedCost(cost=match.cost, source="user")
            override_of[key] = match.key
    for key, cost in overrides.items():
        entries[key] = ResolvedCost(cost=cost, source="user")
        override_of[key] = key

    needle = q.strip().lower()
    wanted = {s for s in source.split(",") if s}
    counts: dict[str, int] = {}
    matched = []
    for key, rc in entries.items():
        if needle and needle not in key.lower():
            continue
        counts[rc.source] = counts.get(rc.source, 0) + 1
        if not wanted or rc.source in wanted:
            matched.append((key, rc))
    order = {name: i for i, name in enumerate(_PRICING_SOURCE_ORDER)}
    matched.sort(
        key=lambda kv: (order.get(kv[1].source, len(order)), kv[1].source, kv[0])
    )
    return {
        "models": [
            {"name": key, **_pricing_entry(rc), "override": override_of.get(key)}
            for key, rc in matched[offset : offset + limit]
        ],
        "total": len(matched),
        "counts": counts,
    }


Price = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]


class ModelPriceUpdate(BaseModel):
    input: Price
    output: Price
    cache_read: Price | None = None
    cache_write: Price | None = None


def _override_model(model: str) -> str:
    model = normalize_model_name(model)
    if not model:
        raise HTTPException(status_code=422, detail="model must not be empty")
    return model


# Registered before /pricing/{model:path}, which would otherwise swallow it.
@app.get("/pricing/overrides")
def get_pricing_overrides(request: Request):
    return list_model_prices(user_id=_request_user_id(request))


@app.put("/pricing/overrides/{model:path}")
def put_pricing_override(request: Request, model: str, body: ModelPriceUpdate):
    return upsert_model_price(
        user_id=_request_user_id(request),
        model=_override_model(model),
        **body.model_dump(),
    )


@app.delete("/pricing/overrides/{model:path}", status_code=204)
def delete_pricing_override(request: Request, model: str):
    if not delete_model_price(
        user_id=_request_user_id(request), model=_override_model(model)
    ):
        raise HTTPException(status_code=404, detail="override not found")
    return Response(status_code=204)


@app.get("/pricing/{model:path}")
async def get_model_pricing(request: Request, model: str):
    """Return resolved pricing for a single model.

    Follows the same resolution used at record time: the caller's overrides
    first, then config overrides, then the configured price sources (in
    priority order), with a containing-name fallback when no exact match exists.
    """
    model = normalize_model_name(model)
    if not model:
        raise HTTPException(status_code=422, detail="model must not be empty")

    overrides = await asyncio.to_thread(
        load_model_price_overrides, user_id=_request_user_id(request)
    )
    match = resolve_cost_match(model, overrides, dict.fromkeys(overrides, "user"))
    if match is not None:
        return {
            "model": match.key,
            "resolved": True,
            **_pricing_entry(ResolvedCost(cost=match.cost, source="user")),
        }

    resolved, model_costs, model_cost_sources = await _resolve_live_cost_maps()
    match = resolve_cost_match(model, model_costs, model_cost_sources)
    if match is None:
        return {
            "model": model,
            "resolved": False,
            "source": None,
            "input": 0.0,
            "output": 0.0,
            "cache_read": 0.0,
            "cache_write": None,
            "tiers": [],
        }

    return {
        "model": match.key,
        "resolved": True,
        **_pricing_entry(resolved[match.key]),
    }


@app.post("/usage/{usage_id}/recalculate-cost")
async def recalculate_usage_cost_route(request: Request, usage_id: str):
    """Recompute one usage row's cost against current pricing.

    Uses the same live-resolved pricing snapshot as /pricing/{model} (config
    overrides + freshest source data), not the periodically-refreshed
    record-time cache. Skips (leaves the row untouched) when the row's
    model no longer resolves against current pricing, rather than
    overwriting with a zeroed fallback cost.
    """
    user_id = _request_user_id(request)
    _resolved, model_costs, model_cost_sources = await _resolve_live_cost_maps(
        force=True
    )

    result = recalculate_usage_cost(
        usage_id,
        user_id=user_id,
        model_costs=model_costs,
        model_cost_sources=model_cost_sources,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="usage row not found")
    if result.skipped or result.old_costs is None or result.new_costs is None:
        return {"skipped": True, "reason": result.reason}

    return {
        "skipped": False,
        "old_costs": {k: float(v) for k, v in result.old_costs.items()},
        "new_costs": {k: float(v) for k, v in result.new_costs.items()},
        "price_snapshot_id": result.price_snapshot_id,
        "cost_estimated": result.price_snapshot_id is None,
        "pricing": result.pricing,
    }


@app.post("/usage/reprice")
async def reprice_estimated_usage(
    request: Request,
    provider: str | None = None,
    model: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    """Reprice the caller's estimated (unbound) usage rows against current
    pricing, honoring the caller's overrides.

    Recomputes stored costs, session/daily rollups, and the snapshot binding for
    every row with no ``price_snapshot_id``, optionally filtered by
    provider/model/date range. ``model`` selects the
    rows a price keyed by that name would match (full name or last segment). Rows whose
    model no longer resolves are left untouched and reported as
    skipped. Bulk work runs in a worker thread; an unfiltered run over a large
    history can take a while.
    """
    user_id = _request_user_id(request)
    _resolved, model_costs, model_cost_sources = await _resolve_live_cost_maps(
        force=True
    )

    return await asyncio.to_thread(
        reprice_estimated_rows,
        user_id=user_id,
        provider=provider,
        model=model,
        since=since,
        until=until,
        model_costs=model_costs,
        model_cost_sources=model_cost_sources,
    )


@app.get("/version")
async def version():
    """Return API version information."""
    return {
        "name": app.title,
        "version": get_version(),
        "commit": await asyncio.to_thread(get_commit),
        "protocol_min": MIN_SUPPORTED_GENERATION,
        "protocol_max": MAX_SUPPORTED_GENERATION,
        # Where clients should point agents. A property of this server's own
        # config, and not a secret: ingesting still needs a token.
        "otlp_logs_endpoint": f"{resolve_server_urls(CONFIG)['otlp_url']}/v1/logs",
    }


@app.get("/install.sh")
async def hosted_installer():
    """Publish the installer preset to this server: it installs the client."""
    server_url = resolve_server_urls(CONFIG)["api_url"]
    try:
        parsed = urlsplit(server_url)
        _ = parsed.port
        valid = (
            bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and not any(char.isspace() for char in server_url)
            and (
                parsed.scheme == "https"
                or (
                    parsed.scheme == "http"
                    and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                )
            )
        )
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(
            503, "configure an HTTPS API origin before client installation"
        )
    installer_path = Path(__file__).resolve().parent.parent / "install.sh"
    script = installer_path.read_text(encoding="utf-8")
    script = script.replace("__TOKENAGE_SERVER_URL__", shlex.quote(server_url))
    script = script.replace("__TOKENAGE_INSTALL_COMMIT__", shlex.quote(""))
    return Response(script, media_type="text/x-shellscript")


# Serve built frontend if available (must come after all API routes)
_frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    _index_html = _frontend_dist / "index.html"

    class SPACatchAllMiddleware(BaseHTTPMiddleware):
        """Rewrite non-API, non-file requests to /index.html for SPA routing."""

        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            requested = path.lstrip("/")
            serving_index = path == "/index.html"
            if (
                request.method in ("GET", "HEAD")
                and _index_html.is_file()
                and not any(path.startswith(p) for p in _SPA_API_PREFIXES)
                and not Path(requested).suffix
                and not (_frontend_dist / requested).is_file()
            ):
                request.scope["path"] = "/index.html"
                serving_index = True
            response = await call_next(request)
            if serving_index:
                # index.html references content-hashed asset filenames that
                # change every build; it must always revalidate (cheap, via
                # the ETag/Last-Modified StaticFiles already sets) so a stale
                # cached copy never keeps pointing at a deleted JS/CSS bundle.
                response.headers["Cache-Control"] = "no-cache"
            return response

    app.add_middleware(SPACatchAllMiddleware)
    app.mount(
        "/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend"
    )

# Registered last so the auth gate runs first (outermost): it must see the
# original request path, before the SPA catch-all rewrites it.
app.add_middleware(AuthGateMiddleware)

# Keep this outermost: metrics sockets never enter auth, APIs or the SPA.
configure_metrics(CONFIG["metrics"]["enabled"])
app.add_middleware(
    MetricsMiddleware,
    enabled=CONFIG["metrics"]["enabled"],
    port=CONFIG["metrics"]["port"],
    host=CONFIG["metrics"]["host"],
    allowed_hosts=CONFIG["metrics"]["allowed_hosts"],
)


def _assert_api_routes_classified() -> None:
    """Fail loudly at import time if a route isn't covered by the auth-gate
    classification (`_SPA_API_PREFIXES` / `AUTH_GATE_EXTRA_GATED_PATHS`).

    `_is_public_path` treats anything that doesn't match `_SPA_API_PREFIXES`
    as public by default (it's meant for static/SPA paths). A new API route
    added without updating that list would silently fall into "public"
    instead of "gated" — this turns that into an import-time crash instead
    of a silent auth bypass.
    """
    unclassified = [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and not any(route.path.startswith(prefix) for prefix in _SPA_API_PREFIXES)
        and route.path not in AUTH_GATE_EXTRA_GATED_PATHS
    ]
    if unclassified:
        raise RuntimeError(
            "Routes not covered by _SPA_API_PREFIXES/AUTH_GATE_EXTRA_GATED_PATHS "
            f"(would be served without an auth check when auth is enabled): "
            f"{unclassified}"
        )


_assert_api_routes_classified()


if __name__ == "__main__":
    import uvicorn

    port = CONFIG["server"]["api_port"]
    uvicorn.run(app, host=CONFIG["server"]["host"], port=port)
