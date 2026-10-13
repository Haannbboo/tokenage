"""One-off: attribute historical usage to devices by client IP, rebuild usage_daily.

Deleted in the PR that drops ``usage.client_ip`` (after that the IP map can
never be re-applied). Dry-run by default; ``--apply`` commits.

    uv run python -m src.scripts.backfill_device_attribution [--apply]

Everything runs in ONE transaction: back up usage_daily, set usage.device_id
from the hardcoded IP map, rebuild usage_daily from usage with the live
upsert's own column semantics, then compare per-day totals before/after and
roll back if any day differs. Rows whose IP is not in the map (or has none)
stay Unknown (device_id NULL).

Operator: run `tokenage server stop` (or stop ingest) first. A row written
between the rebuild's read and commit would be dropped from usage_daily.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, insert, inspect, select, text, update
from sqlalchemy.engine import Connection

from src.database import get_engine
from src.database.models import Device, Usage, UsageDaily
from src.database.usage import daily_values

BACKUP_TABLE = "bak_usage_daily_20261012"

# client_ip -> device_name. Confirmed by a human; anything else stays Unknown.
IP_TO_DEVICE_NAME = {
    "100.71.211.51": "unraid-vm",
    "127.0.0.1": "unraid-vm",
    "100.82.21.45": "unraid-vm",
    "100.88.94.9": "Hanbos-MacBook-Air.local",
}

# Counters summed per daily bucket (everything daily_values emits beyond the key).
_KEY = ("date", "provider", "model", "client_source", "user_id", "device_id")
_COST_SCALE = Decimal("0.00000001")  # Numeric(18, 8)


class BackfillError(RuntimeError):
    pass


def resolve_devices(connection: Connection) -> dict[str, list[Any]]:
    """device_name -> its Device rows; refuse if missing or ambiguous per user."""
    resolved: dict[str, list[Any]] = {}
    for name in sorted(set(IP_TO_DEVICE_NAME.values())):
        rows = list(
            connection.execute(
                select(Device.id, Device.user_id).where(Device.device_name == name)
            )
        )
        if not rows:
            raise BackfillError(f"no device named {name!r}; register it first")
        users = [row.user_id for row in rows]
        if len(users) != len(set(users)):
            raise BackfillError(f"device name {name!r} is ambiguous for a user")
        resolved[name] = rows
    return resolved


def attribute_usage(connection: Connection) -> dict[str, int]:
    """Set usage.device_id from the IP map; only rows still NULL. Per-IP counts."""
    devices = resolve_devices(connection)
    matched: dict[str, int] = {}
    for ip, name in IP_TO_DEVICE_NAME.items():
        total = 0
        for device in devices[name]:  # one device per user, so rows never collide
            result = connection.execute(
                update(Usage)
                .where(
                    Usage.client_ip == ip,
                    Usage.user_id == device.user_id,
                    Usage.device_id.is_(None),
                )
                .values(device_id=device.id)
            )
            total += result.rowcount
        matched[ip] = total
    return matched


def _day_totals(connection: Connection) -> dict[str, tuple[int, int, Decimal]]:
    totals: dict[str, list] = defaultdict(lambda: [0, 0, Decimal(0)])
    query = select(
        UsageDaily.date,
        func.sum(UsageDaily.request_count),
        func.sum(UsageDaily.total_tokens),
        func.sum(UsageDaily.total_cost_usd),
    ).group_by(UsageDaily.date)
    for date, requests, tokens, cost in connection.execute(query):
        totals[date] = [
            int(requests or 0),
            int(tokens or 0),
            Decimal(str(cost or 0)).quantize(_COST_SCALE),
        ]
    return {date: (r, t, c) for date, (r, t, c) in totals.items()}


def rebuild_daily(connection: Connection) -> int:
    """Replace usage_daily with the aggregate of usage; returns rows written."""
    buckets: dict[tuple, dict] = {}
    rows = connection.execute(select(Usage).execution_options(yield_per=5000))
    for row in rows:
        usage = Usage(**{c.name: getattr(row, c.name) for c in Usage.__table__.c})
        values = daily_values(usage)
        key = tuple(values[k] for k in _KEY)
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = values
            continue
        for column, value in values.items():
            if column not in _KEY:
                bucket[column] += value
    connection.execute(delete(UsageDaily))
    if buckets:
        connection.execute(insert(UsageDaily), list(buckets.values()))
    return len(buckets)


def run(engine, *, apply: bool, accept_drift: bool = False, out=print) -> int:
    """Returns 0 on success (committed or dry-run), raises BackfillError otherwise."""
    if BACKUP_TABLE in inspect(engine).get_table_names():
        raise BackfillError(f"backup table {BACKUP_TABLE} already exists; refusing")

    with engine.connect() as connection:
        transaction = connection.begin()
        if engine.dialect.name == "sqlite":
            # pysqlite starts transactions lazily (DML only); without this the
            # CREATE TABLE below would autocommit and survive a rollback.
            connection.exec_driver_sql("BEGIN")
        try:
            before = _day_totals(connection)
            connection.execute(
                text(f"CREATE TABLE {BACKUP_TABLE} AS SELECT * FROM usage_daily")
            )
            matched = attribute_usage(connection)
            daily_rows = rebuild_daily(connection)
            after = _day_totals(connection)

            unknown = connection.execute(
                select(func.count()).select_from(Usage).where(Usage.device_id.is_(None))
            ).scalar_one()
            total = connection.execute(
                select(func.count()).select_from(Usage)
            ).scalar_one()

            out("matched usage rows per IP:")
            for ip, count in matched.items():
                out(f"  {ip:<16} -> {IP_TO_DEVICE_NAME[ip]:<28} {count}")
            out(f"usage rows left Unknown: {unknown} of {total}")
            out(f"usage_daily rows: {len(before)} days -> {daily_rows} rows")
            out("per-day totals (requests, tokens, cost) before == after:")
            bad = []
            for date in sorted(set(before) | set(after)):
                b = before.get(date, (0, 0, Decimal(0)))
                a = after.get(date, (0, 0, Decimal(0)))
                flag = "ok" if a == b else "DIFF"
                out(f"  {date} {b[0]}/{b[1]}/{b[2]} -> {a[0]}/{a[1]}/{a[2]} {flag}")
                if a != b:
                    bad.append(date)
            if bad and not accept_drift:
                raise BackfillError(
                    f"day totals changed for {len(bad)} day(s): {bad}; usage_daily "
                    "had drifted from usage. Re-run with --accept-drift to take "
                    "usage as the source of truth (the backup keeps the old rows)."
                )
            if apply:
                transaction.commit()
                out(f"APPLIED. Backup: {BACKUP_TABLE}")
            else:
                transaction.rollback()
                out("DRY RUN: rolled back. Re-run with --apply to write.")
        except BaseException:
            if transaction.is_active:
                transaction.rollback()
            raise
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--apply", action="store_true", help="commit (default: dry run)"
    )
    parser.add_argument(
        "--accept-drift",
        action="store_true",
        help="allow day totals to change where usage_daily had drifted from usage",
    )
    parser.add_argument(
        "--db", default=None, help="DB path or URL (default: configured)"
    )
    args = parser.parse_args(argv)
    try:
        return run(
            get_engine(args.db), apply=args.apply, accept_drift=args.accept_drift
        )
    except BackfillError as exc:
        print(f"ABORTED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
