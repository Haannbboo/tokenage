"""Pure helpers: usage and tool-call extraction, prompt length, client source."""

from __future__ import annotations

from typing import Any


def parse_client_source(user_agent: str) -> str | None:
    """Infer a normalized client source label from a proxy User-Agent string."""
    normalized = user_agent.strip().lower()
    if not normalized:
        return None

    first_token = normalized.split()[0]
    if first_token.split("/", 1)[0] in {"gemini", "gemini-cli"}:
        return None

    # Prefer stable agent/provider markers anywhere in the UA over the leading token.
    if "codex" in normalized:
        return "codex"
    if "claude" in normalized:
        return "claude"
    if "opencode" in normalized:
        return "opencode"

    # Fall back to the leading product/version token for unknown clients.
    if "/" in first_token:
        product = first_token.split("/", 1)[0]
        if product:
            return product

    return None


def extract_usage(usage: dict[str, Any]) -> dict[str, int]:
    """Normalize usage fields across chat completions, responses, and Anthropic formats."""
    prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens", 0)
    completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens", 0)

    input_details = (
        usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    )

    # Anthropic cache-write tokens (no OpenAI equivalent).
    cache_creation_tokens = usage.get("cache_creation_input_tokens") or 0

    # OpenAI's cached_tokens is a *subset* of prompt_tokens. Anthropic's
    # cache_read_input_tokens is a *disjoint* bucket -- input_tokens only counts
    # tokens after the last cache breakpoint -- so fold it into prompt_tokens to
    # match the "prompt_tokens >= cached_tokens" contract the server's cost
    # calculation expects.
    anthropic_cache_read = usage.get("cache_read_input_tokens")
    if anthropic_cache_read is not None:
        cached_tokens = anthropic_cache_read
        prompt_tokens = prompt_tokens + cached_tokens
    else:
        cached_tokens = input_details.get("cached_tokens") or 0

    total_tokens = usage.get("total_tokens") or (
        prompt_tokens + completion_tokens + cache_creation_tokens
    )

    output_details = (
        usage.get("output_tokens_details")
        or usage.get("completion_tokens_details")
        or {}
    )

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "reasoning_tokens": output_details.get("reasoning_tokens", 0),
        "cached_tokens": cached_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "total_tokens": total_tokens,
    }


def find_stream_usage(message: dict[str, Any]) -> dict[str, Any] | None:
    """Return the raw usage dict from a streaming chunk, if present.

    Checks top-level usage (chat/completions deltas, Anthropic message_delta),
    then response.usage (OpenAI responses API) and message.usage (Anthropic
    message_start, which carries input/cache tokens the later message_delta
    doesn't repeat).
    """
    if usage := message.get("usage"):
        return usage

    response_payload = message.get("response") or {}
    if usage := response_payload.get("usage"):
        return usage

    inner_message = message.get("message") or {}
    if usage := inner_message.get("usage"):
        return usage

    return None


def extract_tool_calls(response: dict[str, Any]) -> list[dict[str, str]]:
    """Extract tool calls from a full response body.

    Supports OpenAI chat/completions, Anthropic messages, and OpenAI responses.
    Returns list of {"tool_use_id": ..., "tool_name": ...}.
    """
    results: list[dict[str, str]] = []

    # OpenAI chat/completions
    for choice in response.get("choices") or []:
        msg = choice.get("message") or {}
        for tc in msg.get("tool_calls") or []:
            tool_use_id = tc.get("id") or ""
            fn = tc.get("function") or {}
            tool_name = fn.get("name") or ""
            if tool_use_id:
                results.append({"tool_use_id": tool_use_id, "tool_name": tool_name})

    # Anthropic messages
    content = response.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "tool_use":
                tool_use_id = part.get("id") or ""
                tool_name = part.get("name") or ""
                if tool_use_id:
                    results.append({"tool_use_id": tool_use_id, "tool_name": tool_name})

    # OpenAI responses API
    output = response.get("output")
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict) and item.get("type") == "function_call":
                tool_use_id = item.get("call_id") or ""
                tool_name = item.get("name") or ""
                if tool_use_id:
                    results.append({"tool_use_id": tool_use_id, "tool_name": tool_name})

    return results


class StreamToolCallAccumulator:
    """Accumulate tool call info from streaming delta chunks."""

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, str]] = {}

    def accumulate(self, payload: dict[str, Any]) -> None:
        # OpenAI chat/completions deltas
        for choice in payload.get("choices") or []:
            delta = choice.get("delta") or {}
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                entry = self._calls.setdefault(
                    idx, {"tool_use_id": "", "tool_name": ""}
                )
                if tc.get("id"):
                    entry["tool_use_id"] += tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    entry["tool_name"] += fn["name"]

        # Anthropic: content_block_start
        block = payload.get("content_block") or {}
        if block.get("type") == "tool_use":
            idx = payload.get("index", 0)
            entry = self._calls.setdefault(idx, {"tool_use_id": "", "tool_name": ""})
            if block.get("id"):
                entry["tool_use_id"] = block["id"]
            if block.get("name"):
                entry["tool_name"] = block["name"]

        # OpenAI responses: output_item.added
        resp = payload.get("response") or {}
        item = resp.get("output_item") or payload.get("item") or {}
        if item.get("type") == "function_call":
            idx = resp.get("output_index") or payload.get("output_index") or 0
            entry = self._calls.setdefault(idx, {"tool_use_id": "", "tool_name": ""})
            if item.get("call_id"):
                entry["tool_use_id"] = item["call_id"]
            if item.get("name"):
                entry["tool_name"] = item["name"]

        # OpenAI responses: output_item.delta
        delta = resp.get("delta") or payload.get("delta") or {}
        if delta.get("type") == "function_call_delta":
            idx = resp.get("output_index") or payload.get("output_index") or 0
            entry = self._calls.setdefault(idx, {"tool_use_id": "", "tool_name": ""})
            if delta.get("call_id"):
                entry["tool_use_id"] += delta["call_id"]
            if delta.get("name"):
                entry["tool_name"] += delta["name"]

    def get_tool_calls(self) -> list[dict[str, str]]:
        return [
            self._calls[idx]
            for idx in sorted(self._calls)
            if self._calls[idx]["tool_use_id"]
        ]


def _content_text_length(content: Any) -> int:
    """Return character count of a message content value."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                total += len(part["text"])
        return total
    return 0


def compute_prompt_length(body_json: dict[str, Any]) -> int:
    """Compute new user prompt character count from a request body.

    Only counts ``role=user`` messages that appear *after* the last
    ``role=assistant`` message, so that tool-call continuation turns
    (which replay the full history but add no new human input) report 0.
    Supports chat/completions (``messages``) and responses (``input``) formats.
    """
    items = body_json.get("messages", []) or body_json.get("input", []) or []
    # Find index of last assistant message; only count user messages after it.
    last_assistant = -1
    for i, item in enumerate(items):
        if isinstance(item, dict) and item.get("role") == "assistant":
            last_assistant = i

    total = 0
    for item in items[last_assistant + 1 :]:
        if isinstance(item, dict) and item.get("role") == "user":
            total += _content_text_length(item.get("content"))
    return total
