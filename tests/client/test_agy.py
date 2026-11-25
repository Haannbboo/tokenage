"""Antigravity: plugin install, the six hooks and the OTLP spool."""

import fcntl
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from client import agy_hooks, event_spool, hooks, otlp_spool, setup
from client.agents import DONE, FAILED, SKIPPED, agy
from protocol.events import SpoolReport

ENDPOINT = "https://collector.example/otlp/v1/logs"
CONVERSATION = "0172d8d4-4674-4b86-87b9-f26d71cdb314"
SECRET = "TOP-SECRET"


@pytest.fixture(autouse=True)
def isolated_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TOKENAGE_HOME", str(tmp_path / ".tokenage"))
    monkeypatch.setenv("TOKENAGE_BIN_DIR", str(tmp_path / "bin dir"))
    from client.auth import save_credentials

    save_credentials(
        {"server_url": "https://collector.example", "ingest_token": "fake-ingest"}
    )


@pytest.fixture
def clock(monkeypatch):
    now = {"ms": 1_800_000_000_000}
    monkeypatch.setattr(agy_hooks.time, "time_ns", lambda: now["ms"] * 1_000_000)
    return now


def target(tmp_path) -> Path:
    return tmp_path / ".gemini" / "config" / "plugins" / "tokenage"


def run(event, payload, monkeypatch, capsys) -> str:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr("sys.stdin", io.StringIO(raw))
    assert hooks.main(["agy", event]) == 0
    return capsys.readouterr().out


def planner(step, tools=(), **extra):
    return {
        "step_index": step,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "created_at": "2026-11-25T07:24:32Z",
        "model": "gemini-3.8-flash-low",
        "input_tokens": 16009,
        "cache_read_tokens": 7,
        "output_tokens": 40,
        "content": f"{SECRET} content",
        "thinking": f"{SECRET} thinking",
        "tool_calls": [{"name": n, "args": {"cmd": SECRET}} for n in tools],
        **extra,
    }


def write_transcript(tmp_path, rows) -> Path:
    path = tmp_path / f"{SECRET}-dir" / "transcript_full.jsonl"
    path.parent.mkdir(exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def base(transcript=None, **extra):
    payload = {"conversationId": CONVERSATION, "lastUserInput": SECRET, **extra}
    if transcript is not None:
        payload["transcriptPath"] = str(transcript)
    return payload


def tool(call_id, name):
    return {"toolCall": {"id": call_id, "name": name, "args": {"cmd": SECRET}}}


def call_digest(call_id, conversation=CONVERSATION):
    return hashlib.sha256(f"{conversation}\0{call_id}".encode()).hexdigest()[:32]


def spooled() -> list[dict]:
    paths = sorted(agy_hooks.otlp_dir().glob("*.json"), key=lambda p: p.stat().st_mtime)
    return [json.loads(path.read_text()) for path in paths]


def records(payload) -> list[dict]:
    (resource,) = payload["resourceLogs"]
    (scope,) = resource["scopeLogs"]
    return [
        {a["key"]: next(iter(a["value"].values())) for a in record["attributes"]}
        | {"_time": record["timeUnixNano"]}
        for record in scope["logRecords"]
    ]


def sign_in(server="https://other.example", token="other-ingest") -> str:
    from client.auth import save_credentials

    save_credentials({"server_url": server, "ingest_token": token})
    return agy_hooks.binding()


def append(transcript, *rows) -> None:
    with transcript.open("a") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def spool(text='{"resourceLogs":[]}', limit=10) -> bool:
    return agy_hooks._spool(
        agy_hooks.otlp_dir(), text, limit, bound=agy_hooks.binding()
    )


def state() -> dict:
    (path,) = agy_hooks.state_dir().glob("*.json")
    return json.loads(path.read_text())


# ---------------------------------------------------------------- plugin


def test_configure_installs_private_plugin_without_token(tmp_path):
    directory = target(tmp_path)
    assert agy.configure(directory, ENDPOINT, "fake-ingest") == DONE
    assert agy.configure(directory, ENDPOINT, "fake-ingest") == DONE
    from client.paths import client_version

    manifest = json.loads((directory / "plugin.json").read_text())
    assert manifest == {
        "name": "tokenage",
        "version": client_version(),
        "author": "tokenage",
    }
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    registered = json.loads((directory / "hooks.json").read_text())["tokenage"]
    launcher = f"'{tmp_path}/bin dir/tokenage'"
    for name, event in {
        "SessionStart": "session-start",
        "PreInvocation": "pre-invocation",
        "PostInvocation": "post-invocation",
        "Stop": "stop",
    }.items():
        assert registered.pop(name) == [
            {"type": "command", "command": f"{launcher} hook agy {event}", "timeout": 5}
        ]
    for name, event in {
        "PreToolUse": "pre-tool-use",
        "PostToolUse": "post-tool-use",
    }.items():
        assert registered.pop(name) == [
            {
                "matcher": "*",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{launcher} hook agy {event}",
                        "timeout": 5,
                    }
                ],
            }
        ]
    assert registered == {}
    assert sorted(p.name for p in directory.iterdir()) == ["hooks.json", "plugin.json"]
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for path in directory.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert "fake-ingest" not in path.read_text()
    # Antigravity's own files are never created or touched.
    assert sorted(p.name for p in directory.parent.parent.iterdir()) == ["plugins"]
    # No endpoint is stored anywhere: the hooks follow the signed-in collector.
    assert not (tmp_path / ".tokenage" / "run" / "agy_wiring.json").exists()
    health = setup.read_agent_states(ENDPOINT)["agy"]
    assert health["status"] == "ready" and health["configured_endpoint"] == ENDPOINT
    assert setup.read_agent_states(None)["agy"]["status"] == "configured"


@pytest.mark.parametrize("version", ["", "1.2", "1.2.3-dev", OSError()])
def test_plugin_version_is_always_a_strict_version(tmp_path, monkeypatch, version):
    def client_version():
        if isinstance(version, Exception):
            raise version
        return version

    monkeypatch.setattr(agy, "client_version", client_version)
    assert agy.configure(target(tmp_path), ENDPOINT) == DONE
    manifest = json.loads((target(tmp_path) / "plugin.json").read_text())
    assert manifest["version"] == "0.1.0"


def test_configure_does_not_echo_endpoint_credentials(tmp_path, capsys):
    endpoint = "https://user:secret@collector.example/secret/v1/logs?token=secret"
    assert agy.configure(target(tmp_path), endpoint, "fake-ingest") == DONE
    output = capsys.readouterr()
    assert "secret" not in output.out + output.err


