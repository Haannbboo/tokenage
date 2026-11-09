"""Usage report: one per model call, built by the client proxy, priced and
recorded by the server. Metadata only: no prompts, responses, IP or costs."""

from pydantic import BaseModel, Field, field_validator

# Reasoning effort levels tokenage stores; the frontend owns their order.
# OpenCode/Kilo send a variant name, which is only kept when it is one of these.
EFFORT_LEVELS = frozenset(
    ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "default")
)


def normalize_effort(value: object) -> str | None:
    """A known effort level (lowercased, stripped), else None."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if value in EFFORT_LEVELS else None


# Counts are non-negative ints; strings are bounded. Same field names as the
# server's record_usage so the two stay a straight mapping.
_Count = Field(default=None, ge=0)


class ToolCallReport(BaseModel):
    tool_use_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=256)


class UsageReport(BaseModel):
    ts: int | None = Field(default=None, ge=0)  # microseconds; server clock if unset
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    endpoint: str = Field(min_length=1, max_length=512)
    client_source: str | None = Field(default=None, max_length=64)
    prompt_tokens: int | None = _Count
    completion_tokens: int | None = _Count
    cached_tokens: int | None = _Count
    cache_creation_tokens: int | None = _Count
    reasoning_tokens: int | None = _Count
    tool_tokens: int | None = _Count
    total_tokens: int | None = _Count
    prompt_length: int | None = _Count
    latency_ms: int | None = _Count
    ttft_ms: int | None = _Count
    status: int | None = Field(default=None, ge=0, le=999)
    base_url: str | None = Field(default=None, max_length=512)
    tool_calls: list[ToolCallReport] = Field(default_factory=list, max_length=1000)
    effort: str | None = None

    # An unknown level from an older or newer client is dropped, not rejected.
    _effort = field_validator("effort", mode="before")(normalize_effort)
