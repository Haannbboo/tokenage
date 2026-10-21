"""ORM model classes and validation constants for tokenage."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


metadata = Base.metadata


class QueuePosition(Base):
    __tablename__ = "queue_positions"

    partition: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[str] = mapped_column(Text, nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid4())
    )
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "installation_hash", name="uq_devices_installation"
        ),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    installation_hash: Mapped[str] = mapped_column(String, nullable=False)
    device_name: Mapped[str] = mapped_column(String, nullable=False)
    client_version: Mapped[str | None] = mapped_column(String, nullable=True)
    client_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_seen_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revoked_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_reported_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    device_name: Mapped[str | None] = mapped_column(String, nullable=True)
    device_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("devices.id"), nullable=True, index=True
    )
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_used_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revoked_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class BaseUrl(Base):
    __tablename__ = "base_urls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    base_url: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    provider_name: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    usages: Mapped[list[Usage]] = relationship(back_populates="base_url")


class PriceSnapshot(Base):
    """Pricing in effect for a model on a given UTC date.

    Written lazily at record time so the cost split of any usage row can be
    recomputed exactly later, even after config/LiteLLM prices change.
    ``source`` records where the pricing came from ("litellm", "server" for config.yaml, or a
    future source like "openrouter").
    """

    __tablename__ = "price_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "date",
            "model",
            "source",
            "rates_hash",
            name="uq_price_snapshots_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    rates_hash: Mapped[str] = mapped_column(String, nullable=False)
    rates_json: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class UserModelPrice(Base):
    """A user's own flat price for a model, USD per million tokens.

    Beats every shared pricing source for that user's rows.
    """

    __tablename__ = "user_model_prices"
    __table_args__ = (
        UniqueConstraint("user_id", "model", name="uq_user_model_prices"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    input: Mapped[float] = mapped_column(Float, nullable=False)
    output: Mapped[float] = mapped_column(Float, nullable=False)
    cache_read: Mapped[float | None] = mapped_column(Float, nullable=True)
    cache_write: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Usage(Base):
    __tablename__ = "usage"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    client_source: Mapped[str | None] = mapped_column(String, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    endpoint: Mapped[str] = mapped_column(String, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_length: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cache_creation_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=0, server_default=text("0")
    )
    output_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=0, server_default=text("0")
    )
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # NULL = Unknown (historical rows only; the live write path requires it).
    device_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    base_url_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("base_urls.id"), nullable=True
    )
    price_snapshot_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    base_url: Mapped[BaseUrl | None] = relationship(back_populates="usages")


class UsageHourly(Base):
    __tablename__ = "usage_hourly"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    # UTC hour start, epoch seconds (usage.ts // 3_600_000_000 * 3600).
    hour: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    client_source: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("''")
    )
    device_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    reasoning_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    cached_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    tool_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    cache_creation_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    prompt_length: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    input_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, server_default=text("0")
    )
    output_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, server_default=text("0")
    )
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, server_default=text("0")
    )
    successful_requests: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    failed_requests: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    latency_sum_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    status_429: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    status_4xx: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    status_5xx: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    status_unknown: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )


Index(
    "uq_usage_hourly",
    UsageHourly.user_id,
    UsageHourly.hour,
    UsageHourly.provider,
    UsageHourly.model,
    UsageHourly.client_source,
    UsageHourly.device_id,
    unique=True,
)


class SessionRecord(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    client_source: Mapped[str | None] = mapped_column(String, nullable=True)
    started: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ended: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    successful_requests: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    failed_requests: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    cached_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    cache_creation_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, server_default=text("0")
    )
    latency_sum_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    avg_latency_ms: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    avg_ttft_ms: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    primary_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_model: Mapped[str | None] = mapped_column(String, nullable=True)
    providers_json: Mapped[str | None] = mapped_column(String, nullable=True)
    models_json: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_calls_json: Mapped[str | None] = mapped_column(
        String, nullable=True, server_default=text("'{}'")
    )
    last_usage_id: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    # Evaluation columns (NULL = not evaluated)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    task_title: Mapped[str | None] = mapped_column(String, nullable=True)
    task_title_zh: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_json: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    evaluated_at: Mapped[str | None] = mapped_column(String, nullable=True)
    project: Mapped[str | None] = mapped_column(String, nullable=True)


Index("ix_sessions_started_desc", SessionRecord.started.desc())
Index(
    "ix_sessions_client_source_started_desc",
    SessionRecord.client_source,
    SessionRecord.started.desc(),
)

VALID_OUTCOMES = {"solved", "partial", "failed", "stuck", "no_op", "unknown"}
VALID_SOURCES = {"manual", "heuristic", "llm"}


class EvaluationJob(Base):
    """One client-reported evaluation run. Metadata only: the result itself
    lives on the session row, so a job never stores a result snapshot."""

    __tablename__ = "evaluation_jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), nullable=False)
    # NULL only for legacy rows written before evaluation moved to the client.
    device_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    client_source: Mapped[str | None] = mapped_column(String, nullable=True)
    trigger: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'auto'")
    )
    evaluator_type: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'codex'")
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class ToolCall(Base):
    __tablename__ = "tool_calls"

    tool_use_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), nullable=False, index=True
    )
    usage_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("usage.id"), nullable=True, index=True
    )
    session_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    tool_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    client_source: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
