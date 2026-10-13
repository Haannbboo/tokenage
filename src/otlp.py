import json
import logging
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request

from src.config.app import CONFIG

from .database import init_db
from .provider_parser import parse_provider_metadata
from .ratelimit import resolve_ingest_request_async
from .recorder import record_tool_call, record_usage, update_tool_call_duration
from .utils import secs_to_micros

log = logging.getLogger(__name__)

CLAUDE_EVENT = "claude_code.api_request"
CODEX_EVENT = "codex.sse_event"
CODEX_API_REQUEST_EVENT = "codex.api_request"
OPENCODE_EVENT = "opencode.message_completed"
KILO_EVENT = "kilo.message_completed"
CODEX_DEBUG_FILE = "/tmp/codex-otlp-debug.json"
CODEX_SERVICE_NAMES = {"codex_cli_rs", "codex_exec", "codex-app-server"}
KILO_SERVICE_NAMES = {"kilo"}
RETIRED_SERVICE_NAMES = {"gemini-cli"}
KNOWN_SERVICE_NAMES = (
    {"claude-code", "opencode"} | CODEX_SERVICE_NAMES | KILO_SERVICE_NAMES
)

# State cache for merging Codex events: conversation key -> {duration_ms, ttft_ms, start_ms}
# `start_ms` comes from codex.websocket_request (the send time of a sampling
# request); `duration_ms` from codex.api_request in HTTP mode, where it is the
# request's own duration. response.completed turns whichever is present into a
# per-request latency.
codex_state: dict = {}

# Tool-name buffers: keyed by correlation ID, values are lists of {tool_name, tool_use_id, ts}
_claude_tool_buffer: dict[str, list[dict]] = {}
_codex_tool_buffer: dict[str, list[dict]] = {}
_opencode_tool_buffer: dict[str, list[dict]] = {}  # keyed by message.id

# Most recent Claude usage row per prompt.id: prompt_key -> (usage_id, session_id, ts_ms).
# Claude's prompt.id spans a whole user turn, so this lets a trailing tool_result
# attach to the request that produced it instead of the next one.
_claude_last_usage: dict[str, tuple[str, str | None, int]] = {}
# Fallback window when a usage row has no known latency, used to tell a
# just-decided tool (still awaiting its result) from a result-less stale one.
CLAUDE_TOOL_FLUSH_GRACE_MS = 2000


def _scoped_id(identifier: str, user_id: str) -> str:
    return f"{user_id}:{identifier}"


def _fallback_tool_use_id() -> str:
    # ponytail: random id when the provider omits one, so distinct calls
    # never collide on the empty-string PK. Doesn't cover providers that
    # emit real but non-globally-unique ids (e.g. some self-hosted gateways).
    return f"generated:{uuid.uuid4().hex}"


def _attr(attributes: list, key: str):
    """Extract a value from OTLP attribute list [{key, value: {stringValue|intValue|...}}]."""
    for a in attributes:
        if a.get("key") == key:
            v = a.get("value", {})
            if "intValue" in v:
                return int(v["intValue"])
            if "doubleValue" in v:
                return v["doubleValue"]
            if "stringValue" in v:
                return v["stringValue"]
            if "boolValue" in v:
                return v["boolValue"]
    return None


def _codex_event_ts_ms(attrs: list) -> int | None:
    """Milliseconds from Codex's `event.timestamp` ISO attribute, or None.

    Codex ships timeUnixNano=0 and only sets observedTimeUnixNano (batch flush
    time), which cannot bound request latency. `event.timestamp` is stamped when
    the event is produced, so differences between events are real durations.
    """
    value = _attr(attrs, "event.timestamp")
    if not isinstance(value, str):
        return None
    try:
        return int(
            datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000
        )
    except ValueError:
        return None


def _optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _record_ts_ms(record: dict) -> int:
    """Milliseconds from a log record, falling back to observedTimeUnixNano.

    Codex tool events ship timeUnixNano=0 with only the observation time set;
    without the fallback the buffer entry looks stale and gets evicted before
    the matching usage event arrives.
    """
    time_ns = record.get("timeUnixNano")
    if time_ns is None or str(time_ns) == "0":
        time_ns = record.get("observedTimeUnixNano")
    if time_ns is None:
        return 0
    try:
        return int(time_ns) // 1_000_000
    except (TypeError, ValueError):
        return 0


