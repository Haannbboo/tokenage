"""User and auth-token operations.

Tokens are opaque (`tokenage_<kind>_<hex>`), shown once at mint time, and stored
only as sha256 hashes.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from uuid import uuid4

from sqlalchemy import or_, select, text
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..database.engine import get_engine
from ..database.models import AuthToken, Device, User

TOKEN_KINDS = ("cli", "ingest", "web")
LOCAL_OWNER_EMAIL = "owner@localhost"
AUTH_STATEMENT_TIMEOUT_MILLISECONDS = 10_000
ACTIVITY_UPDATE_INTERVAL_MICROS = 30 * 1_000_000


def _now_micros() -> int:
    return time.time_ns() // 1000


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _set_auth_statement_timeout(session: Session) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text(
                "SET LOCAL statement_timeout = "
                f"'{AUTH_STATEMENT_TIMEOUT_MILLISECONDS}ms'"
            )
        )


def get_or_create_user(email: str, db_path: str | None = None) -> User:
    """Return the user with this email, creating the row if new.

    Concurrent first logins can race the unique email index; the loser
    retries the lookup in a fresh transaction (same pattern as
    get_or_create_base_url).
    """
    email = email.strip().lower()
    if not email:
        raise ValueError("email must not be empty")
    engine = get_engine(db_path)
    for attempt in range(2):
        with Session(engine, expire_on_commit=False) as session:
            user = session.execute(
                select(User).where(User.email == email)
            ).scalar_one_or_none()
            if user is not None:
                return user
            user = User(email=email, created_at=_now_micros())
            session.add(user)
            try:
                session.commit()
                return user
            except IntegrityError:
                # On PostgreSQL, the transaction is aborted after an
                # IntegrityError; retry in a fresh transaction to observe
                # the concurrently created row.
                session.rollback()
                if attempt == 0:
                    continue
                raise
    raise RuntimeError(f"Failed to resolve user for {email}")


def get_local_owner(db_path: str | None = None) -> User:
    """The single built-in user of the local provider, created on demand."""
    return get_or_create_user(LOCAL_OWNER_EMAIL, db_path)


def get_user_by_id(user_id: str, db_path: str | None = None) -> User | None:
    engine = get_engine(db_path)
    with Session(engine, expire_on_commit=False) as session:
        user = session.execute(
            select(User).where(User.id == user_id)
        ).scalar_one_or_none()
        session.expunge_all()
        return user


def mint_token(
    email: str,
    kind: str = "cli",
    device_name: str | None = None,
    db_path: str | None = None,
) -> tuple[str, User]:
    """Mint a token for the user with this email, creating the user if new.

    Returns the plaintext token (the only time it is available) and the user.
    """
    if kind not in TOKEN_KINDS:
        raise ValueError(
            f"invalid token kind: {kind!r} (expected one of {TOKEN_KINDS})"
        )
    token = f"tokenage_{kind}_{secrets.token_hex(24)}"
    user = get_or_create_user(email, db_path)
    engine = get_engine(db_path)
    now = _now_micros()
    with Session(engine, expire_on_commit=False) as session:
        device_id = None
        if kind == "ingest":
            # Ingest tokens are device-bound; an unbound one resolves to nothing.
            device = Device(
                user_id=user.id,
                installation_hash=hash_token(token),
                device_name=device_name or "token",
                created_at=now,
                last_seen_at=now,
            )
            session.add(device)
            session.flush()
            device_id = device.id
        session.add(
            AuthToken(
                user_id=user.id,
                kind=kind,
                device_name=device_name,
                device_id=device_id,
                token_hash=hash_token(token),
                created_at=now,
            )
        )
        session.commit()
    return token, user


def resolve_token(
    token: str, db_path: str | None = None
) -> tuple[User, AuthToken] | None:
    """Return the token's user and the token row, or None for unknown/revoked.

    Validation DB errors propagate (fail closed). Activity timestamps are written
    at most once per 30 seconds, so they can read up to ~60 seconds stale; write
    failures are best effort for every caller, including CLI/web.
    """
    engine = get_engine(db_path)
    activity_started = False
    try:
        with Session(engine, expire_on_commit=False) as session:
            _set_auth_statement_timeout(session)
            # Only the device columns the checks need: the row also carries the
            # client status report, which this hot path must not fetch.
            row = session.execute(
                select(AuthToken, User, Device.id, Device.revoked_at, Device.user_id)
                .join(User, AuthToken.user_id == User.id)
                .outerjoin(Device, AuthToken.device_id == Device.id)
                .where(AuthToken.token_hash == hash_token(token))
            ).first()
            if row is None:
                return None
            auth_token, user, device_id, device_revoked_at, device_user_id = row
            if auth_token.revoked_at is not None:
                return None
            # An ingest token not bound to a device is a pre-device-binding leftover.
            if auth_token.kind == "ingest" and auth_token.device_id is None:
                return None
            if auth_token.device_id is not None and (
                device_id is None
                or device_revoked_at is not None
                or device_user_id != user.id
            ):
                return None
            # Detach before bookkeeping: rollback after a failed refresh must not
            # expire the already validated identity or trigger another SELECT.
            session.expunge_all()
            now = _now_micros()
            cutoff = now - ACTIVITY_UPDATE_INTERVAL_MICROS
            if auth_token.last_used_at is None or auth_token.last_used_at < cutoff:
                activity_started = True
                # Device before token: mint_device_tokens locks in that order.
                if auth_token.device_id is not None:
                    session.execute(
                        sa_update(Device)
                        .where(
                            Device.id == auth_token.device_id,
                            Device.revoked_at.is_(None),
                            or_(
                                Device.last_seen_at.is_(None),
                                Device.last_seen_at < cutoff,
                            ),
                        )
                        .values(last_seen_at=now)
                    )
                session.execute(
                    sa_update(AuthToken)
                    .where(
                        AuthToken.id == auth_token.id,
                        AuthToken.revoked_at.is_(None),
                        or_(
                            AuthToken.last_used_at.is_(None),
                            AuthToken.last_used_at < cutoff,
                        ),
                    )
                    .values(last_used_at=now)
                )
                session.commit()
    except SQLAlchemyError:
        if not activity_started:
            raise
        # The session context also handles rollback/close failures here. The
        # detached identity remains usable even if bookkeeping loses its DB.
        logging.getLogger(__name__).warning(
            "Failed to update last_used_at for token id=%s", auth_token.id
        )
    return user, auth_token


def update_user_name(
    user_id: str, name: str | None, db_path: str | None = None
) -> None:
    """Backfill a user's name from an OAuth profile, only while it is unset.

    A name already set (first login happened) is never overwritten.
    """
    engine = get_engine(db_path)
    with Session(engine, expire_on_commit=False) as session:
        session.execute(
            sa_update(User)
            .where(User.id == user_id, User.name.is_(None))
            .values(name=name)
        )
        session.commit()


def revoke_token(token_id: str, user_id: str, db_path: str | None = None) -> bool:
    """Revoke a token owned by this user. Returns False if absent/already revoked."""
    engine = get_engine(db_path)
    with Session(engine, expire_on_commit=False) as session:
        result = session.execute(
            sa_update(AuthToken)
            .where(
                AuthToken.id == token_id,
                AuthToken.user_id == user_id,
                AuthToken.revoked_at.is_(None),
            )
            .values(revoked_at=_now_micros())
        )
        changed = result.rowcount > 0  # type: ignore[attr-defined]
        session.commit()
    return changed


def list_user_tokens(user_id: str, db_path: str | None = None) -> list[AuthToken]:
    """Return the user's active (non-revoked) tokens, newest first."""
    engine = get_engine(db_path)
    with Session(engine, expire_on_commit=False) as session:
        rows = (
            session.execute(
                select(AuthToken)
                .where(AuthToken.user_id == user_id, AuthToken.revoked_at.is_(None))
                .order_by(AuthToken.created_at.desc())
            )
            .scalars()
            .all()
        )
        session.expunge_all()
        return list(rows)


