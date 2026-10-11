"""Starlette ASGI app: transparent pass-through for OpenAI-compatible and
Anthropic endpoints. Does no auth (it binds to loopback only) and never
forwards the caller's own credentials upstream."""

from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urljoin

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from client.proxy.config import ProviderConfig, ProxyConfig, normalize_provider_name
from client.proxy.report import ReportSink, build_report, send_in_background
from client.proxy.usage import (
    StreamToolCallAccumulator,
    compute_prompt_length,
    extract_tool_calls,
    find_stream_usage,
    parse_client_source,
)

REQUEST_TIMEOUT_SECONDS = 300
logger = logging.getLogger("tokenage.client.proxy")


class NoProvider(Exception):
    pass


def resolve_provider(config: ProxyConfig, model: str) -> tuple[ProviderConfig, str]:
    """Returns (provider, upstream_model). Strips provider prefix if present and not explicitly mapped."""
    if model in config.models:
        return config.models[model], model

    for sep in ("/", "."):
        if sep in model:
            provider_name, upstream_model = model.split(sep, 1)
            provider_name = normalize_provider_name(provider_name)
            if provider_name in config.providers:
                return config.providers[provider_name], upstream_model

    logger.warning("no provider configured for model=%s", model)
    raise NoProvider(f"No provider configured for model '{model}'")


def build_upstream_url(base_url: str, path: str) -> str:
    """Normalize and combine base URL and request path for upstream request."""
    stripped_path = path.lstrip("/")
    if stripped_path.startswith("v1/"):
        stripped_path = stripped_path[3:]
    return urljoin(base_url.rstrip("/") + "/", stripped_path)


def _parse_upstream_error_body(error_body: bytes) -> dict[str, Any]:
    """Best-effort JSON parse of an upstream error body for relaying.

    Decode to text before json.loads: json.loads(bytes) first decodes to str and
    raises UnicodeDecodeError (not a JSONDecodeError) when the body isn't valid
    UTF-8 (e.g. a non-UTF8 gateway error page), which would 500 the proxy.
    """
    error_text = error_body.decode("utf-8", errors="replace") if error_body else ""
    try:
        return json.loads(error_text) if error_text else {"error": "upstream error"}
    except json.JSONDecodeError:
        return {"error": error_text}


def build_forward_headers(
    headers_in: Any, provider: ProviderConfig | None = None
) -> dict[str, str]:
    """Filter and prepare headers for forwarding to upstream provider."""
    headers = {
        k: v
        for k, v in headers_in.items()
        # The caller's credential is whatever its SDK insists on sending
        # (often a placeholder), and x-tokenage-token is ours: never forward
        # either; the provider key is the only credential that goes upstream.
        if k.lower()
        not in {
            "host",
            "content-length",
            "authorization",
            "x-api-key",
            "x-tokenage-token",
        }
    }
    if provider and provider.api_key:
        if provider.auth_scheme == "x-api-key":
            headers["x-api-key"] = provider.api_key
        else:
            headers["authorization"] = f"Bearer {provider.api_key}"
    return headers


def parse_json_body(body: bytes) -> dict[str, Any]:
    """Safely parse JSON request body, returning empty dict if empty."""
    if not body:
        return {}
    return json.loads(body)


def _detail(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status_code)


