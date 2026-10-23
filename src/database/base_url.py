"""BaseUrl entity operations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..utils import normalize_provider_name
from .models import BaseUrl


def _apply_base_url_updates(
    row: BaseUrl,
    *,
    provider_name: str | None,
    source: str | None,
) -> bool:
    updated = False
    if provider_name and (not row.provider_name or row.provider_name == "unknown"):
        row.provider_name = provider_name
        updated = True
    if source and not row.source:
        row.source = source
        updated = True
    return updated


def get_or_create_base_url(
    base_url: str,
    *,
    session: Session,
    provider_name: str | None = None,
    source: str | None = None,
) -> int:
    """Resolve a stable `base_urls.id`, updating missing metadata when possible."""
    if provider_name is not None:
        provider_name = normalize_provider_name(provider_name)
    for attempt in range(2):
        row = session.scalar(select(BaseUrl).where(BaseUrl.base_url == base_url))
        if row is not None:
            _apply_base_url_updates(row, provider_name=provider_name, source=source)
            session.flush()
            return int(row.id)
        try:
            with session.begin_nested():
                row = BaseUrl(
                    base_url=base_url, provider_name=provider_name, source=source
                )
                session.add(row)
                session.flush()
            return int(row.id)
        except IntegrityError:
            if attempt:
                raise
    raise RuntimeError("Failed to resolve base_url id")


def resolve_base_url_id(
    *,
    base_url: str | None,
    session: Session,
    provider_name: str | None = None,
    source: str | None = None,
) -> int | None:
    if not base_url:
        return None

    return get_or_create_base_url(
        base_url,
        session=session,
        provider_name=provider_name,
        source=source,
    )