def _buffer_tool_call(
    buffer: dict[str, list[dict]],
    key: str | None,
    *,
    tool_name: str,
    tool_use_id: str,
    ts: int,
    duration_ms: int | None = None,
) -> None:
    """Buffer a tool call, merging on tool_use_id so redelivery can't double count."""
    if not key:
        return
    entries = buffer.setdefault(key, [])
    for entry in entries:
        if entry["tool_use_id"] == tool_use_id:
            if tool_name:
                entry["tool_name"] = tool_name
            if duration_ms is not None:
                entry["duration_ms"] = duration_ms
            return
    entries.append(
        {
            "tool_name": tool_name,
            "tool_use_id": tool_use_id,
            "ts": ts,
            "duration_ms": duration_ms,
        }
    )


def _buffer_tool_duration(
    buffer: dict[str, list[dict]],
    key: str | None,
    tool_use_id: str,
    duration_ms: int | None,
) -> bool:
    """Attach the duration from a trailing tool_result to an already-buffered call.

    Only enriches existing entries; a result without a matching decision/name
    event returns False so it can be matched against an already-persisted row.
    """
    if not key or duration_ms is None:
        return False
    for entry in buffer.get(key, []):
        if entry["tool_use_id"] == tool_use_id:
            entry["duration_ms"] = duration_ms
            return True
    return False


def _pop_buffered_tool(
    buffer: dict[str, list[dict]], key: str | None, tool_use_id: str
) -> dict | None:
    """Remove and return a buffered tool call by id, or None when unmatched."""
    if not key or not tool_use_id:
        return None
    entries = buffer.get(key)
    if not entries:
        return None
    for index, entry in enumerate(entries):
        if entry["tool_use_id"] == tool_use_id:
            entries.pop(index)
            if not entries:
                del buffer[key]
            return entry
    return None


def _flush_claude_tools(
    prompt_key: str,
    usage_id: str,
    session_id: str | None,
    user_id: str,
    usage_start_ms: int,
) -> None:
    """Record buffered Claude tools that will never get a tool_result.

    A new api_request for the same prompt can only start after every tool of the
    previous request has produced its result, so any entry decided before this
    request started is result-less (for example a rejected tool).
    """
    entries = _claude_tool_buffer.get(prompt_key)
    if not entries:
        return
    remaining = []
    for entry in entries:
        if entry["ts"] >= usage_start_ms:
            remaining.append(entry)
            continue
        record_tool_call(
            tool_use_id=_scoped_id(entry["tool_use_id"], user_id),
            usage_id=usage_id,
            session_id=session_id,
            tool_name=entry["tool_name"],
            duration_ms=entry.get("duration_ms"),
            user_id=user_id,
            client_source="claude-code",
            ts=entry["ts"],
        )
    if remaining:
        _claude_tool_buffer[prompt_key] = remaining
    else:
        _claude_tool_buffer.pop(prompt_key, None)


def _resource_attr(resource: dict, key: str):
    return _attr(resource.get("attributes", []), key)


def _usage_session_id(
    *,
    service_name: str,
    attrs: list,
    resource_session_id: str,
) -> str | None:
    if service_name in CODEX_SERVICE_NAMES:
        conversation_id = _attr(attrs, "conversation.id")
        return str(conversation_id) if conversation_id else None

    attr_session_id = _attr(attrs, "session.id")
    if attr_session_id:
        return str(attr_session_id)

    if resource_session_id:
        return str(resource_session_id)

    return None


def _state_key(identifier) -> str | None:
    if not identifier:
        return None

    return str(identifier)


@dataclass
class _PromptLengthState:
    """Queued prompt-length values waiting for the matching usage event."""

    values: list[int] = field(default_factory=list)
    ts: int = 0