def list_user_devices(
    user_id: str, db_path: str | None = None, *, include_revoked: bool = False
) -> list[Device]:
    engine = get_engine(db_path)
    with Session(engine, expire_on_commit=False) as session:
        _set_auth_statement_timeout(session)
        query = select(Device).where(Device.user_id == user_id)
        if not include_revoked:
            query = query.where(Device.revoked_at.is_(None))
        rows = session.execute(query.order_by(Device.created_at.desc())).scalars().all()
        session.expunge_all()
        return list(rows)


def mint_device_tokens(
    user_id: str,
    installation_key: str,
    device_name: str,
    *,
    client_version: str | None = None,
    client_commit: str | None = None,
    db_path: str | None = None,
) -> tuple[str, str, Device]:
    """Create or rotate a machine's CLI and ingestion credentials atomically."""
    installation_hash = hash_token(installation_key)
    engine = get_engine(db_path)
    for attempt in range(2):
        now = _now_micros()
        cli_token = f"tokenage_cli_{secrets.token_hex(24)}"
        ingest_token = f"tokenage_ingest_{secrets.token_hex(24)}"
        try:
            with Session(engine, expire_on_commit=False) as session:
                _set_auth_statement_timeout(session)
                device = session.execute(
                    select(Device)
                    .where(
                        Device.user_id == user_id,
                        Device.installation_hash == installation_hash,
                    )
                    # Row lock on servers that honor it (PostgreSQL). SQLite
                    # drops it; there the unique index plus the IntegrityError
                    # retry below is what keeps two first logins from racing.
                    .with_for_update()
                ).scalar_one_or_none()
                if device is None:
                    # A supplied device_id is never proof of ownership. The
                    # installation secret, scoped to the user, is the key.
                    device = Device(
                        id=str(uuid4()),
                        user_id=user_id,
                        installation_hash=installation_hash,
                        device_name=device_name,
                        client_version=client_version,
                        client_commit=client_commit,
                        created_at=now,
                        last_seen_at=now,
                    )
                    session.add(device)
                    session.flush()
                else:
                    device.device_name = device_name
                    # A revoked machine comes back only through a fresh browser
                    # approval: the installation key is not a credential, so the
                    # code + PKCE exchange is what re-authorizes it.
                    device.revoked_at = None
                    device.last_seen_at = now
                    if client_version is not None:
                        device.client_version = client_version
                    if client_commit is not None:
                        device.client_commit = client_commit
                session.execute(
                    sa_update(AuthToken)
                    .where(
                        AuthToken.user_id == user_id,
                        AuthToken.device_id == device.id,
                        AuthToken.kind.in_(("cli", "ingest")),
                        AuthToken.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )
                session.add_all(
                    [
                        AuthToken(
                            user_id=user_id,
                            device_id=device.id,
                            kind=kind,
                            device_name=device_name,
                            token_hash=hash_token(token),
                            created_at=now,
                        )
                        for kind, token in (
                            ("cli", cli_token),
                            ("ingest", ingest_token),
                        )
                    ]
                )
                session.commit()
                return cli_token, ingest_token, device
        except IntegrityError:
            # Two first exchanges for one installation can race at the unique
            # index. Retry in a fresh transaction and rotate the winner's pair.
            if attempt == 1:
                raise
    raise RuntimeError("device token rotation failed")


def revoke_device(device_id: str, user_id: str, db_path: str | None = None) -> bool:
    """Revoke a machine and all its credentials in one transaction."""
    engine = get_engine(db_path)
    now = _now_micros()
    with Session(engine, expire_on_commit=False) as session:
        _set_auth_statement_timeout(session)
        result = session.execute(
            sa_update(Device)
            .where(
                Device.id == device_id,
                Device.user_id == user_id,
                Device.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        changed = result.rowcount > 0  # type: ignore[attr-defined]
        if changed:
            session.execute(
                sa_update(AuthToken)
                .where(
                    AuthToken.device_id == device_id,
                    AuthToken.user_id == user_id,
                    AuthToken.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
        session.commit()
    return changed


def set_device_status(
    device_id: str, status_json: str, db_path: str | None = None
) -> None:
    """Store the latest status report on the device row."""
    with Session(get_engine(db_path)) as session:
        session.execute(
            sa_update(Device)
            .where(Device.id == device_id)
            .values(status_json=status_json, status_reported_at=_now_micros())
        )
        session.commit()
