import os
import time

import pytest

from src.shared_state import (
    QueueFull,
    RedisSharedState,
    SQLiteSharedState,
    partition_for,
)


@pytest.fixture(params=["sqlite", "redis"])
def store(request, tmp_path, redis_store_factory):
    if request.param == "redis":
        return redis_store_factory(RedisSharedState)
    return SQLiteSharedState(tmp_path / "state.db")


def other_store(store):
    if isinstance(store, SQLiteSharedState):
        return SQLiteSharedState(store.path)
    return RedisSharedState(os.environ["TOKENAGE_TEST_REDIS_URL"], prefix=store.prefix)


def test_wal_survives_short_operation_connections(store, tmp_path):
    if not isinstance(store, SQLiteSharedState):
        pytest.skip("SQLite WAL implementation detail")
    wal = tmp_path / "state.db-wal"
    shm = tmp_path / "state.db-shm"
    assert wal.exists()
    assert shm.exists()
    for i in range(3):
        store.set("test", str(i), {"n": i}, 60)
        assert wal.exists()
        assert shm.exists()


def test_affinity_queue_and_ack(store):
    assert partition_for("alice") == 15
    store.publish("alice", [{"n": 1}, {"n": 2}])
    p = partition_for("alice")
    first, second = store.read(p)
    assert first.payload == {"n": 1}
    assert second.payload == {"n": 2}
    store.ack(p, first.position)
    assert store.read(p)[0] == second
    assert store.status()[p].depth == 1
    assert store.status()[p].oldest_pending is not None


def test_exclusive_lease_takeover(store, monkeypatch):
    monkeypatch.setitem(store.claim.__globals__, "LEASE_SECONDS", 0.1)
    other = other_store(store)
    assert store.claim(0, "a")
    assert not other.claim(0, "b")
    assert not other.claim(0, "a")
    time.sleep(0.15)
    assert other.claim(0, "b")
    store.release(0, "a")
    assert other.status()[0].owner == "b"
    other.release(0, "b")
    assert store.claim(0, "a")


def test_kv_expiry_pop_once_and_cap(store):
    other = other_store(store)
    store.set("auth", "a", {"nonce": "test"}, 10)
    assert other.pop("auth", "a") == {"nonce": "test"}
    assert store.pop("auth", "a") is None
    store.set("auth", "a", {}, 0.02)
    time.sleep(0.05)
    assert other.get("auth", "a") is None
    assert other.pop("auth", "a") is None
    store.set("auth", "a", {}, 10, cap=1)
    store.set("auth", "b", {}, 10, cap=1)
    assert other.get("auth", "a") is None
    assert other.get("auth", "b") == {}


def test_sliding_rate_window_shared(store, monkeypatch):
    monkeypatch.setitem(store.rate_limit.__globals__, "RATE_LIMIT_WINDOW_SECONDS", 0.1)
    other = other_store(store)
    assert store.rate_limit("a", 2) is None
    assert other.rate_limit("a", 2) is None
    assert store.rate_limit("a", 2) == 1
    time.sleep(0.15)
    assert other.rate_limit("a", 2) is None


def test_partition_backpressure_boundary(store):
    from src.shared_state import MAX_PENDING_EVENTS

    store.publish("alice", [{}] * (MAX_PENDING_EVENTS - 1))
    with pytest.raises(QueueFull):
        store.publish("alice", [{"n": 1}, {"n": 2}])
    assert store.status()[partition_for("alice")].depth == MAX_PENDING_EVENTS - 1
    store.publish("alice", [{}])
    with pytest.raises(QueueFull):
        store.publish("alice", [{}])
    assert store.status()[partition_for("alice")].depth == MAX_PENDING_EVENTS


def test_store_factory_precedence(config_module, monkeypatch, tmp_path):
    from src.shared_state import get_shared_state

    default = get_shared_state()
    assert default.path.endswith("/.tokenage/state.db")
    monkeypatch.setitem(
        config_module.CONFIG,
        "queue",
        {"url": f"sqlite:///{tmp_path / 'configured.db'}"},
    )
    assert get_shared_state().path == str(tmp_path / "configured.db")
    monkeypatch.setenv("TOKENAGE_QUEUE_URL", f"sqlite:///{tmp_path / 'env.db'}")
    assert get_shared_state().path == str(tmp_path / "env.db")
    monkeypatch.setenv("TOKENAGE_QUEUE_URL", "unsupported://secret@host/0")
    with pytest.raises(ValueError, match="Unsupported queue URL scheme") as exc:
        get_shared_state()
    assert "secret" not in str(exc.value)