def test_disabled_in_antigravity_config_is_not_configured(tmp_path):
    directory = target(tmp_path)
    assert agy.configure(directory, ENDPOINT) == DONE
    config = directory.parent.parent / "config.json"
    config.write_text(json.dumps({"plugins": {"tokenage": {"enabled": True}}}))
    assert setup.read_agent_states(ENDPOINT)["agy"]["configured"] is True
    content = json.dumps({"plugins": {"tokenage": {"enabled": False}}})
    config.write_text(content)
    health = setup.read_agent_states(ENDPOINT)["agy"]
    assert health["configured"] is False and health["status"] == "missing_config"
    assert health["configured_endpoint"] is None
    assert config.read_text() == content


def test_configure_does_not_claim_a_disabled_plugin_is_wired(tmp_path, capsys):
    directory = target(tmp_path)
    config = directory.parent.parent / "config.json"
    config.parent.mkdir(parents=True)
    content = json.dumps({"plugins": {"tokenage": {"enabled": False}}})
    config.write_text(content)
    assert agy.configure(directory, ENDPOINT) == SKIPPED
    assert "agy plugin enable tokenage" in capsys.readouterr().err
    assert config.read_text() == content
    assert setup.wire_agents(logs_endpoint=ENDPOINT, token=None, agents=["agy"]) == []
    assert "wiring agy skipped" in capsys.readouterr().err
    # The files are in place, so enabling it is all that is left to do.
    config.write_text(json.dumps({"plugins": {"tokenage": {"enabled": True}}}))
    assert setup.read_agent_states(ENDPOINT)["agy"]["status"] == "ready"


@pytest.mark.parametrize("name", ["plugin.json", "hooks.json"])
@pytest.mark.parametrize("content", ['{"secret":', "[]", "null", '{"name": "other"}'])
def test_malformed_plugin_files_are_not_configured(tmp_path, name, content):
    directory = target(tmp_path)
    assert setup.read_agent_states(ENDPOINT)["agy"]["status"] == "missing_config"
    assert agy.configure(directory, ENDPOINT) == DONE
    (directory / name).write_text(content)
    assert setup.read_agent_states(ENDPOINT)["agy"]["configured"] is False
    if name == "hooks.json":
        assert agy.configure(directory, ENDPOINT) == DONE  # The author still matches.
        assert setup.read_agent_states(ENDPOINT)["agy"]["status"] == "ready"
    else:
        assert agy.configure(directory, ENDPOINT) == FAILED
        assert (directory / name).read_text() == content


