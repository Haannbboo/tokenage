"""Real interpreter processes share SQLite/Redis state, correlation and revocation.

Children import production code from scratch, without pytest's in-process SQLite
speed patches or inherited engines. Commands and responses travel over pipes;
credentials never appear in logs or process arguments.
"""

import json
import os
import selectors
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager

import pytest
import yaml

WORKER = r"""
import json
import os
import sys
from fastapi.testclient import TestClient
from sqlalchemy import text
from src.api import app
from src.auth import google
from src.auth.tokens import mint_token, resolve_token, revoke_token
from src.database import get_engine, init_db
from src.ingest_queue import Consumer
from src.shared_state import get_shared_state

consumer = None
client = TestClient(app)
print(json.dumps("ready"), flush=True)
for line in sys.stdin:
    command = json.loads(line)
    action = command[0]
    if action == "init":
        init_db()
        result = True
    elif action == "cold-start":
        from src.pricing.sources.base import save_cache_json, load_cache_json
        get_shared_state()
        init_db()
        for _ in range(10):
            save_cache_json("startup", {"writer": os.getpid(), "values": [os.getpid()] * 1000})
            snapshot = load_cache_json("startup")
            assert snapshot is not None
            assert snapshot["values"] == [snapshot["writer"]] * 1000
        result = True
    elif action == "hold-write":
        held_connection = get_engine().connect()
        held_connection.exec_driver_sql("BEGIN IMMEDIATE")
        result = True
    elif action == "release-write":
        held_connection.rollback()
        held_connection.close()
        result = True
    elif action == "mint":
        token, user = mint_token("multiprocess@example.com", kind="ingest")
        _, auth = resolve_token(token)
        result = [token, user.id, auth.device_id, auth.id]
    elif action == "publish":
        get_shared_state().publish(command[1], command[2])
        result = True
    elif action == "drain":
        if consumer is None:
            consumer = Consumer()
        result = consumer.drain()
    elif action == "state-store":
        google.store_oauth_state("state", {"nonce": "test"})
        google.store_cli_code("cli", {"challenge": "test"})
        google.store_login_code("login")
        for namespace in (google.OAUTH_STATE_NAMESPACE, google.CLI_CODE_NAMESPACE,
                          google.LOGIN_CODE_NAMESPACE):
            get_shared_state().set(namespace, "expired", {}, -1)
        result = True
    elif action == "state-pop":
        result = {
            "oauth": [google.pop_oauth_state("state"), google.pop_oauth_state("expired")],
            "cli": [google.pop_cli_code("cli"), google.pop_cli_code("expired")],
            "login": [google.pop_login_code("login"), google.pop_login_code("expired")],
        }
    elif action == "request":
        result = client.post("/ingest/v1/usage", headers={"x-tokenage-token": command[1]},
            json={"provider": "test", "model": "test-model", "endpoint": "test"}).status_code
    elif action == "revoke":
        result = revoke_token(command[1], command[2])
    elif action == "counts":
        with get_engine().connect() as conn:
            result = [conn.execute(text(sql)).scalar_one() for sql in (
                "SELECT count(*) FROM usage",
                "SELECT count(*) FROM tool_calls",
                "SELECT count(DISTINCT usage_id) FROM tool_calls",
                "SELECT count(*) FROM tool_calls WHERE usage_id IS NULL",
                "SELECT sum(request_count) FROM usage_hourly",
                "SELECT count(*) FROM tool_calls t JOIN usage u ON t.usage_id=u.id "
                "WHERE t.tool_use_id != u.user_id || ':' || replace(u.model, 'model-', 'tool-')")]
    else:
        raise ValueError("unknown worker command")
    print(json.dumps(result), flush=True)
"""


@pytest.fixture
def queue_backend(request, monkeypatch, redis_store_factory):
    if request.param == "redis":
        store = redis_store_factory()
        monkeypatch.setenv(
            "TOKENAGE_MULTI_WORKER_TEST_URL", os.environ["TOKENAGE_TEST_REDIS_URL"]
        )
        monkeypatch.setenv("TEST_REDIS_PREFIX", store.prefix)
    else:
        monkeypatch.delenv("TOKENAGE_MULTI_WORKER_TEST_URL", raising=False)
        monkeypatch.delenv("TEST_REDIS_PREFIX", raising=False)


class Worker:
    def __init__(self, env):
        self.stderr = tempfile.TemporaryFile(mode="w+t", dir=env["TOKENAGE_HOME"])
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-c", WORKER],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            text=True,
        )

    def send(self, *command):
        __tracebackhide__ = True
        self.process.stdin.write(json.dumps(command) + "\n")
        self.process.stdin.flush()

    def receive(self):
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            # Above SQLite's 30 s busy_timeout, so a slow, loaded host still
            # reports the real failure rather than a timeout.
            assert selector.select(90), "worker response timed out"
        line = self.process.stdout.readline()
        assert line, "worker exited without responding"
        return json.loads(line)

    def call(self, *command):
        __tracebackhide__ = True
        self.send(*command)
        return self.receive()

    def close(self):
        try:
            self.process.stdin.close()
        except BrokenPipeError:
            pass
        try:
            self.process.wait(15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(5)
        self.stderr.seek(0)
        self.diagnostics = self.stderr.read()
        self.stderr.close()


@contextmanager
def workers(tmp_path, count):
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "pricing": {"auto_fetch": False},
                "db": {"path": str(tmp_path / "usage.db")},
                "queue": {
                    "url": os.environ.get("TOKENAGE_MULTI_WORKER_TEST_URL")
                    or f"sqlite:///{tmp_path / 'state.db'}",
                    "prefix": os.environ.get("TEST_REDIS_PREFIX", "tokenage:"),
                },
            }
        )
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("TOKENAGE_")}
    env.update(TOKENAGE_HOME=str(tmp_path), TOKENAGE_CONFIG=str(config))
    with ExitStack() as cleanup:
        children = []
        for _ in range(count):
            child = Worker(env)
            cleanup.callback(child.close)
            children.append(child)
        assert all(child.receive() == "ready" for child in children)
        yield children
    # Checked only after a passing body, so a killed child cannot mask the
    # test's own failure.
    for child in children:
        assert child.process.returncode == 0, child.diagnostics