def test_server_config_uses_config_path(config_module, tmp_path, monkeypatch):
    from src.config.server_config import load_server_config

    path = tmp_path / "custom.yaml"
    path.write_text("server: {host: 0.0.0.0, api_port: 9876}\n")
    monkeypatch.setenv("TOKENAGE_CONFIG", str(path))
    assert load_server_config().api_port == 9876
    assert load_server_config().host == "0.0.0.0"
    monkeypatch.delenv("TOKENAGE_CONFIG")
    home = tmp_path / "other"
    home.mkdir()
    (home / "config.yaml").write_text("server: {api_port: 8765}\n")
    monkeypatch.setenv("TOKENAGE_HOME", str(home))
    assert load_server_config().api_port == 8765


def test_concurrent_claim_and_pop_have_one_winner(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    other = other_store(store)
    barrier = Barrier(2)

    def claim(pair):
        instance, owner = pair
        barrier.wait()
        return instance.claim(3, owner)

    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(claim, [(store, "a"), (other, "b")])) == 1
    store.set("codes", "once", {"nonce": "n"}, 60)
    barrier = Barrier(2)

    def pop(instance):
        barrier.wait()
        return instance.pop("codes", "once")

    with ThreadPoolExecutor(2) as pool:
        values = list(pool.map(pop, [store, other]))
    assert sum(value is not None for value in values) == 1


def test_signin_code_cross_instance_pop(api_module):
    from src.auth import google
    from src.shared_state import SQLiteSharedState, get_shared_state

    google.store_oauth_state("oauth", {"nonce": "n"})
    other = SQLiteSharedState(get_shared_state().path)
    assert other.pop(google.OAUTH_STATE_NAMESPACE, "oauth")["nonce"] == "n"
    assert google.pop_oauth_state("oauth") is None
    google.store_cli_code("cli", {"user_id": "u"})
    assert other.pop(google.CLI_CODE_NAMESPACE, "cli")["user_id"] == "u"
    assert google.pop_cli_code("cli") is None
    google.store_login_code("login")
    assert other.pop(google.LOGIN_CODE_NAMESPACE, "login") is not None
    assert google.pop_login_code("login") is None


def test_store_translates_sqlite_unavailability(tmp_path, monkeypatch):
    import sqlite3

    from src.shared_state import SQLiteSharedState, StoreUnavailable

    store = SQLiteSharedState(tmp_path / "unavailable.db")

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("SECRET DATABASE URL")

    monkeypatch.setattr("src.shared_state.sqlite3.connect", unavailable)
    with pytest.raises(StoreUnavailable) as exc:
        store.read(0)
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize("scheme", ["redis", "rediss"])
def test_factory_selects_redis_and_bounds_calls(config_module, monkeypatch, scheme):
    from src.shared_state import get_shared_state

    monkeypatch.setenv(
        "TOKENAGE_QUEUE_URL",
        f"{scheme}://user:secret@localhost:6379/0?socket_timeout=0&socket_connect_timeout=0",
    )
    monkeypatch.setitem(config_module.CONFIG, "queue", {"prefix": "custom:"})
    store = get_shared_state()
    assert store.prefix == "custom:"
    assert type(store).__name__ == "RedisSharedState"
    options = store._client.connection_pool.connection_kwargs
    assert options["socket_timeout"] == 5
    assert options["socket_connect_timeout"] == 5
    assert options["retry"].get_retries() == 0


def test_redis_errors_are_sanitized(monkeypatch, caplog):
    import redis

    from src.shared_state import RedisSharedState, StoreUnavailable

    store = RedisSharedState("redis://localhost:6379/0")

    def unavailable(*args, **kwargs):
        raise redis.ConnectionError("SECRET URL AND PAYLOAD")

    monkeypatch.setattr(store._client, "execute_command", unavailable)
    operations = [
        lambda: store.status(),
        lambda: store.publish("a", [{}]),
        lambda: store.read(0),
        lambda: store.ack(0, "1-0"),
        lambda: store.claim(0, "a"),
        lambda: store.release(0, "a"),
        lambda: store.get("a", "b"),
        lambda: store.set("a", "b", {}, 1),
        lambda: store.pop("a", "b"),
        lambda: store.rate_limit("a", 1),
    ]
    for operation in operations:
        with pytest.raises(StoreUnavailable) as exc:
            operation()
        assert "SECRET" not in str(exc.value)
        assert exc.value.__suppress_context__
    assert "shared store unavailable error=ConnectionError" in caplog.text
    assert "SECRET" not in caplog.text


def test_read_limit_and_complete_trim(store):
    store.publish("alice", [{"n": n} for n in range(501)])
    p = partition_for("alice")
    batch = store.read(p)
    assert len(batch) == 500
    assert store.read(p) == batch  # unacknowledged events are redelivered
    store.ack(p, batch[-1].position)
    remaining = store.read(p)
    assert [event.payload for event in remaining] == [{"n": 500}]
    store.ack(p, remaining[0].position)
    assert store.read(p) == []
    assert store.status()[p].depth == 0
    assert store.status()[p].oldest_pending is None
    store.publish("alice", [{"n": 501}])
    store.ack(p, remaining[0].position)  # repeated/stale ack preserves later data
    assert store.read(p)[0].payload == {"n": 501}


