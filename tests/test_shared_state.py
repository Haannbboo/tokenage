import hashlib

import pytest

from src.shared_state import QueueFull, SQLiteSharedState, partition_for


@pytest.fixture(params=[SQLiteSharedState])
def store(request, tmp_path):
    return request.param(tmp_path / "state.db")


def test_affinity_queue_and_ack(store):
    assert (
        partition_for("alice")
        == int.from_bytes(hashlib.sha256(b"alice").digest()[:8], "big") % 16
    )
    first = store.publish("alice", {"n": 1})
    second = store.publish("alice", {"n": 2})
    p = partition_for("alice")
    assert [e.position for e in store.read(p)] == [first, second]
    assert store.read(p, first)[0].payload == {"n": 2}
    assert not store.ack(p, second, "other")
    assert store.claim(p, "owner")
    assert store.ack(p, first, "owner")
    assert store.status()[p].depth == 1
    assert store.status()[p].oldest_pending is not None


def test_exclusive_lease_takeover(store, monkeypatch):
    other = SQLiteSharedState(store.path)
    now = 1000.0
    monkeypatch.setattr("src.shared_state.time.time", lambda: now)
    assert store.claim(0, "a")
    assert not other.claim(0, "b")
    assert not other.claim(0, "a")
    now += 11
    assert not store.renew(0, "a")
    assert other.claim(0, "b")
    assert not store.ack(0, "0", "a")
    store.release(0, "a")
    assert other.renew(0, "b")
    other.release(0, "b")
    assert store.claim(0, "a")


def test_kv_expiry_pop_once_and_cap(store, monkeypatch):
    other = SQLiteSharedState(store.path)
    now = 1000.0
    monkeypatch.setattr("src.shared_state.time.time", lambda: now)
    store.set("auth", "a", {"nonce": "test"}, 10)
    assert other.pop("auth", "a") == {"nonce": "test"}
    assert store.pop("auth", "a") is None
    store.set("auth", "a", {}, 10)
    now += 11
    assert other.get("auth", "a") is None
    assert other.pop("auth", "a") is None
    store.set("auth", "a", {}, 10, cap=1)
    store.set("auth", "b", {}, 10, cap=1)
    assert other.get("auth", "a") is None
    assert other.get("auth", "b") == {}


def test_sliding_rate_window_shared(store, monkeypatch):
    other = SQLiteSharedState(store.path)
    now = 1000.0
    monkeypatch.setattr("src.shared_state.time.time", lambda: now)
    assert store.rate_limit("a", 2) is None
    assert other.rate_limit("a", 2) is None
    assert store.rate_limit("a", 2) == 60
    now += 61
    assert other.rate_limit("a", 2) is None


def test_different_rate_windows_do_not_prune_each_other(store, monkeypatch):
    now = 1000.0
    monkeypatch.setattr("src.shared_state.time.time", lambda: now)
    assert store.rate_limit("long", 1, 3600) is None
    now += 100
    assert store.rate_limit("short", 1, 60) is None
    assert store.rate_limit("long", 1, 3600) == 3500


def test_partition_backpressure_boundary(store):
    p = partition_for("alice")
    with store._connection(True) as conn:
        conn.executemany(
            "INSERT INTO events(partition,payload,created) VALUES (?, '{}', 1)",
            [(p,)] * 10000,
        )
    store.publish("alice", {})  # specified backpressure is strictly above 10,000
    with pytest.raises(QueueFull):
        store.publish("alice", {})
    assert store.status()[p].depth == 10001


def test_store_factory_precedence(config_module, monkeypatch, tmp_path):
    from src.shared_state import get_shared_state

    default = get_shared_state()
    assert default.path.endswith("/.tokenage/state.db")
    config_module.CONFIG["queue"] = {"url": f"sqlite:///{tmp_path / 'configured.db'}"}
    assert get_shared_state().path == str(tmp_path / "configured.db")
    monkeypatch.setenv("TOKENAGE_QUEUE_URL", f"sqlite:///{tmp_path / 'env.db'}")
    assert get_shared_state().path == str(tmp_path / "env.db")
    monkeypatch.setenv("TOKENAGE_QUEUE_URL", "redis://secret@host/0")
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

    other = type(store)(store.path)
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