class PromptLengthTracker:
    """Tracks prompt-event lengths until the later OTLP usage event is written."""

    def __init__(self, prompt_events: set[str]):
        self._prompt_events = prompt_events
        self._state: dict[str, _PromptLengthState] = {}

    def record_prompt_event(
        self,
        service_name: str,
        attrs: list,
        session_id: str,
        user_id: str,
    ) -> None:
        """Store prompt_length from prompt-only events for later usage rows."""
        prompt_length = _attr(attrs, "prompt_length")
        if prompt_length is None:
            return

        event_name = _attr(attrs, "event.name") or ""
        if event_name not in self._prompt_events:
            return

        key = self._key_for(service_name, attrs, session_id, user_id)
        if key is None:
            return

        state = self._state.setdefault(key, _PromptLengthState())
        state.ts = time.time_ns() // 1000
        state.values.append(int(prompt_length))

    def consume_for_usage_event(
        self,
        service_name: str,
        attrs: list,
        session_id: str,
        user_id: str,
    ) -> int:
        """Return the prompt length for a usage row, preferring an inline value when present."""
        prompt_length = _attr(attrs, "prompt_length")
        if prompt_length is not None:
            return int(prompt_length)

        key = self._key_for(service_name, attrs, session_id, user_id)
        if key is None:
            return 0

        state = self._state.get(key)
        if not state:
            return 0

        if not state.values:
            del self._state[key]
            return 0

        value = state.values.pop(0)
        if not state.values:
            del self._state[key]
        return value

    def evict_stale(self, ttl_seconds: int = 600) -> None:
        """Drop old correlation entries so prompt-only events cannot accumulate forever."""
        now = time.time_ns() // 1000
        stale_keys = [
            key
            for key, state in self._state.items()
            if now - state.ts > secs_to_micros(ttl_seconds)
        ]
        for key in stale_keys:
            del self._state[key]

    def _key_for(
        self,
        service_name: str,
        attrs: list,
        session_id: str,
        user_id: str,
    ) -> str | None:
        """Build a stable correlation key from the strongest ID the client exposes."""
        prompt_id = _attr(attrs, "prompt.id") or _attr(attrs, "prompt_id")
        if prompt_id:
            key = f"{service_name}:prompt:{prompt_id}"
            return _scoped_id(key, user_id)

        conversation_id = _attr(attrs, "conversation.id")
        if conversation_id:
            key = f"{service_name}:conversation:{conversation_id}"
            return _scoped_id(key, user_id)

        attr_session_id = _attr(attrs, "session.id") or session_id
        if attr_session_id:
            key = f"{service_name}:session:{attr_session_id}"
            return _scoped_id(key, user_id)

        return None


PROMPT_LENGTH_TRACKER = PromptLengthTracker({"user_prompt", "codex.user_prompt"})


def _extract_claude_fields(
    record: dict,
    attrs: list,
    session_id: str,
    *,
    user_id: str,
) -> dict:
    """Extract normalized usage fields from a Claude OTLP record."""
    time_ns = record.get("timeUnixNano", "0")
    ts = int(time_ns) // 1000

    input_tokens = _attr(attrs, "input_tokens")
    output_tokens = _attr(attrs, "output_tokens")
    cache_read = _attr(attrs, "cache_read_tokens")
    cache_create = _attr(attrs, "cache_creation_tokens")
    status = _attr(attrs, "status_code")

    # prompt_tokens = raw input + cache reads (what the model actually processed)
    prompt_tokens = (int(input_tokens or 0)) + (int(cache_read or 0))
    prompt_length = PROMPT_LENGTH_TRACKER.consume_for_usage_event(
        "claude-code", attrs, session_id, user_id=user_id
    )
    metadata = parse_provider_metadata("claude")
    completion_tokens = int(output_tokens) if output_tokens is not None else None
    cached_tokens = int(cache_read) if cache_read is not None else None
    usage_session_id = _attr(attrs, "session.id") or session_id or None

    total = prompt_tokens + int(output_tokens or 0) + int(cache_create or 0)

    return {
        "ts": ts,
        "provider": metadata.provider,
        "model": _attr(attrs, "model") or "claude-unknown",
        "client_source": "claude-code",
        "session_id": usage_session_id,
        "endpoint": "otlp",
        "prompt_tokens": prompt_tokens,
        "prompt_length": prompt_length,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "cache_creation_tokens": int(cache_create)
        if cache_create is not None
        else None,
        "reasoning_tokens": None,
        "tool_tokens": None,
        "total_tokens": total,
        "latency_ms": _attr(attrs, "duration_ms"),
        "ttft_ms": None,
        "status": status,
        "base_url": metadata.base_url,
        "base_url_provider": metadata.provider,
        "base_url_source": metadata.source,
        "user_id": user_id,
    }


