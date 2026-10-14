import asyncio
import json
import logging
import os
import re
import shlex
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator
from starlette.middleware.base import BaseHTTPMiddleware

from protocol import MAX_SUPPORTED_GENERATION, MIN_SUPPORTED_GENERATION
from protocol.device_status import DeviceStatusReport
from protocol.usage_report import UsageReport
from src.config.app import (
    CONFIG,
    CONFIG_PATH,
    _apply_patch,
    _config_lock,
    refresh_runtime_config,
    set_evaluation_evaluator,
)
from src.config.server_config import (
    resolve_otlp_host_port,
    resolve_server_urls,
)

from ._version import get_version
from .auth import (
    _require_local_owner,
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
    delete_session_evaluation,
    distinct_client_sources,
    distinct_tool_names,
    fetch_recent_usage,
    fetch_session_selector_rows,
    fetch_sessions,
    fetch_tool_calls,
    get_evaluation_job_progress,
    get_session_evaluation,
    get_usage_high_watermark_ts,
    init_db,
    list_active_evaluation_jobs_with_progress,
    list_session_evaluation_jobs_with_progress,
    recalculate_usage_cost,
    reprice_estimated_rows,
    summarize_session_tool_calls,
    summarize_sessions,
    summarize_tool_calls,
    summarize_usage_by_provider,
    summarize_usage_by_source,
    summarize_usage_daily,
    summarize_usage_window,
    update_queued_evaluation_job_evaluator,
    upsert_session_evaluation,
)
from .evaluation import (
    VALID_EVALUATOR_AGENTS,
    list_evaluator_agents,
    require_available_evaluator_type,
    start_session_evaluation_job,
)
from .evaluation_worker import load_evaluation_worker_config, run_evaluation_worker
from .pricing.costs import resolve_cost_match
from .pricing.snapshots import enrich_rows
from .ratelimit import rate_limit_hit, resolve_ingest_request
from .recorder import record_usage
from .utils import normalize_model_name

logger = logging.getLogger(__name__)
EVALUATION_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 5
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
    "/model-effectiveness",
    "/config",
    "/pricing",
    "/local/",
    "/version",
    "/install.sh",
    "/ingest/",
)

# Public: sign-in endpoints, /auth/me (the frontend probes it before login),
# and /version. Everything else API-shaped requires a resolved user.
AUTH_GATE_PUBLIC_PATHS = ("/auth/me", "/version", "/install.sh")

# FastAPI's interactive docs and schema are not in the public allowlist, so
# they are gated like any other API surface.
AUTH_GATE_EXTRA_GATED_PATHS = ("/docs", "/redoc", "/openapi.json")


class ConfigPatch(BaseModel):
    path: list[str]
    op: Literal["set", "delete"]
    value: float | None = None

    @model_validator(mode="before")
    @classmethod
    def require_numeric_value_for_set(cls, data):
        if not isinstance(data, dict):
            return data
        if data.get("op") != "set":
            return data
        value = data.get("value")
        if value is None:
            raise ValueError("set patches require a numeric value")
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("set patches require a numeric value")
        return data


def _request_user_id(request: Request) -> str:
    return get_current_user(request).id


class ConfigPatchUpdate(BaseModel):
    patches: list[ConfigPatch]


def _evaluation_metadata_payload() -> dict:
    worker_config = load_evaluation_worker_config()
    evaluators = list_evaluator_agents()
    default_evaluator = worker_config.evaluator
    default_available = any(
        evaluator["id"] == default_evaluator and evaluator["available"]
        for evaluator in evaluators
    )
    return {
        "evaluators": evaluators,
        "global_evaluator_type": default_evaluator,
        "global_evaluator_available": default_available,
    }


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


class EvaluateSessionWithLlmRequest(BaseModel):
    evaluator_type: str | None = None


class EvaluationJobUpdate(BaseModel):
    evaluator_type: str


class EvaluationConfigUpdate(BaseModel):
    evaluator: str


def _parse_device_status_json(raw: str | None) -> dict | None:
    if raw is None:
        return None
    try:
        status = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return status if isinstance(status, dict) else None


async def _stop_evaluation_worker(
    worker_task: asyncio.Task,
    *,
    timeout_seconds: float | None = None,
) -> None:
    timeout = (
        EVALUATION_WORKER_SHUTDOWN_TIMEOUT_SECONDS
        if timeout_seconds is None
        else timeout_seconds
    )
    done, _pending = await asyncio.wait({worker_task}, timeout=timeout)
    if done:
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        return

    worker_task.cancel()
    logger.warning("Evaluation worker shutdown timed out; cancelling worker task")
    done, _pending = await asyncio.wait({worker_task}, timeout=timeout)
    if done:
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        return
    logger.warning("Evaluation worker task did not finish after cancellation")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(run_evaluation_worker(stop_event=stop_event))
    try:
        yield
    finally:
        stop_event.set()
        await _stop_evaluation_worker(worker_task)