def test_symlinks_are_refused(tmp_path, capsys):
    directory = target(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    directory.parent.mkdir(parents=True)
    directory.symlink_to(elsewhere, target_is_directory=True)
    assert agy.configure(directory, ENDPOINT) == FAILED
    assert "left unchanged" in capsys.readouterr().err
    assert list(elsewhere.iterdir()) == []
    assert setup.read_agent_states(ENDPOINT)["agy"]["configured"] is False
    assert agy.disable(directory, ENDPOINT) == FAILED
    assert directory.is_symlink() and elsewhere.is_dir()

    directory.unlink()
    directory.mkdir()
    private = tmp_path / "private.json"
    private.write_text("private")
    for name in ("hooks.json", "plugin.json"):
        (directory / name).symlink_to(private)
        assert agy.configure(directory, ENDPOINT) == FAILED
        assert agy.disable(directory, ENDPOINT) == FAILED
        assert private.read_text() == "private"
        assert sorted(p.name for p in directory.iterdir()) == [name]
        (directory / name).unlink()


@pytest.mark.parametrize("endpoint", [ENDPOINT, None, "https://other.example"])
def test_disable_removes_only_what_tokenage_wrote(tmp_path, capsys, endpoint):
    directory = target(tmp_path)
    assert agy.disable(directory, endpoint) == SKIPPED  # Nothing installed.
    assert agy.configure(directory, ENDPOINT) == DONE
    (directory / "notes.txt").write_text("mine")
    states = agy_hooks.state_dir()
    states.mkdir(parents=True)
    (states / "x.json").write_text("{}")
    (states / "x.lock").write_text("")
    (states / "busy.lock").write_text("")
    busy = os.open(states / "busy.lock", os.O_RDWR)
    fcntl.flock(busy, fcntl.LOCK_EX)  # A hook that is running right now.
    try:
        # The directory is tokenage's own, whatever collector it was wired for.
        assert agy.disable(directory, endpoint) == DONE
    finally:
        os.close(busy)
    assert [p.name for p in directory.iterdir()] == ["notes.txt"]
    assert (directory / "notes.txt").read_text() == "mine"
    assert [p.name for p in states.iterdir()] == ["busy.lock"]

    (directory / "notes.txt").unlink()
    assert agy.configure(directory, ENDPOINT) == DONE
    assert agy.disable(directory, endpoint) == DONE
    assert not directory.exists() and directory.parent.is_dir()
    assert agy.disable(directory, endpoint) == SKIPPED


def test_a_plugin_tokenage_did_not_install_is_left_alone(tmp_path, capsys):
    directory = target(tmp_path)
    directory.mkdir(parents=True)
    foreign = {
        "plugin.json": json.dumps({"name": "tokenage"}),
        "hooks.json": json.dumps({"tokenage": {"Stop": [{"command": "other"}]}}),
    }
    for name, content in foreign.items():
        (directory / name).write_text(content)
    assert agy.configure(directory, ENDPOINT) == FAILED
    assert "left unchanged" in capsys.readouterr().err
    assert agy.disable(directory, ENDPOINT) == SKIPPED
    assert {p.name: p.read_text() for p in directory.iterdir()} == foreign

    # Matching commands cannot claim a plugin without the author marker.
    hooks_file = directory / "hooks.json"
    hooks_file.write_text(json.dumps(agy.hooks_document()))
    assert not agy.state(directory)
    assert agy.configure(directory, ENDPOINT) == FAILED
    assert agy.disable(directory, ENDPOINT) == SKIPPED
    assert json.loads(hooks_file.read_text()) == agy.hooks_document()


@pytest.mark.parametrize("event", [*agy._FLAT, *agy._GROUPED])
def test_status_requires_a_handler_for_every_event(tmp_path, event):
    directory = target(tmp_path)
    assert agy.configure(directory, ENDPOINT) == DONE
    (directory / "hooks.json").write_text(json.dumps({"tokenage": {}}))
    assert not agy.state(directory)
    definition = agy.hooks_document()
    definition["tokenage"][event] = [{"command": "foreign", "other": " hook agy "}]
    (directory / "hooks.json").write_text(json.dumps(definition))
    assert not agy.state(directory)


def test_configure_reports_a_partial_write(tmp_path, monkeypatch, capsys):
    write = agy._write_atomic

    def fail_hooks(path, text):
        if path.name == "hooks.json":
            raise OSError()
        write(path, text)

    directory = target(tmp_path)
    monkeypatch.setattr(agy, "_write_atomic", fail_hooks)
    assert agy.configure(directory, ENDPOINT) == FAILED
    assert json.loads((directory / "plugin.json").read_text())["author"] == "tokenage"
    assert "plugin.json written, hooks.json update failed" in capsys.readouterr().err
    assert not agy.state(directory)


def test_setup_registers_agy_and_finds_it_off_path(tmp_path, monkeypatch):
    assert setup.AGENT_MODULES["agy"] is agy
    assert setup.agent_targets()["agy"] == str(target(tmp_path))
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    assert setup.agent_path("agy") is None
    binary = tmp_path / ".gemini" / "antigravity-cli" / "bin" / "agy"
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    assert setup.agent_path("agy") == str(binary)
    local = tmp_path / ".local" / "bin" / "agy"
    local.parent.mkdir(parents=True)
    local.write_text("")
    assert setup.agent_path("agy") == str(local)
    assert setup.installed_agents() == ["agy"]


def test_setup_disable_and_logout_clear_the_otlp_spool(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", lambda name: None)
    for clear in (
        lambda: setup.run_setup(disable=True),
        lambda: __import__("client.auth").auth.logout(keep_agents=True),
    ):
        assert spool()
        assert list(agy_hooks.otlp_dir().iterdir())
        monkeypatch.setattr(
            "client.auth._revoke_logout_device", lambda credentials: True
        )
        clear()
        assert list(agy_hooks.otlp_dir().iterdir()) == []


# ----------------------------------------------------------------- hooks


def test_hook_module_imports_only_the_standard_library(tmp_path):
    code = (
        "import sys, io\n"
        "from client import agy_hooks\n"
        "for event in agy_hooks.EVENTS:\n"
        '    sys.stdin = io.StringIO(\'{"conversationId": "c1", "cwd": "/"}\')\n'
        "    agy_hooks.main([event])\n"
        "heavy = {'pydantic', 'httpx', 'yaml', 'protocol', 'src', 'client.auth'}\n"
        "print(sorted(heavy & {m.split('.')[0] for m in sys.modules} | "
        "heavy & set(sys.modules)), file=sys.stderr)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "TOKENAGE_HOME": str(tmp_path / ".tokenage")},
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == "[]"
    assert result.stdout == "{}\n" * 5  # Every event but pre-tool-use.


@pytest.mark.parametrize("event", agy_hooks.EVENTS)
@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "[]",
        "{}",
        '{"conversationId": 1}',
        '{"conversationId": "bad id/../x"}',
        json.dumps(
            {
                "conversationId": CONVERSATION,
                "cwd": "/",
                "transcriptPath": "/missing",
                "toolCall": {"id": "t1", "name": "run_command"},
            }
        ),
    ],
)
def test_stdout_contract_holds_even_on_errors(event, raw, monkeypatch, capsys):
    # Any stdout from PreToolUse, even "{}", makes Antigravity deny the tool.
    expected = "" if event == "pre-tool-use" else "{}\n"
    assert run(event, raw, monkeypatch, capsys) == expected


def test_stdout_contract_holds_when_the_handler_raises(monkeypatch, capsys):
    def boom(event):
        raise RuntimeError("boom")

    monkeypatch.setattr(agy_hooks, "_handle", boom)
    assert run("pre-tool-use", base(), monkeypatch, capsys) == ""
    assert run("stop", base(), monkeypatch, capsys) == "{}\n"
    assert hooks.main(["agy"]) == 0 and capsys.readouterr().out == "{}\n"


def test_session_start_spools_a_report_the_event_spool_delivers(
    tmp_path, monkeypatch, capsys
):
    cwd = tmp_path / "orchard"
    cwd.mkdir()
    payload = base(cwd=str(cwd), transcriptPath=f"/{SECRET}/transcript")
    assert run("session-start", payload, monkeypatch, capsys) == "{}\n"
    (path,) = event_spool.spool_dir().glob("*.json")
    body = path.read_text()
    assert SpoolReport.model_validate_json(body).model_dump(exclude={"ts"}) == {
        "schema_version": 1,
        "agent": "agy",
        "session_id": CONVERSATION,
        "project": "orchard",
    }
    assert SECRET not in body and str(tmp_path) not in body
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert path.name.split(".")[-2] == event_spool._identity()[2]

    posted = []
    monkeypatch.setattr(
        event_spool.httpx,
        "post",
        lambda url, **kw: posted.append(kw["json"]) or httpx.Response(200),
    )
    assert event_spool.drain_once()
    assert posted[0]["events"][0]["agent"] == "agy"
    assert not list(event_spool.spool_dir().glob("*.json"))


def test_signed_out_hooks_write_no_spool(tmp_path, monkeypatch, capsys):
    from client.auth import clear_credentials

    clear_credentials()
    transcript = write_transcript(tmp_path, [planner(1)])
    assert run("session-start", base(cwd=str(tmp_path)), monkeypatch, capsys) == "{}\n"
    assert run("post-invocation", base(transcript), monkeypatch, capsys) == "{}\n"
    assert not event_spool.spool_dir().exists()
    assert state()["last_step_index"] == 1  # Never replayed to a later login.


def test_invocation_with_tools_emits_one_payload(tmp_path, monkeypatch, capsys, clock):
    transcript = write_transcript(tmp_path, [{"type": "USER_INPUT", "step_index": 0}])
    start = clock["ms"]
    run("pre-invocation", base(invocationNum=0, initialNumSteps=0), monkeypatch, capsys)
    assert state()["pending_invocation"] == {
        "invocation_num": 0,
        "started_at_ms": start,
        "first_tool_ts_ms": None,
    }
    clock["ms"] += 1200  # The model answers...
    assert (
        run("pre-tool-use", base(**tool("t1", "run_command")), monkeypatch, capsys)
        == ""
    )
    clock["ms"] += 5000  # ...and its tool runs for five seconds.
    run("post-tool-use", base(**tool("t1", "run_command")), monkeypatch, capsys)
    assert state()["pending_tools"] == [
        {
            "call_id": call_digest("t1"),
            "tool_name": "run_command",
            "ts_ms": start + 1200,
            "duration_ms": 5000,
        }
    ]
    with transcript.open("a") as stream:
        row = planner(1, ["run_command", "view_file"], cache_creation_tokens=3)
        stream.write(json.dumps(row) + "\n")
    clock["ms"] += 10
    run("post-invocation", base(transcript), monkeypatch, capsys)

    (payload,) = spooled()
    attributes = payload["resourceLogs"][0]["resource"]["attributes"]
    assert attributes == [
        {"key": "service.name", "value": {"stringValue": "agy"}},
        {"key": "session.id", "value": {"stringValue": CONVERSATION}},
    ]
    now_ns = str(clock["ms"] * 1_000_000)
    raw = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    assert {r["observedTimeUnixNano"] for r in raw} == {now_ns}
    hooked, denied, completed = records(payload)
    message = f"{CONVERSATION}:1"
    assert hooked == {
        "event.name": "agy.tool_decision",
        "session.id": CONVERSATION,
        "message.id": message,
        "call_id": call_digest("t1"),
        "tool_name": "run_command",
        "duration_ms": "5000",
        "_time": str((start + 1200) * 1_000_000),
    }
    # Named by the transcript but never hooked: a stable synthetic id.
    assert denied["call_id"] == call_digest("1:1")
    assert denied["tool_name"] == "view_file" and "duration_ms" not in denied
    assert completed == {
        "event.name": "agy.invocation_completed",
        "session.id": CONVERSATION,
        "message.id": message,
        "model": "gemini-3.8-flash-low",
        "input_tokens": "16009",
        "cache_read_tokens": "7",
        "output_tokens": "40",
        "cache_creation_tokens": "3",
        "duration_ms": "1200",  # Latency stops at the first tool call.
        "http.response.status_code": "200",
        "_time": "1795591472000000000",  # created_at
    }
    after = state()
    assert after["last_step_index"] == 1 and after["pending_invocation"] is None
    assert after["pending_tools"] == [] and "tool_starts" not in after
    assert after["transcript_inode"] == transcript.stat().st_ino
    assert after["byte_offset"] == transcript.stat().st_size

    run("stop", base(transcript), monkeypatch, capsys)  # Nothing new to send.
    assert len(spooled()) == 1


@pytest.mark.parametrize("created_at", ["2027-01-15T08:00:00.700Z", None])
def test_latency_without_tools_is_stable_on_redelivery(
    tmp_path, monkeypatch, capsys, clock, created_at
):
    transcript = write_transcript(tmp_path, [planner(1, created_at=created_at)])
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    clock["ms"] += 1000
    with monkeypatch.context() as interrupted:
        # Simulate an alarm after spooling and before the cursor write.
        write = agy_hooks._write_atomic

        def fail_state(path, value):
            if path.parent == agy_hooks.state_dir():
                raise TimeoutError
            write(path, value)

        interrupted.setattr(agy_hooks, "_write_atomic", fail_state)
        run("post-invocation", base(transcript), monkeypatch, capsys)

    clock["ms"] += 1000
    run("stop", base(transcript), monkeypatch, capsys)
    first, second = [records(payload)[0] for payload in spooled()]
    assert all("duration_ms" not in record for record in (first, second))
    if created_at:
        assert first == second and first["_time"] == "1800000000700000000"
    assert "cache_creation_tokens" not in first


def test_resumed_conversation_does_not_replay_history(
    tmp_path, monkeypatch, capsys, clock
):
    history = [planner(1), planner(3, ["run_command"]), planner(5)]
    transcript = write_transcript(tmp_path, history)
    boundary = transcript.stat().st_size
    with transcript.open("a") as stream:
        stream.write('{"type":"USER_INPUT"')
    run("pre-invocation", base(transcript, initialNumSteps=6), monkeypatch, capsys)
    assert state()["last_step_index"] == 5
    assert state()["byte_offset"] == boundary
    assert state()["transcript_inode"] == transcript.stat().st_ino
    run("post-invocation", base(transcript), monkeypatch, capsys)
    assert spooled() == []  # PostInvocation ran before the new step landed.
    assert state()["pending_invocation"] is not None

    with transcript.open("a") as stream:
        stream.write("}\n")
        stream.write(json.dumps(planner(7)) + "\n")
        stream.write(json.dumps(planner(8, status="RUNNING")) + "\n")
        stream.write(json.dumps(planner(9))[:40])  # Still being written.
    run("stop", base(transcript), monkeypatch, capsys)
    (payload,) = spooled()
    assert [r["message.id"] for r in records(payload)] == [f"{CONVERSATION}:7"]

    # A later invocation of a known conversation keeps its own cursor.
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    assert state()["last_step_index"] == 7


@pytest.mark.parametrize("initial", [None, True, "1"])
def test_history_flush_needs_a_trusted_baseline(tmp_path, monkeypatch, capsys, initial):
    transcript = write_transcript(tmp_path, [planner(1)])
    run("pre-tool-use", base(**tool("old", "Read")), monkeypatch, capsys)
    run("pre-invocation", base(initialNumSteps=initial), monkeypatch, capsys)
    run("post-invocation", base(transcript), monkeypatch, capsys)
    append(transcript, planner(2))
    run("stop", base(transcript), monkeypatch, capsys)
    assert spooled() == []
    assert state()["resync"] is True
    assert state()["last_step_index"] == 2
    assert state()["byte_offset"] == transcript.stat().st_size

    run("pre-invocation", base(transcript, initialNumSteps=3), monkeypatch, capsys)
    append(transcript, planner(3))
    run("stop", base(transcript), monkeypatch, capsys)
    (completed,) = records(spooled()[0])
    assert completed["message.id"] == f"{CONVERSATION}:3"


@pytest.mark.parametrize("oversized", [False, True])
def test_scan_checkpoints_bounded_progress(tmp_path, monkeypatch, capsys, oversized):
    first, second = planner(1), planner(2)
    first_end = len(json.dumps(first)) + 1
    noise = {"type": "USER_INPUT", "content": "x" * (3500 if oversized else 350)}
    budget = 1000
    monkeypatch.setattr(agy_hooks, "SCAN_MAX_BYTES", budget)
    transcript = write_transcript(tmp_path, [first, noise, second])
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)

    run("post-invocation", base(transcript), monkeypatch, capsys)
    assert state()["byte_offset"] < transcript.stat().st_size
    assert state()["byte_offset"] >= first_end
    for _ in range(6):
        before = state()["byte_offset"]
        if before == transcript.stat().st_size:
            break
        run("stop", base(transcript), monkeypatch, capsys)
        assert 0 < state()["byte_offset"] - before <= budget
    assert state()["byte_offset"] == transcript.stat().st_size
    assert [r["message.id"] for payload in spooled() for r in records(payload)] == [
        f"{CONVERSATION}:1",
        f"{CONVERSATION}:2",
    ]