def _extract_opencode_fields(
    record: dict,
    attrs: list,
    session_id: str,
    client_source: str = "opencode",
    *,
    user_id: str,
) -> dict:
    """Extract normalized usage fields from an OpenCode or Kilo OTLP record."""
    time_ns = record.get("timeUnixNano", "0")
    ts = int(time_ns) // 1000

    input_tokens = _attr(attrs, "input_token_count")
    output_tokens = _attr(attrs, "output_token_count")
    reasoning = _attr(attrs, "reasoning_token_count")
    cached = _attr(attrs, "cached_token_count")
    cache_create = _attr(attrs, "cache_creation_token_count")
    prompt_length = _attr(attrs, "prompt_length")
    duration_ms = _attr(attrs, "duration_ms")
    ttft_ms = _attr(attrs, "ttft_ms")
    status = _attr(attrs, "http.response.status_code")
    if status is None:
        status = _attr(attrs, "status_code")
    if status is None:
        status = _attr(attrs, "http.status_code")
    provider_from_attr = _attr(attrs, "provider")
    model = _attr(attrs, "model") or f"{client_source}-unknown"
    usage_session_id = _attr(attrs, "session.id") or session_id or None

    metadata = parse_provider_metadata(
        client_source,
        str(provider_from_attr) if provider_from_attr is not None else None,
    )

    prompt_tokens = (int(input_tokens or 0)) + (int(cached or 0))
    cached_tokens = int(cached) if cached is not None else None

    # Include reasoning in completion_tokens so reasoning_tokens is a subset,
    # matching the codex convention (OpenAI-style).
    if output_tokens is not None:
        completion_tokens = int(output_tokens) + int(reasoning or 0)
    else:
        completion_tokens = None

    # Include cache-creation so total matches the proxy's prompt+completion+
    # cache_creation computation (the source's total_token_count includes it).
    normalized_total = (
        prompt_tokens + int(completion_tokens or 0) + int(cache_create or 0)
    )

    # Pop buffered tool calls for this message.id
    message_id = _attr(attrs, "message.id")
    tool_info: list[dict] = []
    message_key = _scoped_id(message_id, user_id) if message_id else None
    if message_key and message_key in _opencode_tool_buffer:
        tool_info = _opencode_tool_buffer.pop(message_key)

    return {
        "ts": ts,
        "provider": provider_from_attr or metadata.provider,
        "model": model,
        "client_source": client_source,
        "session_id": usage_session_id,
        "endpoint": "otlp",
        "prompt_tokens": prompt_tokens,
        "prompt_length": int(prompt_length) if prompt_length is not None else 0,
        "completion_tokens": completion_tokens,
        "cached_tokens": cached_tokens,
        "cache_creation_tokens": int(cache_create)
        if cache_create is not None
        else None,
        "reasoning_tokens": int(reasoning) if reasoning is not None else None,
        "tool_tokens": None,
        "total_tokens": normalized_total,
        "latency_ms": int(duration_ms) if duration_ms is not None else None,
        "ttft_ms": int(ttft_ms) if ttft_ms is not None else None,
        "status": int(status) if status is not None else None,
        "base_url": metadata.base_url,
        "base_url_provider": metadata.provider,
        "base_url_source": metadata.source,
        "user_id": user_id,
        "_tool_info": tool_info,
    }


def _parse_opencode_record(
    record: dict,
    attrs: list,
    session_id: str,
    client_source: str = "opencode",
    *,
    user_id: str,
    device_id: str,
) -> None:
    fields = _extract_opencode_fields(
        record,
        attrs,
        session_id,
        client_source=client_source,
        user_id=user_id,
    )
    tool_calls = fields.pop("_tool_info", [])
    usage = record_usage(**fields, device_id=device_id)
    if usage:
        for tool_info in tool_calls:
            record_tool_call(
                tool_use_id=_scoped_id(tool_info["tool_use_id"], user_id),
                usage_id=usage.id,
                session_id=usage.session_id,
                tool_name=tool_info["tool_name"],
                duration_ms=tool_info.get("duration_ms"),
                user_id=user_id,
                client_source=client_source,
                ts=tool_info["ts"],
            )