@pytest.mark.parametrize("count", [2, 3, 4, 5])
def test_concurrent_cold_database_startup(tmp_path, count):
    with workers(tmp_path, count) as children:
        # Every interpreter has finished imports; release the constructors
        # together so WAL activation and DDL run from real competing processes.
        for child in children:
            child.send("cold-start")
        assert [child.receive() for child in children] == [True] * count


@pytest.mark.parametrize("count", [2, 3, 4, 5])
@pytest.mark.parametrize("queue_backend", ["sqlite", "redis"], indirect=True)
def test_interleaved_tool_usage_matches_once_across_processes(
    tmp_path, count, queue_backend
):
    with workers(tmp_path, count) as children:
        assert children[0].call("init")
        _, user_id, device_id, _ = children[0].call("mint")
        expected = 12
        pending = []
        for n in range(expected):

            def event(fields):
                record = {
                    "timeUnixNano": str((1776384000 + n) * 1_000_000_000),
                    "attributes": [
                        {
                            "key": key,
                            "value": {
                                "intValue"
                                if isinstance(value, int)
                                else "stringValue": value
                            },
                        }
                        for key, value in fields.items()
                    ],
                }
                return {
                    "kind": "otlp",
                    "user_id": user_id,
                    "device_id": device_id,
                    "record": [record, "opencode", "shared-session"],
                }

            tool = event(
                {
                    "event.name": "opencode.tool_decision",
                    "message.id": str(n),
                    "call_id": f"tool-{n}",
                    "tool_name": "Bash",
                }
            )
            usage = event(
                {
                    "event.name": "opencode.message_completed",
                    "message.id": str(n),
                    "model": f"model-{n}",
                    "input_token_count": 10,
                    "output_token_count": 5,
                }
            )
            # Force correlation to cross processes. Every event still has
            # the same user's partition.
            publisher, finisher = children[n % count], children[(n + 1) % count]
            assert publisher.call("publish", user_id, [tool])
            assert publisher.call("drain") == 1
            assert finisher.call("publish", user_id, [usage])
            assert finisher.call("drain") == 1
            # A second wave builds a real backlog for simultaneous consumers.
            extra_tool = event(
                {
                    "event.name": "opencode.tool_decision",
                    "message.id": f"extra-{n}",
                    "call_id": f"extra-tool-{n}",
                    "tool_name": "Bash",
                }
            )
            extra_usage = event(
                {
                    "event.name": "opencode.message_completed",
                    "message.id": f"extra-{n}",
                    "model": f"extra-model-{n}",
                    "input_token_count": 10,
                    "output_token_count": 5,
                }
            )
            pending.extend([extra_tool, extra_usage])
        assert children[0].call("publish", user_id, pending)
        drained = 0
        # Bound the passes, rather than relying on timing/sleeps or unlimited retries.
        for _ in range(len(pending) + 1):
            for child in children:
                child.send("drain")
            processed = sum(child.receive() for child in children)
            drained += processed
            if processed == 0:
                break
        assert drained == len(pending)
        expected *= 2
        assert children[0].call("counts") == [
            expected,
            expected,
            expected,
            0,
            expected,
            0,
        ]


@pytest.mark.parametrize("count", [2, 3, 4, 5])
@pytest.mark.parametrize("queue_backend", ["sqlite", "redis"], indirect=True)
def test_signin_state_single_use_and_expiry_across_processes(
    tmp_path, count, queue_backend
):
    with workers(tmp_path, count) as children:
        assert children[0].call("state-store")
        for child in children[1:]:
            child.send("state-pop")
        results = [child.receive() for child in children[1:]]
        for name, expected in (
            ("oauth", {"nonce": "test"}),
            ("cli", {"challenge": "test"}),
            ("login", {}),
        ):
            assert sum(result[name][0] == expected for result in results) == 1
            assert (
                sum(result[name][0] is None for result in results) == len(results) - 1
            )
            assert all(result[name][1] is None for result in results)
        assert children[0].call("state-pop") == {
            name: [None, None] for name in ("oauth", "cli", "login")
        }


@pytest.mark.parametrize("count", [2, 3, 4, 5])
def test_revoked_token_rejected_on_next_request_in_other_process(tmp_path, count):
    with workers(tmp_path, count) as children:
        first, *others = children
        assert first.call("init")
        token, user_id, _, token_id = first.call("mint")
        for child in others:
            child.send("request", token)
        assert [child.receive() for child in others] == [204] * len(others)
        assert first.call("revoke", token_id, user_id)
        for child in others:
            child.send("request", token)
        assert [child.receive() for child in others] == [401] * len(others)


@pytest.mark.parametrize("count", [2, 3, 4, 5])
def test_warm_startup_does_not_wait_for_sqlite_writer(tmp_path, count):
    with workers(tmp_path, count) as children:
        first, *others = children
        assert first.call("init")
        assert first.call("hold-write")
        try:
            for child in others:
                child.send("init")
            assert [child.receive() for child in others] == [True] * len(others)
        finally:
            assert first.call("release-write")