app = FastAPI(title="tokenage-api", lifespan=lifespan)
app.include_router(auth_router)


def _is_public_path(path: str) -> bool:
    """Public: sign-in endpoints, /auth/me, /version, SPA."""
    if not any(path.startswith(prefix) for prefix in _SPA_API_PREFIXES):
        return path not in AUTH_GATE_EXTRA_GATED_PATHS
    if path.startswith("/ingest/"):
        # Ingest routes authenticate themselves: ingest tokens only, which the
        # user gate (cli/web tokens, loopback owner) deliberately rejects.
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
        resolved = _resolve_request_user(request)
        if resolved is None:
            return JSONResponse(status_code=401, content=AUTH_GATE_LOGIN_REQUIRED)
        user, auth_token = resolved
        if auth_token is not None:  # the loopback owner (no token) is exempt
            retry_after = rate_limit_hit(f"api:{user.id}", "api_per_minute")
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
    only_failed: bool = False,
    status_429: bool = False,
    status_4xx: bool = False,
    status_5xx: bool = False,
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
                only_failed=only_failed,
                status_429=status_429,
                status_4xx=status_4xx,
                status_5xx=status_5xx,
            )
        )
    )


@app.get("/usage/count")
async def get_usage_count(
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
async def usage_high_watermark(request: Request):
    return {"ts": get_usage_high_watermark_ts(user_id=_request_user_id(request))}


@app.get("/usage/sources")
async def usage_sources(
    request: Request,
    since: str | None = None,
    until: str | None = None,
):
    return distinct_client_sources(
        user_id=_request_user_id(request), since=since, until=until
    )


@app.get("/usage/tools")
async def usage_tools(
    request: Request,
    since: str | None = None,
    until: str | None = None,
):
    return distinct_tool_names(
        user_id=_request_user_id(request), since=since, until=until
    )


@app.get("/usage/run-summary")
async def usage_run_summary(
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
async def usage_summary(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
):
    return summarize_usage_daily(
        user_id=_request_user_id(request),
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
    )


@app.get("/usage/by-source")
async def usage_by_source(
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
async def usage_by_tool(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
    only_failed: bool = False,
    status_429: bool = False,
    status_4xx: bool = False,
    status_5xx: bool = False,
):
    return summarize_tool_calls(
        user_id=_request_user_id(request),
        since=since,
        until=until,
        provider=provider,
        model=model,
        client_source=client_source,
        device_id=device_id,
        only_failed=only_failed,
        status_429=status_429,
        status_4xx=status_4xx,
        status_5xx=status_5xx,
    )


@app.get("/usage/by-provider")
async def usage_by_provider(
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
async def usage_daily(
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
async def usage_daily_by_dimension(
    request: Request,
    dimension: str = "model",
    since: str | None = None,
    until: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    client_source: str | None = None,
    device_id: str | None = None,
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
    )


@app.get("/sessions")
async def get_sessions(
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
async def get_sessions_summary(
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
async def model_effectiveness(
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
async def sessions_daily_effectiveness(request: Request, date: str):
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
    record_usage(
        **fields,
        user_id=user_id,
        device_id=device_id,
        base_url=report.base_url,
        base_url_provider=report.provider,
        base_url_source="proxy_config",
        tool_calls=[(tc.tool_use_id, tc.tool_name) for tc in report.tool_calls],
    )
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


@app.put(
    "/local/sessions/{session_id}/evaluation",
    dependencies=[Depends(_require_local_owner)],
)
async def put_session_evaluation(
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
            detail="Manual evaluation endpoint only accepts source 'manual'. Use the LLM evaluation endpoint for LLM-sourced results.",
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


@app.get(
    "/local/sessions/{session_id}/evaluation",
    dependencies=[Depends(_require_local_owner)],
)
async def get_evaluation(request: Request, session_id: str):
    evaluation = get_session_evaluation(session_id, user_id=_request_user_id(request))
    return {"evaluation": evaluation}


@app.delete(
    "/local/sessions/{session_id}/evaluation",
    dependencies=[Depends(_require_local_owner)],
)
async def delete_evaluation(request: Request, session_id: str):
    deleted = delete_session_evaluation(session_id, user_id=_request_user_id(request))
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}


@app.post(
    "/local/sessions/{session_id}/evaluate-with-llm",
    status_code=202,
    dependencies=[Depends(_require_local_owner)],
)
async def evaluate_session_with_llm(
    http_request: Request,
    session_id: str,
    request: EvaluateSessionWithLlmRequest | None = None,
):
    configured_evaluator = load_evaluation_worker_config().evaluator
    evaluator_type = (
        request.evaluator_type if request else None
    ) or configured_evaluator
    try:
        evaluator_type = require_available_evaluator_type(evaluator_type)
        return start_session_evaluation_job(
            session_id,
            trigger="manual",
            evaluator_type=evaluator_type,
            user_id=_request_user_id(http_request),
        )
    except ValueError as e:
        message = str(e)
        if "Session not found" in message:
            raise HTTPException(status_code=404, detail=message)
        if "Unsupported session source" in message:
            raise HTTPException(status_code=400, detail=message)
        if (
            "Unsupported evaluator agent" in message
            or "Evaluator not available" in message
        ):
            raise HTTPException(status_code=400, detail=message)
        if "Manual evaluation exists" in message:
            raise HTTPException(status_code=409, detail=message)
        raise


@app.get(
    "/local/poll/{job_id}",
    dependencies=[Depends(_require_local_owner)],
)
async def poll_job(request: Request, job_id: str):
    job = get_evaluation_job_progress(job_id, user_id=_request_user_id(request))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get(
    "/local/evaluation-jobs/active",
    dependencies=[Depends(_require_local_owner)],
)
async def active_evaluation_jobs(request: Request, session_ids: str | None = None):
    parsed_session_ids = [
        item for item in (session_ids or "").split(",") if item
    ] or None
    user_id = _request_user_id(request)
    jobs = list_active_evaluation_jobs_with_progress(
        session_ids=parsed_session_ids, user_id=user_id
    )
    return {
        "jobs": {job["session_id"]: job for job in jobs},
        **_evaluation_metadata_payload(),
    }


@app.get(
    "/local/sessions/{session_id}/evaluation-jobs",
    dependencies=[Depends(_require_local_owner)],
)
async def session_evaluation_jobs(request: Request, session_id: str):
    user_id = _request_user_id(request)
    jobs = list_session_evaluation_jobs_with_progress(session_id, user_id=user_id)
    return {
        "jobs": jobs,
        **_evaluation_metadata_payload(),
    }


@app.patch(
    "/local/evaluation-jobs/{job_id}",
    dependencies=[Depends(_require_local_owner)],
)
async def update_evaluation_job(
    request: Request, job_id: str, update: EvaluationJobUpdate
):
    user_id = _request_user_id(request)
    current = get_evaluation_job_progress(job_id, user_id=user_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if current["status"] != "queued":
        raise HTTPException(
            status_code=409,
            detail="Only queued evaluation jobs can change evaluator",
        )

    try:
        evaluator_type = require_available_evaluator_type(update.evaluator_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    updated = update_queued_evaluation_job_evaluator(
        job_id, evaluator_type=evaluator_type, user_id=user_id
    )
    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Only queued evaluation jobs can change evaluator",
        )
    refreshed = get_evaluation_job_progress(job_id, user_id=user_id)
    return refreshed or updated


@app.get("/usage/{usage_id}/tool-calls")
async def get_usage_tool_calls(request: Request, usage_id: str):
    return fetch_tool_calls(usage_id=usage_id, user_id=_request_user_id(request))


@app.get("/sessions/{session_id}/tool-calls")
async def get_session_tool_calls(request: Request, session_id: str):
    return fetch_tool_calls(session_id=session_id, user_id=_request_user_id(request))


@app.get("/sessions/{session_id}/tool-calls/summary")
async def get_session_tool_calls_summary(request: Request, session_id: str):
    summary = summarize_session_tool_calls(
        session_id, user_id=_request_user_id(request)
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return summary


@app.patch("/config", dependencies=[Depends(_require_local_owner)])
async def patch_config(update: ConfigPatchUpdate):
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError as RuamelYAMLError

    path = os.path.expanduser(CONFIG_PATH)
    yaml_parser = YAML()
    yaml_parser.preserve_quotes = True

    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                config = yaml_parser.load(f) or {}
        else:
            config = {}

        if not isinstance(config, dict):
            raise HTTPException(
                status_code=400,
                detail="Config root must be a YAML mapping",
            )

        for patch in update.patches:
            _apply_patch(config, patch.path, patch.op, patch.value)

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml_parser.dump(config, f)

        await asyncio.to_thread(refresh_runtime_config, path)
        return {"status": "success"}
    except RuamelYAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {str(e)}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/config/evaluation", dependencies=[Depends(_require_local_owner)])
async def update_evaluation_config(update: EvaluationConfigUpdate):
    """Update the global evaluator type in config.yaml."""
    if update.evaluator not in VALID_EVALUATOR_AGENTS:
        raise HTTPException(
            status_code=400, detail=f"Invalid evaluator: {update.evaluator}"
        )
    try:
        set_evaluation_evaluator(update.evaluator)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"global_evaluator_type": update.evaluator}


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
    """Fetch configured price sources unless auto_fetch is disabled."""
    auto_fetch = config_snapshot.get("pricing", {}).get("auto_fetch", True)
    if not auto_fetch:
        return []
    from src.pricing.sources.registry import fetch_sources

    return fetch_sources(config_snapshot)


async def _resolve_live_cost_maps():
    """Live-resolved pricing (config overrides + freshest source data), shared
    by /pricing/{model} and /usage/{id}/recalculate-cost so both price a model
    identically instead of drifting from independently maintained copies.

    Returns ``(resolved, model_costs, model_cost_sources)``.
    """
    from src.pricing.maps import resolve_all_costs

    with _config_lock:
        config_snapshot = dict(CONFIG)
    resolved = resolve_all_costs(
        config_snapshot, await asyncio.to_thread(_fetch_live_sources, config_snapshot)
    )
    model_costs = {key: rc.cost for key, rc in resolved.items()}
    model_cost_sources = {key: rc.source for key, rc in resolved.items()}
    return resolved, model_costs, model_cost_sources


@app.get("/pricing")
async def get_pricing():
    """Return all models with resolved pricing and source metadata."""
    resolved, _costs, _sources = await _resolve_live_cost_maps()
    return {key: _pricing_entry(rc) for key, rc in resolved.items()}


@app.get("/pricing/{model:path}")
async def get_model_pricing(model: str):
    """Return resolved pricing for a single model.

    Follows the same resolution used at record time: config overrides first,
    then the configured price sources (in priority order), with a
    containing-name fallback when no exact match exists.
    """
    model = normalize_model_name(model)
    if not model:
        raise HTTPException(status_code=422, detail="model must not be empty")

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
    _resolved, model_costs, model_cost_sources = await _resolve_live_cost_maps()

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


@app.post("/usage/reprice", dependencies=[Depends(_require_local_owner)])
async def reprice_estimated_usage(
    provider: str | None = None,
    model: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
):
    """Reprice estimated (unbound) usage rows against current pricing.

    Recomputes stored costs, session/daily rollups, and the snapshot binding for
    every row with no ``price_snapshot_id``, optionally filtered by
    provider/model/date range and capped with ``limit``. Rows whose
    model no longer resolves are left untouched and reported as
    skipped. Bulk work runs in a worker thread; an unfiltered run over a large
    history can take a while.
    """
    _resolved, model_costs, model_cost_sources = await _resolve_live_cost_maps()

    return await asyncio.to_thread(
        reprice_estimated_rows,
        provider=provider,
        model=model,
        since=since,
        until=until,
        limit=limit,
        model_costs=model_costs,
        model_cost_sources=model_cost_sources,
    )


def _collector_hint() -> dict[str, str]:
    """The OTLP logs endpoint for clients, or nothing when it is not configured."""
    override = os.environ.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT")
    if override:
        try:
            parsed = urlparse(override)
            if (
                parsed.hostname
                and parsed.port
                and parsed.scheme in {"http", "https"}
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
            ):
                host = parsed.hostname
                scheme = parsed.scheme
                public = urlparse(resolve_server_urls(CONFIG)["otlp_url"])
                if host in {"0.0.0.0", "::"}:
                    host = public.hostname or "localhost"
                    scheme = public.scheme
                elif host in {
                    "localhost",
                    "127.0.0.1",
                    "::1",
                } and public.hostname not in {
                    "localhost",
                    "127.0.0.1",
                    "::1",
                    "0.0.0.0",
                    "::",
                }:
                    # A loopback-only listener is not reachable by clients of a
                    # remotely published API. Do not point them at themselves.
                    return {}
                authority = f"[{host}]" if ":" in host else host
                path = parsed.path or "/v1/logs"
                if not path.endswith("/v1/logs"):
                    return {}
                return {
                    "otlp_logs_endpoint": f"{scheme}://{authority}:{parsed.port}{path}"
                }
            # Do not advertise a different collector when the runtime override
            # has a bind address but cannot safely be shared with clients.
            if parsed.hostname and parsed.port:
                return {}
        except ValueError:
            pass
    otlp_url = resolve_server_urls(CONFIG).get("otlp_url")
    return {"otlp_logs_endpoint": f"{otlp_url}/v1/logs"} if otlp_url else {}


@app.get("/version")
async def version():
    """Return API version information."""
    bind_host, bind_port = resolve_otlp_host_port(CONFIG)
    return {
        "name": app.title,
        "version": get_version(),
        "protocol_min": MIN_SUPPORTED_GENERATION,
        "protocol_max": MAX_SUPPORTED_GENERATION,
        # Bind metadata is separate from the externally published client URL.
        # The resolver emits only host/port, never URL credentials or paths.
        "collector_bind": {"host": bind_host, "port": bind_port},
        # Where clients should point agents. A property of this server's own
        # config, and not a secret: ingesting still needs a token. Left out
        # rather than half-built when the config has no collector URL, because
        # /version is public and must answer either way.
        **_collector_hint(),
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