def _codex_state_for(attrs: list, user_id: str) -> dict | None:
    """Return the mutable per-conversation Codex state, creating it if needed.

    Returns None for events without a conversation id (for example the
    `/models` api_request that Codex emits alongside a websocket session).
    """
    conv_id = _attr(attrs, "conversation.id")
    state_key = _state_key(conv_id)
    if not state_key:
        return None
    state_key = _scoped_id(state_key, user_id)
    state = codex_state.get(state_key)
    if state is None:
        state = {"ts": time.time_ns() // 1000}
        codex_state[state_key] = state
    return state


def _handle_codex_state_event(attrs: list, user_id: str) -> bool:
    """Handle Codex state-only events.

    Returns True when the event was consumed without producing a usage row.

    Codex reports no total duration on the token-bearing response.completed
    event. It does emit the request send (`codex.websocket_request`) and, in
    HTTP mode, the request end with its own duration (`codex.api_request`).
    Cache whichever arrives so response.completed can derive per-request latency.
    """
    event_kind = _attr(attrs, "event.kind")
    event_name = _attr(attrs, "event.name")

    if event_name == "codex.websocket_request":
        start_ms = _codex_event_ts_ms(attrs)
        state = _codex_state_for(attrs, user_id)
        if state is not None and start_ms is not None:
            state["start_ms"] = start_ms
        return True

    if event_kind == CODEX_API_REQUEST_EVENT or event_name == CODEX_API_REQUEST_EVENT:
        duration = _attr(attrs, "duration_ms")
        state = _codex_state_for(attrs, user_id)
        if state is not None and duration is not None:
            state["duration_ms"] = int(duration)
        return True

    if event_kind == "response.created":
        duration = _attr(attrs, "duration_ms")
        state = _codex_state_for(attrs, user_id)
        if state is not None and duration is not None:
            state["ttft_ms"] = int(duration)
        return True

    return False


def _extract_codex_fields(
    record: dict,
    attrs: list,
    service_name: str,
    *,
    user_id: str,
) -> dict | None:
    """Extract normalized usage fields from a Codex OTLP response.completed record."""
    event_kind = _attr(attrs, "event.kind")

    if event_kind != "response.completed":
        return None

    # Only parse if we have token counts (to avoid the other response.completed event)
    input_tokens = _attr(attrs, "input_token_count")
    if input_tokens is None:
        return None

    # Codex ships timeUnixNano=0 and only sets observedTimeUnixNano (batch flush
    # time), which can land well after the real completion. Prefer the event's
    # own timestamp so ts agrees with the latency derived below.
    completed_ms = _codex_event_ts_ms(attrs)
    if completed_ms is not None:
        ts = completed_ms * 1000  # ms -> us
    else:
        time_ns = record.get("timeUnixNano", "0")
        if time_ns == "0":
            time_ns = record.get("observedTimeUnixNano", "0")
        ts = int(time_ns) // 1000

    conv_id = _attr(attrs, "conversation.id")
    usage_session_id = str(conv_id) if conv_id is not None else None
    output_tokens = _attr(attrs, "output_token_count")
    cached_tokens = _attr(attrs, "cached_token_count")
    reasoning_tokens = _attr(attrs, "reasoning_token_count")
    tool_tokens = _attr(attrs, "tool_token_count")
    latency_ms = _attr(attrs, "duration_ms")
    status = _attr(attrs, "http.response.status_code")
    # Codex >=0.157 reports time-to-first-token directly on response.completed.
    ttft_ms = _attr(attrs, "ttft_ms")

    # Turn the cached request start/duration into a per-request latency. Codex
    # never reports total duration on response.completed itself.
    state_key = _state_key(conv_id)
    state_key = _scoped_id(state_key, user_id) if state_key else None
    state = codex_state.pop(state_key, None) if state_key else None
    if state is not None:
        if "duration_ms" in state:
            latency_ms = state["duration_ms"]
        elif "start_ms" in state:
            if completed_ms is not None and completed_ms >= state["start_ms"]:
                latency_ms = completed_ms - state["start_ms"]
        if ttft_ms is None and "ttft_ms" in state:
            ttft_ms = state["ttft_ms"]

    prompt_tokens = int(input_tokens or 0)
    completion_tokens = int(output_tokens or 0)
    total_tokens = prompt_tokens + completion_tokens
    prompt_length = PROMPT_LENGTH_TRACKER.consume_for_usage_event(
        service_name, attrs, "", user_id=user_id
    )
    metadata = parse_provider_metadata("codex")
    model = _attr(attrs, "model") or "codex-unknown"

    # Pop buffered tool calls for this conversation.id
    conv_id = _attr(attrs, "conversation.id")
    tool_info: list[dict] = []
    conversation_key = _scoped_id(conv_id, user_id) if conv_id else None
    if conversation_key and conversation_key in _codex_tool_buffer:
        tool_info = _codex_tool_buffer.pop(conversation_key)

    return {
        "ts": ts,
        "provider": metadata.provider,
        "model": model,
        "client_source": "codex",
        "session_id": usage_session_id,
        "endpoint": "otlp",
        "prompt_tokens": prompt_tokens,
        "prompt_length": prompt_length,
        "completion_tokens": completion_tokens,
        "cached_tokens": int(cached_tokens) if cached_tokens is not None else None,
        "reasoning_tokens": int(reasoning_tokens)
        if reasoning_tokens is not None
        else None,
        "tool_tokens": int(tool_tokens) if tool_tokens is not None else None,
        "total_tokens": total_tokens,
        "latency_ms": int(latency_ms) if latency_ms is not None else None,
        "ttft_ms": int(ttft_ms) if ttft_ms is not None else None,
        "cache_creation_tokens": None,
        "status": status,
        "base_url": metadata.base_url,
        "base_url_provider": metadata.provider,
        "base_url_source": metadata.source,
        "user_id": user_id,
        "_tool_info": tool_info,
    }


def _parse_log_record(
    record: dict,
    service_name: str,
    resource_session_id: str,
    *,
    user_id: str,
    device_id: str,
) -> None:
    if service_name in RETIRED_SERVICE_NAMES:
        return

    attrs = record.get("attributes", [])
    usage_session_id = _usage_session_id(
        service_name=service_name,
        attrs=attrs,
        resource_session_id=resource_session_id,
    )
    PROMPT_LENGTH_TRACKER.record_prompt_event(
        service_name,
        attrs,
        usage_session_id or "",
        user_id=user_id,
    )
    event_name = _attr(attrs, "event.name") or ""

    # Buffer tool events for later association with usage rows. The decision
    # event carries the tool name, the trailing result event the duration.
    ts_attr = _record_ts_ms(record)
    if event_name == "tool_decision" and service_name == "claude-code":
        prompt_id = _attr(attrs, "prompt.id")
        _buffer_tool_call(
            _claude_tool_buffer,
            _scoped_id(prompt_id, user_id) if prompt_id else None,
            tool_name=_attr(attrs, "tool_name") or "",
            tool_use_id=_attr(attrs, "tool_use_id") or _fallback_tool_use_id(),
            ts=ts_attr,
        )
    elif event_name == "tool_result" and service_name == "claude-code":
        # Record the tool as soon as its duration is known, attached to the
        # request that produced it (the last usage row for this prompt). A
        # following request cannot complete before this result exists.
        prompt_id = _attr(attrs, "prompt.id")
        prompt_key = _scoped_id(prompt_id, user_id) if prompt_id else None
        entry = _pop_buffered_tool(
            _claude_tool_buffer, prompt_key, _attr(attrs, "tool_use_id") or ""
        )
        if entry is not None:
            last = _claude_last_usage.get(prompt_key) if prompt_key else None
            usage_id, session_id = (last[0], last[1]) if last else (None, None)
            record_tool_call(
                tool_use_id=_scoped_id(entry["tool_use_id"], user_id),
                usage_id=usage_id,
                session_id=session_id or usage_session_id,
                tool_name=entry["tool_name"],
                duration_ms=_optional_int(_attr(attrs, "duration_ms")),
                user_id=user_id,
                client_source="claude-code",
                ts=entry["ts"],
            )
    elif event_name == "codex.tool_decision" and service_name in CODEX_SERVICE_NAMES:
        conv_id = _attr(attrs, "conversation.id")
        tool_name = _attr(attrs, "tool_name") or ""
        # Skip the exec wrapper — only keep native tool names
        if tool_name != "exec":
            _buffer_tool_call(
                _codex_tool_buffer,
                _scoped_id(conv_id, user_id) if conv_id else None,
                tool_name=tool_name,
                tool_use_id=_attr(attrs, "call_id") or _fallback_tool_use_id(),
                ts=ts_attr,
            )
    elif event_name == "codex.tool_result" and service_name in CODEX_SERVICE_NAMES:
        conv_id = _attr(attrs, "conversation.id")
        call_id = _attr(attrs, "call_id") or ""
        duration_ms = _optional_int(_attr(attrs, "duration_ms"))
        matched_buffer = _buffer_tool_duration(
            _codex_tool_buffer,
            _scoped_id(conv_id, user_id) if conv_id else None,
            call_id,
            duration_ms,
        )
        if not matched_buffer and call_id and duration_ms is not None:
            # The following response.completed can flush and persist a tool
            # before Codex's separate tool_result event reaches the collector.
            # In that case, enrich the existing row instead of dropping timing.
            update_tool_call_duration(
                tool_use_id=_scoped_id(call_id, user_id),
                user_id=user_id,
                client_source="codex",
                duration_ms=duration_ms,
            )
    elif (
        service_name in ({"opencode"} | KILO_SERVICE_NAMES)
        and event_name == f"{service_name}.tool_decision"
    ):
        message_id = _attr(attrs, "message.id")
        tool_name = _attr(attrs, "tool_name") or ""
        if message_id and tool_name:
            _buffer_tool_call(
                _opencode_tool_buffer,
                _scoped_id(message_id, user_id),
                tool_name=tool_name,
                tool_use_id=_attr(attrs, "call_id") or _fallback_tool_use_id(),
                ts=ts_attr,
                duration_ms=_optional_int(_attr(attrs, "duration_ms")),
            )

    if (
        event_name == CLAUDE_EVENT or event_name == "api_request"
    ) and service_name == "claude-code":
        fields: dict | None = _extract_claude_fields(
            record, attrs, usage_session_id or "", user_id=user_id
        )
        if fields is not None:
            usage = record_usage(**fields, device_id=device_id)
            if usage:
                prompt_id = _attr(attrs, "prompt.id")
                prompt_key = _scoped_id(prompt_id, user_id) if prompt_id else None
                usage_ts_ms = int(fields["ts"]) // 1000
                latency_ms = int(fields.get("latency_ms") or 0)
                # Treat an unknown latency as the grace window so a tool whose
                # decision lands just before the request completes is not
                # mistaken for a result-less one.
                window_ms = latency_ms if latency_ms > 0 else CLAUDE_TOOL_FLUSH_GRACE_MS
                if prompt_key:
                    _flush_claude_tools(
                        prompt_key,
                        usage.id,
                        usage.session_id,
                        user_id,
                        usage_ts_ms - window_ms,
                    )
                    _claude_last_usage[prompt_key] = (
                        usage.id,
                        usage.session_id,
                        usage_ts_ms,
                    )
    elif event_name == CODEX_EVENT and service_name in CODEX_SERVICE_NAMES:
        if _handle_codex_state_event(attrs, user_id=user_id):
            return
        fields = _extract_codex_fields(record, attrs, service_name, user_id=user_id)
        if fields is not None:
            tool_calls = fields.pop("_tool_info", [])
            usage = record_usage(**fields, device_id=device_id)
            if usage:
                for tool_info in tool_calls:
                    record_tool_call(
                        tool_use_id=_scoped_id(tool_info["tool_use_id"], user_id),
                        usage_id=usage.id,
                        session_id=usage.session_id,
                        tool_name=tool_info["tool_name"],
                        duration_ms=tool_info.get("duration_ms"),
                        user_id=user_id,
                        client_source="codex",
                        ts=tool_info["ts"],
                    )
    elif event_name == CODEX_API_REQUEST_EVENT and service_name in CODEX_SERVICE_NAMES:
        _handle_codex_state_event(attrs, user_id=user_id)
    elif (
        event_name == "codex.websocket_request" and service_name in CODEX_SERVICE_NAMES
    ):
        _handle_codex_state_event(attrs, user_id=user_id)
    elif event_name == OPENCODE_EVENT and service_name == "opencode":
        _parse_opencode_record(
            record,
            attrs,
            usage_session_id or "",
            user_id=user_id,
            device_id=device_id,
        )
    elif event_name == KILO_EVENT and service_name in KILO_SERVICE_NAMES:
        _parse_opencode_record(
            record,
            attrs,
            usage_session_id or "",
            client_source="kilo",
            user_id=user_id,
            device_id=device_id,
        )
    elif (
        service_name not in KNOWN_SERVICE_NAMES
        and attrs
        and not os.path.exists(CODEX_DEBUG_FILE)
    ):
        # Capture first unknown-service payload with attributes to discover schema
        with open(CODEX_DEBUG_FILE, "w") as f:
            json.dump(
                {"service": service_name, "event": event_name, "record": record},
                f,
                indent=2,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="tokenage-otlp", lifespan=lifespan)


@app.get("/")
@app.get("/health")
async def health():
    return {"status": "ok", "service": app.title}


async def _read_capped_json(request: Request) -> dict:
    """Read the request body enforcing otlp.max_body_bytes then parse as JSON."""
    max_body = CONFIG.get("otlp", {}).get("max_body_bytes", 2_000_000)
    content_length = request.headers.get("content-length")
    try:
        declared_length = int(content_length) if content_length else None
    except ValueError:
        declared_length = None
    if declared_length is not None and declared_length > max_body:
        raise HTTPException(status_code=413, detail="request body too large")

    body_bytes = bytearray()
    async for chunk in request.stream():
        body_bytes.extend(chunk)
        if len(body_bytes) > max_body:
            raise HTTPException(status_code=413, detail="request body too large")
    return json.loads(body_bytes)


def _iter_log_records(body: dict) -> Iterator[tuple[dict, str, str]]:
    """Yield (record, service_name, session_id) for each ingestable record."""
    for resource_log in body.get("resourceLogs", []):
        resource = resource_log.get("resource", {})
        service_name = _resource_attr(resource, "service.name") or ""
        if service_name in RETIRED_SERVICE_NAMES:
            continue
        session_id = _resource_attr(resource, "session.id") or ""
        # Dump first unrecognised resource block to discover service name
        if service_name not in KNOWN_SERVICE_NAMES and not os.path.exists(
            CODEX_DEBUG_FILE + ".resource"
        ):
            with open(CODEX_DEBUG_FILE + ".resource", "w") as f:
                json.dump(
                    {"service_name": service_name, "resource": resource}, f, indent=2
                )
        for scope_log in resource_log.get("scopeLogs", []):
            for record in scope_log.get("logRecords", []):
                yield record, service_name, session_id


@app.post("/v1/logs")
async def receive_logs(request: Request):
    # Auth + rate limit + body cap — all before any record is parsed.
    user_id, device_id = await resolve_ingest_request_async(request)

    # Evict stale codex_state entries (older than 10 minutes)
    now = time.time_ns() // 1000
    stale_keys = [
        k for k, v in codex_state.items() if now - v.get("ts", 0) > secs_to_micros(600)
    ]
    for k in stale_keys:
        del codex_state[k]

    # Evict tool buffers whose last entry is older than 10 minutes (aborted
    # turns/dropped connections that never get matched to a usage event).
    now_ms = now // 1000
    for buf in (_claude_tool_buffer, _codex_tool_buffer, _opencode_tool_buffer):
        stale_buf_keys = [
            k
            for k, entries in buf.items()
            if entries and now_ms - entries[-1]["ts"] > 600_000
        ]
        for k in stale_buf_keys:
            del buf[k]

    for k, (_, _, usage_ts_ms) in list(_claude_last_usage.items()):
        if now_ms - usage_ts_ms > 600_000:
            del _claude_last_usage[k]

    PROMPT_LENGTH_TRACKER.evict_stale()

    body = await _read_capped_json(request)

    max_records = CONFIG.get("otlp", {}).get("max_records_per_request", 1_000)
    records = list(_iter_log_records(body))
    rejected = 0
    if len(records) > max_records:
        rejected = len(records) - max_records
        log.warning(
            "Rejected %d OTLP records past otlp.max_records_per_request=%d",
            rejected,
            max_records,
        )
        records = records[:max_records]

    for record, service_name, session_id in records:
        _parse_log_record(
            record,
            service_name,
            session_id,
            user_id=user_id,
            device_id=device_id,
        )
    if rejected:
        # OTLP partial success: still 200 so the exporter does not retry the same
        # oversized batch forever, but the dropped count is reported, not hidden.
        return {
            "partialSuccess": {
                "rejectedLogRecords": rejected,
                "errorMessage": "record count exceeds otlp.max_records_per_request",
            }
        }
    return {}


@app.post("/v1/metrics")
async def receive_metrics(request: Request):
    return {}


@app.post("/v1/traces")
async def receive_traces(request: Request):
    # Codex latency comes from the log stream; traces are not ingested.
    return {}