@pytest.mark.parametrize("invalid", ["with space", "/some/path", "x" * 129, 1])
def test_invalid_metadata_is_dropped_without_losing_usage(
    tmp_path, monkeypatch, capsys, invalid
):
    valid = "pkg.tool:@a+b-c_10"
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    run("pre-tool-use", base(**tool("invalid", invalid)), monkeypatch, capsys)
    assert state()["pending_tools"] == []
    transcript = write_transcript(
        tmp_path, [planner(1, [invalid, valid], model=invalid)]
    )
    run("stop", base(transcript), monkeypatch, capsys)
    decision, completion = records(spooled()[0])
    assert decision["tool_name"] == valid
    assert "model" not in completion and completion["input_tokens"] == "16009"


def test_tokenless_steps_are_consumed_without_spooling(tmp_path, monkeypatch, capsys):
    row = planner(1, ["Read"])
    for key in agy_hooks.TOKEN_KEYS:
        row.pop(key, None)
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    run("pre-tool-use", base(**tool("tool-1", "Read")), monkeypatch, capsys)
    transcript = write_transcript(tmp_path, [row])
    run("stop", base(transcript), monkeypatch, capsys)
    assert spooled() == []
    assert state()["last_step_index"] == 1
    assert state()["byte_offset"] == transcript.stat().st_size
    assert state()["pending_tools"] == []
    append(transcript, planner(2))
    run("stop", base(transcript), monkeypatch, capsys)
    (completion,) = records(spooled()[0])
    assert completion["message.id"] == f"{CONVERSATION}:2"