def test_kv_cap_preserves_updates_and_namespace_isolation(store):
    store.set("a:b", "c", 1, 60, cap=1)
    store.set("a", "b:c", 2, 60, cap=1)
    store.set("a:b", "c", 3, 60, cap=1)
    assert store.get("a:b", "c") == 3
    assert store.get("a", "b:c") == 2
    store.set("a:b", "later", 4, 120, cap=1)
    assert store.get("a:b", "c") is None
    assert store.get("a:b", "later") == 4


def test_expiry_uses_real_store_clock(store, monkeypatch):
    monkeypatch.setitem(store.claim.__globals__, "LEASE_SECONDS", 0.02)
    store.set("auth", "short", {}, 0.02)
    store.set("auth", "past", {}, -1)
    assert store.claim(0, "a")
    time.sleep(0.05)
    assert store.get("auth", "short") is None
    assert store.pop("auth", "past") is None
    assert store.status()[0].owner is None
    assert store.claim(0, "b")


def test_rate_limit_is_sliding_and_rejections_do_not_extend_it(store, monkeypatch):
    monkeypatch.setitem(store.rate_limit.__globals__, "RATE_LIMIT_WINDOW_SECONDS", 0.4)
    assert store.rate_limit("sliding", 2) is None
    time.sleep(0.25)
    assert store.rate_limit("sliding", 2) is None
    assert store.rate_limit("sliding", 2) == 1
    time.sleep(0.2)
    assert store.rate_limit("sliding", 2) is None
    assert store.rate_limit("sliding", 2) == 1


def test_concurrent_publish_respects_partition_limit(store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    monkeypatch.setitem(store.publish.__globals__, "MAX_PENDING_EVENTS", 1)
    other = other_store(store)
    barrier = Barrier(2)

    def publish(instance):
        barrier.wait()
        try:
            instance.publish("alice", [{}])
            return True
        except QueueFull:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(publish, [store, other])) == 1
    assert store.status()[partition_for("alice")].depth == 1


def test_concurrent_rate_limit_has_one_shared_budget(store):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: store.rate_limit("parallel", 2), range(8)))
    assert results.count(None) == 2
    assert all(result is None or 1 <= result <= 60 for result in results)


def test_unresponsive_redis_is_bounded():
    import socket
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from src.shared_state import RedisSharedState, StoreUnavailable

    release = Event()
    with socket.socket() as listener, ThreadPoolExecutor(1) as pool:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(2)

        def blackhole():
            with listener.accept()[0]:
                release.wait(2)

        future = pool.submit(blackhole)
        store = RedisSharedState(f"redis://127.0.0.1:{listener.getsockname()[1]}/0")
        # Exercise the real socket timeout without a five-second test delay.
        store._client.connection_pool.connection_kwargs["socket_timeout"] = 0.05
        start = time.monotonic()
        try:
            with pytest.raises(StoreUnavailable):
                store.read(0)
            assert time.monotonic() - start < 1
        finally:
            release.set()
            future.result(timeout=3)
            store._client.connection_pool.disconnect()


def test_redis_status_uses_one_call_and_local_clock(monkeypatch):
    store = RedisSharedState("redis://localhost:6379/0")
    calls = []

    def status_reply(*args):
        calls.append(args)
        return [["1000", "500000"], [[2, "990000-0", "worker"], [0, None, None]]]

    monkeypatch.setattr(store._client, "execute_command", status_reply)
    monkeypatch.setitem(store.status.__globals__["time"].__dict__, "time", lambda: 2000)
    status = store.status()
    assert len(calls) == 1
    assert calls[0][0] == "EVAL"
    assert status[0].depth == 2
    assert status[0].oldest_pending == 1989.5
    assert status[0].owner == "worker"
    assert status[1].oldest_pending is None
    assert status[1].owner is None


def test_redis_malformed_ack_is_unavailable():
    from src.shared_state import RedisSharedState, StoreUnavailable

    store = RedisSharedState("redis://localhost:6379/0")
    with pytest.raises(StoreUnavailable):
        store.ack(0, "not-a-position")


def test_redis_constructor_failure_logs_only_class(monkeypatch, caplog):
    from src.shared_state import RedisSharedState, StoreUnavailable

    def invalid(*args, **kwargs):
        raise ValueError("SECRET URL")

    monkeypatch.setattr("redis.ConnectionPool.from_url", invalid)
    with pytest.raises(StoreUnavailable) as exc:
        RedisSharedState("redis://localhost:6379/0")
    assert exc.value.__suppress_context__
    assert "shared store unavailable error=ValueError" in caplog.text
    assert "SECRET" not in caplog.text