class ProxyApp:
    def __init__(
        self,
        config: ProxyConfig,
        sink: ReportSink = send_in_background,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.sink = sink
        # One pooled client for the app's lifetime; tests inject a mock upstream.
        self.http = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS, transport=transport
        )

    def report(self, **fields: Any) -> None:
        """Hand one report to the sink. Never raises into the model call."""
        try:
            self.sink(build_report(**fields))
        except Exception as exc:
            logger.warning("usage report dropped: %s", type(exc).__name__)

    async def _forward_stream_or_error(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
        provider: ProviderConfig,
        model: str,
        client_source: str | None,
        path: str,
        started_at: float,
        prompt_length: int,
    ) -> Response:
        """Open an upstream streaming connection, check status, and relay or error."""
        req = self.http.build_request("POST", url, headers=headers, content=body)
        upstream = await self.http.send(req, stream=True)

        if upstream.status_code >= 400:
            logger.warning(
                "upstream rejected: model=%s upstream=%s status=%s",
                model,
                url,
                upstream.status_code,
            )
            try:
                error_body = await upstream.aread()
            finally:
                await upstream.aclose()
            return JSONResponse(
                content=_parse_upstream_error_body(error_body),
                status_code=upstream.status_code,
            )

        async def _relay():
            # Anthropic streams usage across two events: message_start carries
            # input/cache tokens, message_delta carries the final output token
            # count. Merge raw usage dicts across events instead of overwriting,
            # so the later event doesn't wipe out the earlier one's fields.
            raw_usage: dict[str, Any] = {}
            ttft_ms: int | None = None
            buffer = ""
            tool_accumulator = StreamToolCallAccumulator()

            try:
                async for chunk in upstream.aiter_bytes():
                    if ttft_ms is None:
                        ttft_ms = int((time.monotonic() - started_at) * 1000)
                    yield chunk

                    try:
                        buffer += chunk.decode(errors="ignore")
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line.startswith("data:") or "[DONE]" in line:
                                continue

                            payload = json.loads(line[5:].strip())
                            stream_usage = find_stream_usage(payload)
                            if stream_usage is not None:
                                raw_usage.update(stream_usage)
                            tool_accumulator.accumulate(payload)
                    except Exception:
                        continue
            finally:
                await upstream.aclose()
                self.report(
                    provider=provider.name,
                    model=model,
                    endpoint=path,
                    base_url=provider.base_url,
                    client_source=client_source,
                    raw_usage=raw_usage,
                    tool_calls=tool_accumulator.get_tool_calls(),
                    prompt_length=prompt_length,
                    latency_ms=int((time.monotonic() - started_at) * 1000),
                    ttft_ms=ttft_ms,
                    status=upstream.status_code,
                )

        # The background task closes the upstream if the caller disconnects
        # before _relay() ever starts (its finally never runs then).
        return StreamingResponse(
            _relay(),
            media_type="text/event-stream",
            background=BackgroundTask(upstream.aclose),
        )

    async def forward(self, request: Request, path: str) -> Response:
        """Core proxy logic: resolve provider, forward request, and report usage."""
        body = await request.body()
        try:
            body_json = parse_json_body(body)
        except ValueError:
            return _detail("Request body must be valid JSON", 400)
        if not isinstance(body_json, dict):
            return _detail("Request body must be a JSON object", 400)
        client_source = parse_client_source(request.headers.get("user-agent", ""))
        model = body_json.get("model", "")
        if not isinstance(model, str):
            return _detail("'model' must be a string", 400)
        try:
            provider, upstream_model = resolve_provider(self.config, model)
        except NoProvider as exc:
            return _detail(str(exc), 404)
        prompt_length = compute_prompt_length(body_json)

        if upstream_model != model:
            body_json["model"] = upstream_model
            body = json.dumps(body_json).encode()

        url = build_upstream_url(provider.base_url, path)
        headers = build_forward_headers(request.headers, provider)
        started_at = time.monotonic()

        if body_json.get("stream", False):
            # chat/completions only reports stream usage on request; the other
            # APIs report it natively and may reject the unknown field.
            if path == "/v1/chat/completions" and "stream_options" not in body_json:
                body_json["stream_options"] = {"include_usage": True}
                body = json.dumps(body_json).encode()

            return await self._forward_stream_or_error(
                url=url,
                headers=headers,
                body=body,
                provider=provider,
                model=upstream_model,
                client_source=client_source,
                path=path,
                started_at=started_at,
                prompt_length=prompt_length,
            )

        response = await self.http.post(url, headers=headers, content=body)

        latency_ms = int((time.monotonic() - started_at) * 1000)
        response_json: Any = None
        if response.status_code >= 400:
            logger.warning(
                "upstream rejected: model=%s upstream=%s status=%s",
                model,
                url,
                response.status_code,
            )
            # Relaying the upstream error still reports a failed call (status
            # >= 400). The body may be non-JSON/non-UTF8, so parse defensively.
            relayed: Response = JSONResponse(
                content=_parse_upstream_error_body(response.content),
                status_code=response.status_code,
            )
        else:
            try:
                response_json = response.json()
            except ValueError:
                response_json = None
            relayed = (
                JSONResponse(content=response_json, status_code=response.status_code)
                if response_json is not None
                else Response(
                    content=response.content, status_code=response.status_code
                )
            )

        parsed = response_json if isinstance(response_json, dict) else {}
        self.report(
            provider=provider.name,
            model=upstream_model,
            endpoint=path,
            base_url=provider.base_url,
            client_source=client_source,
            raw_usage=parsed.get("usage") or {},
            tool_calls=extract_tool_calls(parsed),
            prompt_length=prompt_length,
            latency_ms=latency_ms,
            ttft_ms=None,
            status=response.status_code,
        )
        return relayed

    def endpoint(self, path: str):
        async def handler(request: Request) -> Response:
            return await self.forward(request, path)

        return handler

    async def list_models(self, request: Request) -> Response:
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {"id": model, "object": "model", "owned_by": provider.name}
                    for model, provider in self.config.models.items()
                ],
            }
        )

    async def get_model(self, request: Request) -> Response:
        model_id = request.path_params["model_id"]
        provider = self.config.models.get(model_id)
        if provider is None:
            return _detail(f"Model '{model_id}' not found", 404)
        return JSONResponse(
            {"id": model_id, "object": "model", "owned_by": provider.name}
        )


def create_app(
    config: ProxyConfig,
    sink: ReportSink = send_in_background,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Starlette:
    proxy = ProxyApp(config, sink, transport)
    routes = [
        Route(p, proxy.endpoint(target), methods=["POST"])
        for target, paths in {
            "/v1/chat/completions": ("/chat/completions", "/v1/chat/completions"),
            "/v1/responses": ("/responses", "/v1/responses"),
            "/v1/messages": ("/messages", "/v1/messages"),
        }.items()
        for p in paths
    ]
    routes += [
        Route(p, proxy.list_models, methods=["GET"])
        for p in ("/api/v1/models", "/v1/models", "/models")
    ]
    routes += [
        Route(p, proxy.get_model, methods=["GET"])
        for p in ("/models/{model_id:path}", "/v1/models/{model_id:path}")
    ]

    @asynccontextmanager
    async def lifespan(_: Starlette):
        try:
            yield
        finally:
            await proxy.http.aclose()

    return Starlette(routes=routes, lifespan=lifespan)