def test_several_steps_pair_tools_by_name_and_skip_latency(
    tmp_path, monkeypatch, capsys, clock
):
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    for call_id, name in (("a", "view_file"), ("b", "run_command"), ("c", "extra")):
        run("pre-tool-use", base(**tool(call_id, name)), monkeypatch, capsys)
        run("post-tool-use", base(**tool(call_id, name)), monkeypatch, capsys)
    rows = [planner(1, ["view_file"]), planner(3, ["run_command", "grep_search"])]
    run("stop", base(write_transcript(tmp_path, rows)), monkeypatch, capsys)
    (payload,) = spooled()
    got = [
        (r["event.name"], r["message.id"], r.get("call_id")) for r in records(payload)
    ]
    assert got == [
        ("agy.tool_decision", f"{CONVERSATION}:1", call_digest("a")),
        ("agy.invocation_completed", f"{CONVERSATION}:1", None),
        ("agy.tool_decision", f"{CONVERSATION}:3", call_digest("b")),
        ("agy.tool_decision", f"{CONVERSATION}:3", call_digest("3:1")),
        ("agy.tool_decision", f"{CONVERSATION}:3", call_digest("c")),
        ("agy.invocation_completed", f"{CONVERSATION}:3", None),
    ]
    assert all("duration_ms" not in r for r in records(payload) if "call_id" not in r)


def test_post_tool_use_without_pre_still_reports_the_tool(
    tmp_path, monkeypatch, capsys, clock
):
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    run("post-tool-use", base(**tool("t9", "run_command")), monkeypatch, capsys)
    transcript = write_transcript(tmp_path, [planner(1, ["run_command"])])
    run("post-invocation", base(transcript), monkeypatch, capsys)
    decision, _ = records(spooled()[0])
    assert decision["call_id"] == call_digest("t9") and "duration_ms" not in decision


def test_scan_is_incremental_and_recovers_from_a_rewritten_file(
    tmp_path, monkeypatch, capsys, clock
):
    transcript = write_transcript(tmp_path, [planner(1), planner(2)])
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    run("post-invocation", base(transcript), monkeypatch, capsys)
    assert state()["byte_offset"] == transcript.stat().st_size
    run("post-invocation", base(transcript), monkeypatch, capsys)
    assert len(spooled()) == 1

    transcript.write_text(json.dumps(planner(3)) + "\n")  # Shrank.
    run("post-invocation", base(transcript), monkeypatch, capsys)
    assert len(spooled()) == 2

    # The offset now lands inside a line: read again from the start.
    filler = {"type": "USER_INPUT", "content": "x" * 400}
    rows = [filler, planner(3), planner(4)]
    transcript.write_text("".join(json.dumps(row) + "\n" for row in rows))
    run("post-invocation", base(transcript), monkeypatch, capsys)
    steps = [r["message.id"] for p in spooled() for r in records(p)]
    assert sorted(steps) == [f"{CONVERSATION}:{n}" for n in (1, 2, 3, 4)]


def test_only_allowlisted_metadata_is_stored_or_spooled(
    tmp_path, monkeypatch, capsys, clock
):
    cwd = tmp_path / "orchard"
    cwd.mkdir()
    rows = [
        {"type": "USER_INPUT", "step_index": 0, "content": SECRET},
        planner(1, ["run_command"], error=SECRET, extra_field=SECRET),
    ]
    transcript = write_transcript(tmp_path, rows[:1])
    common = base(
        transcript,
        initialNumSteps=1,
        cwd=str(cwd),
        workspacePaths=[str(cwd)],
        error=SECRET,
        terminationReason=SECRET,
    )
    run("session-start", common, monkeypatch, capsys)
    run("pre-invocation", common, monkeypatch, capsys)
    raw_id = f"/private/{SECRET}"
    run("pre-tool-use", {**common, **tool(raw_id, "run_command")}, monkeypatch, capsys)
    run("post-tool-use", {**common, **tool(raw_id, "run_command")}, monkeypatch, capsys)
    assert state()["pending_tools"][0]["call_id"] == call_digest(raw_id)
    assert raw_id not in json.dumps(state())
    append(transcript, rows[1])
    written = [p for p in (tmp_path / ".tokenage").rglob("*") if p.is_file()]
    run("post-invocation", common, monkeypatch, capsys)
    run("stop", common, monkeypatch, capsys)
    written += [p for p in (tmp_path / ".tokenage").rglob("*") if p.is_file()]
    assert len(spooled()) == 1 and len(written) > 4
    for path in written:
        if path.exists():
            text = path.read_text()
            # No path: the transcript's directory and file name stay out.
            assert SECRET not in text and "transcript_full" not in text
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    allowed = {
        "event.name",
        "session.id",
        "message.id",
        "call_id",
        "tool_name",
        "duration_ms",
        "model",
        "input_tokens",
        "cache_read_tokens",
        "output_tokens",
        "http.response.status_code",
        "_time",
    }
    assert {key for record in records(spooled()[0]) for key in record} == allowed
    assert set(state()) == {
        "binding",
        "resync",
        "last_step_index",
        "byte_offset",
        "discarding_line",
        "transcript_inode",
        "pending_invocation",
        "pending_tools",
    }
    assert stat.S_IMODE(agy_hooks.state_dir().stat().st_mode) == 0o700
    assert stat.S_IMODE(agy_hooks.otlp_dir().stat().st_mode) == 0o700


def test_state_files_are_pruned_by_age_and_count(monkeypatch, capsys):
    monkeypatch.setattr(agy_hooks, "STATE_MAX_FILES", 2)
    for name in ("c1", "c2", "c3"):
        run("pre-invocation", {"conversationId": name}, monkeypatch, capsys)
    assert len(list(agy_hooks.state_dir().glob("*.json"))) == 2
    old = next(agy_hooks.state_dir().glob("*.json"))
    os.utime(old, (0, 0))
    run("pre-invocation", {"conversationId": "c4"}, monkeypatch, capsys)
    assert not old.exists()


def test_pruning_never_removes_a_lock_a_hook_holds(monkeypatch, capsys):
    directory = agy_hooks.state_dir()
    directory.mkdir(parents=True)
    for name in ("held.lock", "idle.lock", "stale.lock", "stale.json", "note.txt"):
        (directory / name).write_text("{}")
        os.utime(directory / name, (0, 0))
    held = os.open(directory / "held.lock", os.O_RDWR)
    fcntl.flock(held, fcntl.LOCK_EX)  # Another hook, mid-update, no state yet.
    try:
        run("pre-invocation", {"conversationId": "c1"}, monkeypatch, capsys)
        digest = hashlib.sha256(b"c1").hexdigest()
        assert sorted(p.name for p in directory.iterdir()) == sorted(
            ["held.lock", "note.txt", f"{digest}.json", f"{digest}.lock"]
        )
        # The lock on the path is still the one that hook holds.
        assert os.fstat(held).st_ino == (directory / "held.lock").stat().st_ino
    finally:
        os.close(held)


