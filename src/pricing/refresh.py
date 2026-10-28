"""Background source refresh and repair of usage recorded before prices arrived."""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from .costs import MISS_TIMES, MISSES_LOCK, resolve_cost_match
from .maps import MAPS_LOCK, refresh_pricing_maps
from .sources.base import cache_is_fresh, cache_path
from .sources.registry import build_sources, fetch_sources

log = logging.getLogger(__name__)
# ponytail: check cache metadata once a minute; keep work off the ingest path.
TICK_SECONDS = 60
# ponytail: bound forced downloads per process, including manual requests.
FORCE_INTERVAL = 15 * 60
# ponytail: stop forcing downloads for local or never-priced models after an hour.
GIVE_UP_SECONDS = 60 * 60
# ponytail: bound miss memory even for junk or never-priced model names.
MISS_RETENTION_SECONDS = 24 * 60 * 60
# ponytail: recover recent misses lost across process restarts without scanning history.
BACKLOG_SECONDS = 7 * 24 * 60 * 60
# Hold across download and swap so concurrent fetches cannot install out of order.
FETCH_LOCK = threading.Lock()
_last_download_attempt: float | None = None
_last_manual_download_attempt: float | None = None


def refresh_live_maps(config, *, force=False, manual=False):
    """Refresh live maps with separate download floors for manual requests."""
    global _last_download_attempt, _last_manual_download_attempt
    if not config.get("pricing", {}).get("auto_fetch", True):
        return False
    with FETCH_LOCK:
        # Reading a sibling's fresh cache requires no download or floor slot.
        if force or _cache_state(config)[1]:
            now = time.monotonic()
            last_attempt = (
                _last_manual_download_attempt if manual else _last_download_attempt
            )
            if last_attempt is not None and now - last_attempt < FORCE_INTERVAL:
                if not _cache_state(config)[1]:
                    refresh_pricing_maps(config, fetch_sources(config))
                    return True
                return False
            # Reserve before I/O so failures also back off.
            if manual:
                _last_manual_download_attempt = now
            else:
                _last_download_attempt = now
        fetched = fetch_sources(config, force=force)
        refresh_pricing_maps(config, fetched)
        return True


def _cache_state(config):
    state = []
    due = False
    for source in build_sources(config):
        # Adapters use their class-level name for caches; instance names are provenance.
        name = type(source).name
        path = cache_path(name)
        try:
            stat = path.stat()
            state.append((name, stat.st_mtime_ns, stat.st_size))
        except OSError:
            state.append((name, None, None))
        due |= not cache_is_fresh(name, source.ttl_seconds)
    return tuple(state), due


class PricingRefresh:
    def __init__(self):
        from src.config.app import CONFIG

        self.stop_event = threading.Event()
        self.worker = None
        self.cache_state = _cache_state(CONFIG)[0]
        self.pending_backlog = True
        self.last_backlog_attempt: float | None = None

    def tick(self):
        from src.config.app import CONFIG
        from src.database import load_model_price_overrides, reprice_estimated_rows
        from src.database.usage import users_with_unpriced_rows

        now = time.monotonic()
        state, due = _cache_state(CONFIG)
        with MISSES_LOCK:
            for pair, first_seen in tuple(MISS_TIMES.items()):
                if now - first_seen >= MISS_RETENTION_SECONDS:
                    del MISS_TIMES[pair]
            pairs = {
                pair
                for pair, first_seen in MISS_TIMES.items()
                if now - first_seen < GIVE_UP_SECONDS
            }
        overrides_by_user = {}

        def resolved(pair):
            user_id, model = pair
            if user_id not in overrides_by_user:
                try:
                    overrides_by_user[user_id] = load_model_price_overrides(
                        user_id=user_id
                    )
                except Exception as exc:
                    overrides_by_user[user_id] = {}
                    log.warning("Pricing overrides load failed: %s", type(exc).__name__)
            overrides = overrides_by_user[user_id]
            if resolve_cost_match(model, overrides, dict.fromkeys(overrides, "user")):
                return True
            with MAPS_LOCK:
                return resolve_cost_match(model) is not None

        unresolved = any(not resolved(pair) for pair in pairs)
        refreshed = False
        if due:
            refreshed = refresh_live_maps(CONFIG)
            if refreshed:
                self.pending_backlog = True
        elif unresolved:
            refreshed = refresh_live_maps(CONFIG, force=True)
        elif state != self.cache_state:
            # Pick up a sibling's cache rewrite without scheduling a backlog pass.
            refreshed = refresh_live_maps(CONFIG)
        if refreshed:
            self.cache_state = _cache_state(CONFIG)[0]
        if self.pending_backlog and (
            self.last_backlog_attempt is None
            or now - self.last_backlog_attempt >= FORCE_INTERVAL
        ):
            since = datetime.fromtimestamp(
                time.time() - BACKLOG_SECONDS, UTC
            ).isoformat()
            try:
                users = users_with_unpriced_rows(since=since)
            except Exception as exc:
                users = []
                failed = True
                log.warning("Pricing backlog reprice failed: %s", type(exc).__name__)
            else:
                failed = False
            for user_id in users:
                try:
                    reprice_estimated_rows(
                        user_id=user_id, since=since, zero_cost_only=True
                    )
                except Exception as exc:
                    failed = True
                    log.warning(
                        "Pricing backlog reprice failed: %s", type(exc).__name__
                    )
            if failed:
                self.last_backlog_attempt = now
            else:
                self.last_backlog_attempt = None
                self.pending_backlog = False
        # Repair resolved misses each tick, including after a manual refresh.
        # Keep failed repairs for retry, but bound all entries to the window.
        for pair in pairs:
            if not resolved(pair):
                continue
            try:
                summary = reprice_estimated_rows(
                    user_id=pair[0], model=pair[1], zero_cost_only=True
                )
                # Another worker may already have repaired every row. A rare
                # in-flight row is recovered by the TTL backlog pass.
                if summary["repriced"] > 0 or summary["candidates"] == 0:
                    with MISSES_LOCK:
                        MISS_TIMES.pop(pair, None)
                else:
                    log.warning("Pricing miss reprice skipped all candidates")
            except Exception as exc:
                log.warning("Pricing miss reprice failed: %s", type(exc).__name__)

    def _run(self):
        if self.stop_event.wait(random.uniform(0, 30)):
            return
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                log.warning("Pricing refresh failed: %s", type(exc).__name__)
            self.stop_event.wait(TICK_SECONDS)

    def start(self):
        from src.config.app import CONFIG

        if not CONFIG.get("pricing", {}).get("auto_fetch", True):
            return
        self.worker = threading.Thread(
            target=self._run, name="tokenage-pricing", daemon=True
        )
        self.worker.start()

    def close(self):
        self.stop_event.set()
        if self.worker is not None:
            self.worker.join(3)


@asynccontextmanager
async def pricing_refresh_lifespan():
    refresher = PricingRefresh()
    refresher.start()
    try:
        yield refresher
    finally:
        await asyncio.to_thread(refresher.close)