def test_a_hook_waiting_on_a_pruned_lock_takes_the_new_one(monkeypatch):
    directory = agy_hooks.state_dir()
    directory.mkdir(parents=True)
    path = directory / "x.lock"
    real = fcntl.flock
    calls = []

    def flock(fd, operation):
        if not calls:  # Pruned between this hook's open and its lock.
            path.unlink()
            path.write_text("")
        calls.append(fd)
        return real(fd, operation)

    monkeypatch.setattr(agy_hooks.fcntl, "flock", flock)
    fd = agy_hooks._lock(path)
    try:
        assert len(calls) == 2
        assert os.fstat(fd).st_ino == path.stat().st_ino
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    "event", ["session-start", "pre-tool-use", "post-invocation", "stop"]
)
def test_each_hook_reads_the_login_once(tmp_path, monkeypatch, capsys, event):
    """A second read could belong to another login than the first."""
    real, calls = agy_hooks.binding, []
    monkeypatch.setattr(agy_hooks, "binding", lambda: calls.append(1) or real())
    transcript = write_transcript(tmp_path, [planner(1)])
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    calls.clear()
    run(event, base(transcript, cwd=str(tmp_path)), monkeypatch, capsys)
    assert len(calls) == 1
    if event != "pre-tool-use":
        directory = (
            agy_hooks.event_dir() if event == "session-start" else agy_hooks.otlp_dir()
        )
        (path,) = directory.glob("*.json")
        assert path.name.split(".")[-2] == real()


def test_a_new_login_never_receives_the_previous_logins_usage(
    tmp_path, monkeypatch, capsys, clock
):
    transcript = write_transcript(tmp_path, [])
    first = agy_hooks.binding()
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    run("pre-tool-use", base(**tool("a1", "run_command")), monkeypatch, capsys)
    append(transcript, planner(1, ["run_command"]))  # Never flushed as A.
    assert state()["binding"] == first

    second = sign_in()  # logout --keep-agents, then another account.
    run("pre-invocation", base(initialNumSteps=2), monkeypatch, capsys)
    after = state()
    assert after["binding"] == second and after["pending_tools"] == []
    assert after["last_step_index"] == 1 and after["resync"] is False
    append(transcript, planner(3, ["run_command"]))
    run("stop", base(transcript), monkeypatch, capsys)

    (path,) = agy_hooks.otlp_dir().glob("*.json")
    assert path.name.split(".")[-2] == second
    got = records(json.loads(path.read_text()))
    assert {r["message.id"] for r in got} == {f"{CONVERSATION}:3"}
    assert call_digest("a1") not in {r.get("call_id") for r in got}


def test_a_login_change_mid_invocation_skips_to_the_transcript_end(
    tmp_path, monkeypatch, capsys, clock
):
    transcript = write_transcript(tmp_path, [planner(1)])
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    run("pre-tool-use", base(**tool("a1", "run_command")), monkeypatch, capsys)

    second = sign_in()
    run("post-tool-use", base(**tool("a1", "run_command")), monkeypatch, capsys)
    assert state()["resync"] is True and state()["pending_invocation"] is None
    run("post-invocation", base(transcript), monkeypatch, capsys)
    append(transcript, planner(2))  # Written late, still the old invocation.
    run("stop", base(transcript), monkeypatch, capsys)
    after = state()
    assert spooled() == [] and after["pending_tools"] == []
    assert after["last_step_index"] == 2 and after["resync"] is True
    assert after["byte_offset"] == transcript.stat().st_size

    run("pre-invocation", base(transcript, initialNumSteps=3), monkeypatch, capsys)
    append(transcript, planner(3))
    run("stop", base(transcript), monkeypatch, capsys)
    (path,) = agy_hooks.otlp_dir().glob("*.json")
    assert path.name.split(".")[-2] == second
    (completed,) = records(json.loads(path.read_text()))
    assert completed["message.id"] == f"{CONVERSATION}:3"


def test_usage_from_signed_out_time_is_never_sent_after_signing_in(
    tmp_path, monkeypatch, capsys, clock
):
    from client.auth import clear_credentials

    transcript = write_transcript(tmp_path, [planner(1)])
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    run("post-invocation", base(transcript), monkeypatch, capsys)
    clear_credentials()
    run("pre-tool-use", base(**tool("a1", "run_command")), monkeypatch, capsys)
    append(transcript, planner(2, ["run_command"]))
    run("post-invocation", base(transcript), monkeypatch, capsys)
    assert state()["last_step_index"] == 2 and state()["pending_tools"] == []
    append(transcript, planner(3))  # Signed out, and no hook saw it.

    sign_in()
    run("stop", base(transcript), monkeypatch, capsys)
    assert state()["last_step_index"] == 3
    steps = [r["message.id"] for p in spooled() for r in records(p)]
    assert steps == [f"{CONVERSATION}:1"]  # Sent while signed in, before.


def test_tool_call_without_an_id_is_paired_by_step_and_name(
    tmp_path, monkeypatch, capsys, clock
):
    def call(name, **extra):
        return base(toolCall={"name": name, "args": {"cmd": SECRET}}, **extra)

    run("pre-tool-use", call("run_command", stepIdx=4), monkeypatch, capsys)
    clock["ms"] += 250
    run("post-tool-use", call("run_command", stepIdx=4), monkeypatch, capsys)
    run("pre-tool-use", call("view_file"), monkeypatch, capsys)  # Nothing to key on.
    run("pre-tool-use", call("view_file", stepIdx=True), monkeypatch, capsys)
    run("pre-tool-use", base(**tool("real", "grep_search")), monkeypatch, capsys)
    assert [(t["call_id"], t["duration_ms"]) for t in state()["pending_tools"]] == [
        (call_digest("step:4:run_command"), 250),
        (call_digest("real"), None),
    ]


def test_pending_tools_stay_bounded_without_a_flush(monkeypatch, capsys, clock):
    monkeypatch.setattr(agy_hooks, "PENDING_TOOLS_MAX", 3)
    for number in range(5):
        run("pre-tool-use", base(**tool(f"t{number}", "x")), monkeypatch, capsys)
    clock["ms"] += 40
    run("post-tool-use", base(**tool("t0", "x")), monkeypatch, capsys)  # Evicted.
    run("post-tool-use", base(**tool("t4", "x")), monkeypatch, capsys)
    after = state()
    assert "tool_starts" not in after
    assert [(t["call_id"], t["duration_ms"]) for t in after["pending_tools"]] == [
        (call_digest("t3"), None),
        (call_digest("t4"), 40),
        (call_digest("t0"), None),
    ]


def test_a_full_spool_does_not_move_the_cursor_past_unsent_steps(
    tmp_path, monkeypatch, capsys, clock
):
    monkeypatch.setattr(agy_hooks, "OTLP_MAX_RECORDS", 2)
    transcript = write_transcript(tmp_path, [planner(1), planner(2, ["run_command"])])
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    run("pre-tool-use", base(**tool("t1", "run_command")), monkeypatch, capsys)

    monkeypatch.setattr(agy_hooks, "OTLP_MAX_FILES", 0)
    run("stop", base(transcript), monkeypatch, capsys)
    after = state()
    assert spooled() == [] and after["last_step_index"] == -1
    assert after["byte_offset"] == 0 and len(after["pending_tools"]) == 1

    monkeypatch.setattr(agy_hooks, "OTLP_MAX_FILES", 1)  # Room for step 1 only.
    run("stop", base(transcript), monkeypatch, capsys)
    after = state()
    assert [r["message.id"] for p in spooled() for r in records(p)] == [
        f"{CONVERSATION}:1"
    ]
    assert after["last_step_index"] == 1 and after["byte_offset"] == 0
    assert [t["call_id"] for t in after["pending_tools"]] == [call_digest("t1")]

    otlp_spool.clear_spool()  # Delivered: a later hook picks up where it stopped.
    monkeypatch.setattr(agy_hooks, "OTLP_MAX_FILES", 10)
    run("post-invocation", base(transcript), monkeypatch, capsys)
    got = [(r["message.id"], r.get("call_id")) for p in spooled() for r in records(p)]
    assert sorted(got, key=str) == [
        (f"{CONVERSATION}:2", call_digest("t1")),
        (f"{CONVERSATION}:2", None),
    ]
    after = state()
    assert after["last_step_index"] == 2 and after["pending_tools"] == []
    assert after["byte_offset"] == transcript.stat().st_size


@pytest.mark.parametrize("conversation", [CONVERSATION, "c" * 254])
def test_large_steps_keep_their_completion_and_capped_tools_in_one_file(
    tmp_path, monkeypatch, capsys, clock, conversation
):
    names = [f"tool_{number:03}_" + "n" * 119 for number in range(250)]
    transcript = write_transcript(tmp_path, [planner(1, names), planner(2, names)])
    run(
        "pre-invocation",
        base(conversationId=conversation, initialNumSteps=0),
        monkeypatch,
        capsys,
    )
    run(
        "pre-tool-use",
        base(conversationId=conversation, **tool("overflow", names[-1])),
        monkeypatch,
        capsys,
    )
    run("stop", base(transcript, conversationId=conversation), monkeypatch, capsys)

    paths = list(agy_hooks.otlp_dir().glob("*.json"))
    assert len(paths) == 2
    messages = set()
    for path in paths:
        assert path.stat().st_size <= agy_hooks.OTLP_MAX_BYTES
        *decisions, completion = records(json.loads(path.read_text()))
        assert 0 < len(decisions) <= agy_hooks.AGY_MAX_BUFFERED_TOOLS
        if conversation == CONVERSATION:
            assert len(decisions) == 100
        assert [r["tool_name"] for r in decisions] == names[: len(decisions)]
        assert all(r["event.name"] == "agy.tool_decision" for r in decisions)
        assert call_digest("overflow", conversation) not in {
            r["call_id"] for r in decisions
        }
        assert completion["event.name"] == "agy.invocation_completed"
        assert {r["message.id"] for r in decisions} == {completion["message.id"]}
        messages.add(completion["message.id"])
    assert messages == {f"{conversation}:1", f"{conversation}:2"}
    assert state()["last_step_index"] == 2


def test_a_replaced_transcript_with_an_unread_step_before_the_offset(
    tmp_path, monkeypatch, capsys, clock
):
    unread = json.dumps(planner(2)) + "\n"
    empty = json.dumps({"type": "USER_INPUT", "content": ""}) + "\n"
    filler = {"type": "USER_INPUT", "content": "x" * (len(unread) - len(empty))}
    transcript = write_transcript(tmp_path, [planner(1), filler])
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    run("post-invocation", base(transcript), monkeypatch, capsys)
    before = state()
    assert before["byte_offset"] == transcript.stat().st_size

    # Same size, and the old offset still falls just after a newline.
    replacement = transcript.with_name("replacement.jsonl")
    replacement.write_text(json.dumps(planner(1)) + "\n" + unread)
    assert replacement.stat().st_size == before["byte_offset"]
    os.replace(replacement, transcript)
    assert transcript.stat().st_ino != before["transcript_inode"]
    run("post-invocation", base(transcript), monkeypatch, capsys)
    steps = [r["message.id"] for p in spooled() for r in records(p)]
    assert sorted(steps) == [f"{CONVERSATION}:1", f"{CONVERSATION}:2"]
    assert state()["transcript_inode"] == transcript.stat().st_ino


# ------------------------------------------------------------ OTLP spool


def spool_one(tmp_path, monkeypatch, capsys):
    transcript = write_transcript(tmp_path, [planner(1)])
    run("pre-invocation", base(initialNumSteps=0), monkeypatch, capsys)
    run("post-invocation", base(transcript), monkeypatch, capsys)
    (path,) = otlp_spool.spool_dir().glob("*.json")
    return path


@pytest.mark.parametrize(
    "status,retained",
    [
        (200, False),
        (204, False),
        (400, False),
        (401, True),
        (403, True),
        (404, False),
        (408, True),
        (422, False),
        (429, True),
        (500, True),
        (503, True),
        (0, True),
    ],
)
def test_drain_delivery(tmp_path, monkeypatch, capsys, status, retained):
    path = spool_one(tmp_path, monkeypatch, capsys)
    body = path.read_bytes()

    def post(url, **kwargs):
        assert url == "https://collector.example/otlp/v1/logs"
        assert kwargs["headers"]["x-tokenage-token"] == "fake-ingest"
        assert kwargs["content"] == body
        if not status:
            raise httpx.ConnectError("offline")
        return httpx.Response(status)

    monkeypatch.setattr(otlp_spool.httpx, "post", post)
    assert otlp_spool.drain_once() is not retained
    assert path.exists() is retained


def test_drain_uses_one_snapshot_of_the_credentials(tmp_path, monkeypatch, capsys):
    """The login changes while the drain starts: the files it picks, the token
    and the endpoint must still all belong to the same login."""
    old = {"server_url": "https://collector.example", "ingest_token": "fake-ingest"}
    mine = spool_one(tmp_path, monkeypatch, capsys)
    body = mine.read_bytes()
    sign_in()
    assert spool('{"resourceLogs":[],"other":1}')
    reads = []
    # The drain read the credentials just before the other account signed in.
    monkeypatch.setattr(
        otlp_spool, "load_credentials", lambda: reads.append(1) or dict(old)
    )
    monkeypatch.setattr(
        agy_hooks, "binding", lambda: pytest.fail("a second credentials read")
    )
    posted = []

    def post(url, **kwargs):
        posted.append((url, kwargs["headers"]["x-tokenage-token"], kwargs["content"]))
        return httpx.Response(200)

    monkeypatch.setattr(otlp_spool.httpx, "post", post)
    assert otlp_spool.drain_once()
    assert posted == [("https://collector.example/otlp/v1/logs", "fake-ingest", body)]
    assert reads == [1]
    (other,) = otlp_spool.spool_dir().glob("*.json")
    assert other.read_bytes() == b'{"resourceLogs":[],"other":1}'

    monkeypatch.setattr(
        otlp_spool, "load_credentials", lambda: (_ for _ in ()).throw(ValueError())
    )
    assert otlp_spool.drain_once()  # Unreadable credentials: nothing is sent.
    assert len(posted) == 1


def test_spooled_usage_survives_a_long_weekend(tmp_path, monkeypatch, capsys):
    path = spool_one(tmp_path, monkeypatch, capsys)
    age = path.stat().st_mtime - 6 * 86400
    os.utime(path, (age, age))
    monkeypatch.setattr(otlp_spool.httpx, "post", lambda *a, **kw: httpx.Response(503))
    assert otlp_spool.drain_once() is False and path.exists()
    assert otlp_spool.RETENTION_SECONDS == 7 * 86400
    age -= 2 * 86400
    os.utime(path, (age, age))
    assert otlp_spool.drain_once() and not path.exists()


def test_drain_posts_to_the_recorded_collector_oldest_first(
    tmp_path, monkeypatch, capsys
):
    from client.auth import remember_collector

    remember_collector("https://ingest.example/otlp/v1/logs")
    first = spool_one(tmp_path, monkeypatch, capsys)
    os.utime(first, (first.stat().st_mtime - 50, first.stat().st_mtime - 50))
    assert spool()
    posted = []

    def post(url, **kwargs):
        posted.append((url, kwargs["content"]))
        return httpx.Response(500 if len(posted) == 2 else 200)

    monkeypatch.setattr(otlp_spool.httpx, "post", post)
    assert otlp_spool.drain_once() is False  # The second file asks for backoff.
    assert [url for url, _ in posted] == ["https://ingest.example/otlp/v1/logs"] * 2
    assert posted[0][1] != b'{"resourceLogs":[]}' and not first.exists()
    assert len(list(otlp_spool.spool_dir().glob("*.json"))) == 1


def test_drain_drops_expired_and_current_logins_invalid_files(
    tmp_path, monkeypatch, capsys
):
    directory = otlp_spool.spool_dir()
    stale = spool_one(tmp_path, monkeypatch, capsys)
    os.utime(stale, (0, 0))
    bound = otlp_spool._identity()[2]
    (directory / f"big.{bound}.json").write_text(" " * (otlp_spool.MAX_BYTES + 1))
    (directory / f"bad.{bound}.json").write_text("[]")
    private = tmp_path / "private.json"
    private.write_text('{"resourceLogs":[]}')
    (directory / f"link.{bound}.json").symlink_to(private)
    other_binding = agy_hooks.binding_of(
        {"server_url": "https://new.example", "ingest_token": "new"}
    )
    expired = directory / f"expired.{other_binding}.json"
    expired.write_text('{"resourceLogs":[]}')
    os.utime(expired, (0, 0))
    monkeypatch.setattr(
        otlp_spool.httpx, "post", lambda *a, **kw: pytest.fail("must not upload")
    )
    assert otlp_spool.drain_once()
    assert list(directory.iterdir()) == []
    assert private.exists()

    from client.auth import clear_credentials

    clear_credentials()
    assert otlp_spool.drain_once()  # Signed out: nothing to do, no backoff.


def test_spool_cap_and_clear(monkeypatch):
    monkeypatch.setattr(agy_hooks, "OTLP_MAX_FILES", 1)
    assert [spool("{}", agy_hooks.OTLP_MAX_FILES) for _ in range(2)] == [True, False]
    assert len(list(otlp_spool.spool_dir().glob("*.json"))) == 1
    otlp_spool.clear_spool()
    assert list(otlp_spool.spool_dir().iterdir()) == []


def test_drain_loop_backs_off_after_a_failure(monkeypatch):
    waits = []

    class Stop:
        def is_set(self):
            return len(waits) >= 4

        def wait(self, delay):
            waits.append(delay)

    results = iter([False, False, True, RuntimeError()])

    def drain():
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    class Thread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(otlp_spool, "drain_once", drain)
    monkeypatch.setattr(otlp_spool.threading, "Event", Stop)
    monkeypatch.setattr(otlp_spool.threading, "Thread", Thread)
    otlp_spool.start_drain()
    assert waits == [2.0, 4.0, 1.0, 2.0]


# ----------------------------------------------------------- event spool


def write_reports() -> None:
    for number, agent in enumerate(("claude", "agy", "codex")):
        event_spool.write_report(
            SpoolReport(
                schema_version=1,
                agent=agent,
                session_id=f"session-{agent}",
                project="orchard",
                ts=agy_hooks.time.time_ns() // 1000 + number,
            )
        )


def test_an_event_an_older_server_rejects_does_not_take_the_batch_with_it(
    monkeypatch,
):
    write_reports()
    accepted, requests = [], []

    def post(url, **kwargs):
        agents = [event["agent"] for event in kwargs["json"]["events"]]
        requests.append(agents)
        if "agy" in agents:  # This server does not know the agent yet.
            return httpx.Response(422)
        accepted.extend(agents)
        return httpx.Response(200)

    monkeypatch.setattr(event_spool.httpx, "post", post)
    assert event_spool.drain_once()
    assert requests == [["claude", "agy", "codex"], ["claude"], ["agy"], ["codex"]]
    assert accepted == ["claude", "codex"]
    assert not list(event_spool.spool_dir().glob("*.json"))


@pytest.mark.parametrize("failure", [503, 401, 0])
def test_isolating_a_rejected_batch_keeps_what_could_not_be_sent(monkeypatch, failure):
    write_reports()

    def post(url, **kwargs):
        agents = [event["agent"] for event in kwargs["json"]["events"]]
        if len(agents) > 1:
            return httpx.Response(422)
        if agents == ["claude"]:
            return httpx.Response(200)
        if not failure:
            raise httpx.ConnectError("offline")
        return httpx.Response(failure)

    monkeypatch.setattr(event_spool.httpx, "post", post)
    assert event_spool.drain_once() is False
    kept = [
        SpoolReport.model_validate_json(path.read_text()).agent
        for path in event_spool.spool_dir().glob("*.json")
    ]
    assert sorted(kept) == ["agy", "codex"]
