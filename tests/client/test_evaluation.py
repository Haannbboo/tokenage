"""Client-driven local evaluation: discovery, evaluation, persistence, upload.

Everything is local: transcripts come from fixture files/DBs, the evaluator CLI
is a fake subprocess, and the server is a fake httpx. No real agent CLI runs.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import yaml

from client import evaluation
from protocol.evaluation import EvaluationCompletedReport, EvaluationResult

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER = "https://srv.example"
SOLVED = json.dumps(
    {
        "task_title": "Fix chart",
        "task_title_zh": "修复图表",
        "summary": "Done.",
        "outcome": "solved",
        "confidence": 0.9,
        "evidence": ["done"],
        "failure_reason": None,
    }
)


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def device(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TOKENAGE_HOME", str(home / ".tokenage"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".xdg-data"))
    monkeypatch.delenv("TOKENAGE_ROOT", raising=False)
    _signed_in(home)
    monkeypatch.setattr(
        evaluation.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "claude" else None,
    )
    return home


def _signed_in(
    home: Path, *, user_id: str = "u1", cli_token: str = "cli-token"
) -> None:
    path = home / ".tokenage" / "credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "server_url": SERVER,
                "user_id": user_id,
                "email": "you@example.com",
                "cli_token": cli_token,
                "ingest_token": "ingest",
                "otlp_logs_endpoint": "https://srv.example/v1/logs",
            }
        ),
        encoding="utf-8",
    )


class FakePost:
    """Records POSTs; per-endpoint status codes make upload failures injectable."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.status_codes = {"started": 204, "completed": 204}

    def __call__(self, url, *, json=None, headers=None, timeout=None):
        kind = url.rsplit("/", 1)[-1]
        self.calls.append(
            {
                "url": url,
                "kind": kind,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return httpx.Response(
            self.status_codes.get(kind, 404), request=httpx.Request("POST", url)
        )

    def kinds(self) -> list[str]:
        return [call["kind"] for call in self.calls]

    def completed_calls(self) -> list[dict]:
        return [call for call in self.calls if call["kind"] == "completed"]


class FakeRun:
    """A fake evaluator CLI; ``on_call`` can mutate the transcript mid-run."""

    def __init__(
        self,
        stdout: str = SOLVED,
        stderr: str = "",
        returncode: int = 0,
        on_call=None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.on_call = on_call
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.on_call is not None:
            self.on_call(kwargs)
        return subprocess.CompletedProcess(
            args=kwargs["args"],
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


@pytest.fixture
def http(device: Path, monkeypatch: pytest.MonkeyPatch) -> FakePost:
    fake = FakePost()
    monkeypatch.setattr(evaluation.httpx, "post", fake)
    return fake


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    fake = FakeRun()
    monkeypatch.setattr(evaluation.subprocess, "run", fake)
    return fake


# ------------------------------------------------------- transcript fixtures


CLAUDE_ID = "claude-session-1"
CODEX_ID = "11111111-2222-3333-4444-555555555555"


def _claude_record(message_id: str, role: str, content: list) -> dict:
    return {
        "type": role,
        "uuid": message_id,
        "message": {"id": message_id, "role": role, "content": content},
    }


def _write_claude_session(
    home: Path,
    session_id: str = CLAUDE_ID,
    *,
    user_text: str = "Fix the chart",
    mtime: float | None = None,
) -> Path:
    path = home / ".claude" / "projects" / "-tmp-project" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        _claude_record("u1", "user", [{"type": "text", "text": user_text}]),
        _claude_record("a1", "assistant", [{"type": "text", "text": "Chart fixed"}]),
    ]
    path.write_text(
        "\n".join(json.dumps(record) for record in records), encoding="utf-8"
    )
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _write_codex_session(
    home: Path,
    session_id: str = CODEX_ID,
    *,
    user_text: str = "Fix the chart",
    mtime: float | None = None,
) -> Path:
    path = (
        home
        / ".codex"
        / "sessions"
        / "2026"
        / "05"
        / "14"
        / f"rollout-2026-05-14T00-00-00-{session_id}.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": user_text}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Chart fixed"}],
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(record) for record in records), encoding="utf-8"
    )
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _write_sqlite_transcript(
    home: Path, client_source: str, session_id: str, *, part_updated: int = 1
) -> Path:
    db_path = home / ".xdg-data" / client_source / f"{client_source}.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE message (id text PRIMARY KEY, session_id text NOT NULL, "
            "time_created integer NOT NULL, time_updated integer NOT NULL, "
            "data text NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE part (id text PRIMARY KEY, message_id text NOT NULL, "
            "session_id text NOT NULL, time_created integer NOT NULL, "
            "time_updated integer NOT NULL, data text NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO message VALUES (?, ?, ?, ?, ?)",
            [
                ("msg-user", session_id, 1, 1, json.dumps({"role": "user"})),
                ("msg-assistant", session_id, 2, 2, json.dumps({"role": "assistant"})),
            ],
        )
        connection.executemany(
            "INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    "part-user",
                    "msg-user",
                    session_id,
                    1,
                    1,
                    json.dumps({"type": "text", "text": "Fix OpenCode sessions"}),
                ),
                (
                    "part-assistant",
                    "msg-assistant",
                    session_id,
                    2,
                    part_updated,
                    json.dumps({"type": "text", "text": "OpenCode sessions fixed"}),
                ),
            ],
        )
    return db_path


def _candidates_by_id() -> dict[str, evaluation.LocalSessionCandidate]:
    return {
        candidate.session_id: candidate
        for candidate in evaluation.discover_local_sessions()
    }


def _record(session_id: str) -> dict:
    record = evaluation._read_result(session_id)
    assert record is not None
    return record


# ----------------------------------------------------------------- discovery


def test_discovery_orders_oldest_last_activity_first_tie_session_id(device):
    _write_claude_session(device, "claude-b", mtime=2_000_000)
    _write_claude_session(device, "claude-a", mtime=2_000_000)
    _write_claude_session(device, "claude-old", mtime=1_000_000)

    candidates = evaluation.discover_local_sessions()

    assert [candidate.session_id for candidate in candidates] == [
        "claude-old",
        "claude-a",
        "claude-b",
    ]


def test_discovery_excludes_claude_subagent_transcripts(device):
    _write_claude_session(device, "parent")
    _write_claude_session(device, "agent-old")
    child = (
        device
        / ".claude"
        / "projects"
        / "project"
        / "parent"
        / "subagents"
        / "child.jsonl"
    )
    child.parent.mkdir(parents=True)
    child.write_text("{}", encoding="utf-8")
    assert [row.session_id for row in evaluation.discover_local_sessions()] == [
        "parent"
    ]


def test_sqlite_activity_and_revision_come_from_message_and_part_rows(device):
    db_path = _write_sqlite_transcript(
        device, "opencode", "ses_abc", part_updated=1_791_957_000_000
    )
    # The shared DB file's mtime must not decide session activity.
    os.utime(db_path, (1, 1))

    candidate = _candidates_by_id()["ses_abc"]

    assert candidate.client_source == "opencode"
    assert candidate.last_activity == 1_791_957_000.0
    assert candidate.revision == "sqlite:1791957000000"


def test_discovery_ignores_too_recent_sessions(device, cli, http):
    now = time.time()
    _write_claude_session(device, "quiet", mtime=now - 10_000)
    _write_claude_session(device, "recent", mtime=now - 100)

    evaluation.run_evaluation_pass(now=now)

    assert len(cli.calls) == 1
    assert evaluation._read_result("quiet") is not None
    assert evaluation._read_result("recent") is None


# ------------------------------------------------- evaluation and coverage


def test_pass_evaluates_oldest_quiet_session_and_uploads(device, cli, http):
    now = time.time()
    _write_claude_session(device, "s-old", mtime=now - 10_000)
    _write_claude_session(device, "s-new", mtime=now - 5_000)

    evaluation.run_evaluation_pass(now=now)

    assert len(cli.calls) == 1
    assert "Fix the chart" in cli.calls[0]["input"]
    assert cli.calls[0]["timeout"] == evaluation.EVALUATION_TIMEOUT_SECONDS
    assert http.kinds() == ["started", "completed"]
    assert [call["url"] for call in http.calls] == [
        f"{SERVER}/evaluation-jobs/started",
        f"{SERVER}/evaluation-jobs/completed",
    ]
    assert http.calls[0]["headers"] == {"Authorization": "Bearer cli-token"}

    record = _record("s-old")
    assert record["submitted"] is True
    assert record["revision"].startswith("jsonl:")
    completed = EvaluationCompletedReport.model_validate(record["completed"])
    assert completed.status == "succeeded"
    assert completed.result is not None
    assert completed.result.outcome == "solved"
    assert completed.session_id == "s-old"
    assert completed.evaluator_type == "claude"
    assert evaluation._read_result("s-new") is None


def test_local_result_covers_revision_until_session_resumes(device, cli, http):
    now = time.time()
    path = _write_claude_session(device, "resumed", mtime=now - 10_000)
    evaluation.run_evaluation_pass(now=now)
    assert len(cli.calls) == 1
    first_revision = _record("resumed")["revision"]

    evaluation.run_evaluation_pass(now=now)
    assert len(cli.calls) == 1  # covered: no re-evaluation

    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n"
            + json.dumps(
                _claude_record("a2", "assistant", [{"type": "text", "text": "More"}])
            )
        )
    evaluation.run_evaluation_pass(now=time.time() + 601)
    assert len(cli.calls) == 2
    assert _record("resumed")["revision"] != first_revision


def test_session_changed_during_evaluation_stays_reeligible(device, http, monkeypatch):
    now = time.time()
    path = _write_claude_session(device, "moving", mtime=now - 10_000)

    def append_during_eval(kwargs):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n"
                + json.dumps(
                    _claude_record(
                        "a2", "assistant", [{"type": "text", "text": "Late"}]
                    )
                )
            )

    fake = FakeRun(on_call=append_during_eval)
    monkeypatch.setattr(evaluation.subprocess, "run", fake)

    evaluation.run_evaluation_pass(now=now)
    first_revision = _record("moving")["revision"]
    assert first_revision != _candidates_by_id()["moving"].revision

    revision_before_second = _candidates_by_id()["moving"].revision
    evaluation.run_evaluation_pass(now=time.time() + 601)
    assert len(fake.calls) == 2
    assert _record("moving")["revision"] == revision_before_second
    assert revision_before_second != first_revision


def test_upload_failure_retries_same_job_without_reevaluation(device, cli, http):
    now = time.time()
    _write_claude_session(device, "retry-me", mtime=now - 10_000)
    http.status_codes["completed"] = 500

    evaluation.run_evaluation_pass(now=now)
    assert len(cli.calls) == 1
    record = _record("retry-me")
    assert record["submitted"] is False
    job_id = record["job_id"]

    http.status_codes["completed"] = 204
    evaluation.run_evaluation_pass(now=now)

    assert len(cli.calls) == 1  # uploaded, never re-evaluated
    assert record["job_id"] == job_id
    assert _record("retry-me")["submitted"] is True
    retried = http.completed_calls()[-1]["json"]
    assert retried["job_id"] == job_id
    assert retried["started_at"] == record["completed"]["started_at"]


@pytest.mark.parametrize("status", [400, 404, 409, 422])
def test_permanently_rejected_results_stay_local_without_repeated_uploads(
    device, cli, http, status
):
    _write_claude_session(device, "untracked", mtime=time.time() - 10_000)
    http.status_codes["completed"] = status
    evaluation.run_evaluation_pass()
    evaluation.run_evaluation_pass()
    record = _record("untracked")
    assert record["submitted"] is False
    assert record["submission_error"] == status
    assert len(cli.calls) == 1
    assert len(http.completed_calls()) == 1


def test_upload_retries_are_bounded_per_pass(device, http):
    identity = evaluation.load_evaluation_identity()
    for index in range(evaluation.MAX_PENDING_UPLOADS_PER_PASS + 2):
        evaluation._write_result(
            {
                "session_id": f"pending-{index}",
                "server_url": identity.server_url,
                "user_id": identity.user_id,
                "submitted": False,
                "completed": {"job_id": f"job-{index}"},
            }
        )
    http.status_codes["completed"] = 503

    evaluation.retry_pending_uploads(identity)

    assert len(http.completed_calls()) == evaluation.MAX_PENDING_UPLOADS_PER_PASS
    evaluation.retry_pending_uploads(identity)
    assert (
        len({call["json"]["job_id"] for call in http.completed_calls()})
        == evaluation.MAX_PENDING_UPLOADS_PER_PASS + 2
    )


def test_failed_started_upload_does_not_block_evaluation(device, cli, http):
    now = time.time()
    _write_claude_session(device, "started-down", mtime=now - 10_000)
    http.status_codes["started"] = 500

    evaluation.run_evaluation_pass(now=now)

    assert len(cli.calls) == 1
    assert http.kinds() == ["started", "completed"]
    assert _record("started-down")["submitted"] is True


@pytest.mark.parametrize("status", [401, 403])
def test_explicit_identity_rejection_pauses_paid_evaluation(device, cli, http, status):
    _write_claude_session(device, "revoked", mtime=time.time() - 10_000)
    http.status_codes["started"] = status
    evaluation.run_evaluation_pass()
    assert not cli.calls
    assert http.kinds() == ["started"]
    assert evaluation._read_result("revoked") is None


def test_revoked_upload_stops_retries_and_new_evaluations(device, cli, http):
    _write_claude_session(device, "pending", mtime=time.time() - 10_000)
    http.status_codes["completed"] = 500
    evaluation.run_evaluation_pass()
    _write_claude_session(device, "next", mtime=time.time() - 10_000)
    http.status_codes["completed"] = 401
    evaluation.run_evaluation_pass()
    assert len(cli.calls) == 1
    assert evaluation._read_result("next") is None


def test_account_switch_does_not_upload_previous_users_result(device, cli, http):
    now = time.time()
    _write_claude_session(device, "scoped", mtime=now - 10_000)
    http.status_codes["completed"] = 500
    evaluation.run_evaluation_pass(now=now)
    assert _record("scoped")["submitted"] is False
    assert len(http.completed_calls()) == 1

    _signed_in(device, user_id="u2", cli_token="other-token")
    http.status_codes["completed"] = 204
    evaluation.run_evaluation_pass(now=now)
    assert len(http.completed_calls()) == 1  # not uploaded to the new account
    assert _record("scoped")["submitted"] is False

    _signed_in(device, user_id="u1")
    evaluation.run_evaluation_pass(now=now)
    assert len(http.completed_calls()) == 2
    assert _record("scoped")["submitted"] is True


def test_identity_change_during_run_keeps_result_local(device, http, monkeypatch):
    now = time.time()
    _write_claude_session(device, "switched", mtime=now - 10_000)

    def switch_account(kwargs):
        _signed_in(device, user_id="u2", cli_token="other-token")

    fake = FakeRun(on_call=switch_account)
    monkeypatch.setattr(evaluation.subprocess, "run", fake)

    evaluation.run_evaluation_pass(now=now)

    assert http.kinds() == ["started"]  # bound to u1; completion not uploaded
    assert _record("switched")["submitted"] is False
    assert _record("switched")["user_id"] == "u1"


def test_signed_out_client_evaluates_nothing(device, cli, http):
    now = time.time()
    _write_claude_session(device, "offline", mtime=now - 10_000)
    (device / ".tokenage" / "credentials.json").unlink()

    evaluation.run_evaluation_pass(now=now)

    assert cli.calls == []
    assert http.calls == []
    assert not evaluation.evaluations_dir().exists()


def test_no_evaluator_installed_skips(device, cli, http, monkeypatch):
    now = time.time()
    monkeypatch.setattr(evaluation.shutil, "which", lambda name: None)
    _write_claude_session(device, "no-cli", mtime=now - 10_000)

    evaluation.run_evaluation_pass(now=now)

    assert cli.calls == []
    assert http.calls == []
    assert evaluation._read_result("no-cli") is None


def test_explicit_unavailable_evaluator_skips(device, cli, http, monkeypatch):
    (device / ".tokenage" / "client.yaml").write_text(
        "evaluation:\n  evaluator: codex\n", encoding="utf-8"
    )
    monkeypatch.setattr(evaluation.shutil, "which", lambda name: None)
    _write_claude_session(device, "explicit", mtime=time.time() - 10_000)

    evaluation.run_evaluation_pass(now=time.time())

    assert cli.calls == []


def test_auto_disabled_skips_new_evaluations_but_retries_uploads(device, cli, http):
    now = time.time()
    _write_claude_session(device, "disabled", mtime=now - 10_000)
    http.status_codes["completed"] = 500
    evaluation.run_evaluation_pass(now=now)
    assert len(cli.calls) == 1

    (device / ".tokenage" / "client.yaml").write_text(
        "evaluation:\n  auto_enabled: false\n", encoding="utf-8"
    )
    _write_claude_session(device, "disabled-2", mtime=now - 10_000)
    http.status_codes["completed"] = 204
    evaluation.run_evaluation_pass(now=now)

    assert len(cli.calls) == 1  # no new evaluation
    assert _record("disabled")["submitted"] is True  # pending upload retried
    assert evaluation._read_result("disabled-2") is None


# --------------------------------------------------------------- no-op paths


def test_evaluator_session_is_no_op_without_invoking_any_cli(device, cli, http):
    _write_codex_session(
        device,
        user_text=(
            f"{evaluation.EVALUATION_PROMPT}\n\n"
            "<session_transcript>...</session_transcript>"
        ),
        mtime=time.time() - 10_000,
    )

    evaluation.run_evaluation_pass(now=time.time())

    assert cli.calls == []
    completed = EvaluationCompletedReport.model_validate(_record(CODEX_ID)["completed"])
    assert completed.result is not None
    assert completed.result.outcome == "no_op"


def test_claude_mem_observer_session_is_no_op(device, cli, http):
    path = (
        device
        / ".claude"
        / "projects"
        / "-home--claude-mem-observer-sessions"
        / "sess-mem.jsonl"
    )
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    os.utime(path, (time.time() - 10_000,) * 2)

    evaluation.run_evaluation_pass(now=time.time())

    assert cli.calls == []
    completed = EvaluationCompletedReport.model_validate(
        _record("sess-mem")["completed"]
    )
    assert completed.result is not None
    assert completed.result.outcome == "no_op"


def test_missing_transcript_text_completes_unknown_without_running_cli(
    device, cli, http
):
    _write_sqlite_transcript(device, "opencode", "ses_empty")
    db_path = device / ".xdg-data" / "opencode" / "opencode.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM part")

    evaluation.run_evaluation_pass(now=time.time())

    assert cli.calls == []
    completed = EvaluationCompletedReport.model_validate(
        _record("ses_empty")["completed"]
    )
    assert completed.result is not None
    assert completed.result.outcome == "unknown"


# --------------------------------------------------------- failures/bounds


def test_timeout_persists_minimal_failed_completion(device, http, monkeypatch):
    _write_claude_session(device, "slow", mtime=time.time() - 10_000)

    def timeout(**kwargs):
        raise subprocess.TimeoutExpired(cmd=kwargs["args"], timeout=300)

    monkeypatch.setattr(evaluation.subprocess, "run", timeout)

    evaluation.run_evaluation_pass(now=time.time())

    completed = EvaluationCompletedReport.model_validate(_record("slow")["completed"])
    assert completed.status == "failed"
    assert completed.result is None
    assert completed.error == "Evaluation agent timed out"
    assert http.kinds() == ["started", "completed"]
    assert _record("slow")["submitted"] is True
    # Deliberately preserve the previous automatic-evaluation policy: failures
    # cover this revision, rather than repeatedly spending credits on it.
    evaluation.run_evaluation_pass(now=time.time())
    assert http.kinds() == ["started", "completed"]


def test_clock_moving_backwards_does_not_lose_evaluation_result(
    device, cli, http, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    _write_claude_session(device, "clock", mtime=time.time() - 10_000)
    started = datetime.now(UTC)
    ticks = iter([started, started - timedelta(seconds=60)])

    class Clock:
        @staticmethod
        def now(tz):
            return next(ticks)

    monkeypatch.setattr(evaluation, "datetime", Clock)
    evaluation.run_evaluation_pass()
    record = _record("clock")
    assert record["submitted"] is True
    assert record["completed"]["finished_at"] == record["completed"]["started_at"]
    assert len(cli.calls) == 1


def test_result_is_bounded_and_scrubbed_before_persistence(device, http, monkeypatch):
    _write_claude_session(device, "leaky", mtime=time.time() - 10_000)
    stdout = json.dumps(
        {
            "task_title": "x" * 400,
            "task_title_zh": None,
            "summary": "s" * 4000,
            "outcome": "solved",
            "confidence": 0.5,
            "evidence": [
                "api_key=sk-abcdef123456 /home/hanbo/private/notes.txt",
                "more",
            ],
            "failure_reason": None,
            "project": "p" * 300,
        }
    )
    monkeypatch.setattr(evaluation.subprocess, "run", FakeRun(stdout=stdout))

    evaluation.run_evaluation_pass(now=time.time())

    result = EvaluationResult.model_validate(_record("leaky")["completed"]["result"])
    assert result.task_title is not None and len(result.task_title) <= 256
    assert result.summary is not None and len(result.summary) <= 2048
    assert result.project is not None and len(result.project) <= 128
    text = json.dumps(_record("leaky"))
    assert "sk-abcdef123456" not in text
    assert "/home/hanbo/private/notes.txt" not in text


def test_agent_failure_does_not_log_subprocess_output(
    device, http, monkeypatch, caplog
):
    _write_claude_session(device, "failing", mtime=time.time() - 10_000)
    monkeypatch.setattr(
        evaluation.subprocess,
        "run",
        FakeRun(
            stdout="private transcript sentinel api_key=sk-stdout-secret",
            stderr=(
                "Authorization: Bearer stdout-token "
                "--api-key cli-secret /home/hanbo/token.txt"
            ),
            returncode=1,
        ),
    )

    evaluation.run_evaluation_pass(now=time.time())

    assert "Evaluation agent subprocess failed" in caplog.text
    assert "private transcript sentinel" not in caplog.text
    assert "sk-stdout-secret" not in caplog.text
    assert "stdout-token" not in caplog.text
    assert "cli-secret" not in caplog.text
    assert "/home/hanbo/token.txt" not in caplog.text


def test_safe_session_filename_cannot_escape_the_evaluations_dir(device):
    for session_id in ("../escape", "..", "a/b/c", "x" * 400):
        name = evaluation.safe_session_filename(session_id)
        assert "/" not in name and "\\" not in name
        assert (
            evaluation._result_path(session_id).parent == evaluation.evaluations_dir()
        )
    assert evaluation.safe_session_filename("x" * 400).endswith(".json")


# ---------------------------------------------------------------- invocations


def test_codex_invocation_disables_exporters_and_uses_ephemeral_sandbox():
    invocation = evaluation.build_evaluator_invocation(
        "Transcript text", evaluator="codex"
    )
    assert invocation.command == [
        "codex",
        "-c",
        'otel.exporter="none"',
        "-c",
        'otel.trace_exporter="none"',
        "-c",
        'otel.metrics_exporter="none"',
        "-c",
        "otel.log_user_prompt=false",
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ignore-rules",
        "-",
    ]
    assert "Transcript text" in invocation.stdin
    assert "Return ONLY valid JSON" in invocation.stdin
    assert invocation.env["OTEL_SDK_DISABLED"] == "true"
    assert invocation.env["OTEL_LOGS_EXPORTER"] == "none"


def test_claude_invocation_disables_tools_hooks_and_telemetry():
    invocation = evaluation.build_evaluator_invocation(
        "Transcript text", evaluator="claude"
    )
    assert invocation.command[:7] == [
        "claude",
        "--print",
        "--no-session-persistence",
        "--tools",
        "",
        "--strict-mcp-config",
        "--disable-slash-commands",
    ]
    settings = json.loads(
        invocation.command[invocation.command.index("--settings") + 1]
    )
    assert settings["disableAllHooks"] is True
    assert settings["env"]["CLAUDE_CODE_ENABLE_TELEMETRY"] == "0"
    assert "Transcript text" in invocation.stdin
    assert invocation.env["OTEL_SDK_DISABLED"] == "true"
    assert invocation.env["CLAUDE_CODE_NO_AUTO_GIT"] == "1"
    assert invocation.env["CLAUDE_CODE_NO_BROWSER"] == "1"
    assert invocation.env["CLAUDE_CODE_HEADLESS"] == "1"


def test_opencode_invocation_feeds_stdin_and_skips_plugins():
    invocation = evaluation.build_evaluator_invocation(
        "Transcript text", evaluator="opencode"
    )
    assert invocation.command == ["opencode", "run", "--pure"]
    assert "--model" not in invocation.command  # uses its own configured model
    assert "Transcript text" in invocation.stdin
    assert invocation.env["OPENCODE_DISABLE_AUTOUPDATE"] == "true"
    assert invocation.env["OTEL_SDK_DISABLED"] == "true"


def test_build_evaluator_invocation_rejects_invalid_agent():
    with pytest.raises(ValueError, match="Unsupported evaluator agent"):
        evaluation.build_evaluator_invocation("text", evaluator="invalid")


def test_worker_survives_failed_passes_and_stops(monkeypatch):
    calls: list[int] = []

    def failing_pass() -> None:
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(evaluation, "run_evaluation_pass", failing_pass)
    worker = evaluation.EvaluationWorker(pass_interval=0.01)
    worker.start()
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    worker.stop()

    assert calls


# --------------------------------------------------------- transcript readers


def test_load_codex_transcript_reads_user_assistant_text_only(device):
    path = _write_codex_session(device)
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    records.insert(
        0,
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "Developer rules"}],
            },
        },
    )
    records.append(
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "shell",
                "arguments": "secret tool input",
            },
        }
    )
    path.write_text(
        "\n".join(json.dumps(record) for record in records), encoding="utf-8"
    )

    transcript = evaluation.load_session_transcript("codex", CODEX_ID)

    assert "USER:\nFix the chart" in transcript
    assert "ASSISTANT:\nChart fixed" in transcript
    assert "Developer rules" not in transcript
    assert "secret tool input" not in transcript


def test_load_claude_transcript_skips_tool_results(device):
    path = _write_claude_session(device)
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                _claude_record("u1", "user", [{"type": "text", "text": "Fix backend"}]),
                _claude_record(
                    "a1",
                    "assistant",
                    [
                        {"type": "text", "text": "Updated backend"},
                        {"type": "tool_use", "name": "Bash", "input": "secret"},
                    ],
                ),
                _claude_record(
                    "u2",
                    "user",
                    [{"type": "tool_result", "content": "large command output"}],
                ),
            ]
        ),
        encoding="utf-8",
    )

    transcript = evaluation.load_session_transcript("claude-code", CLAUDE_ID)

    assert "USER:\nFix backend" in transcript
    assert "ASSISTANT:\nUpdated backend" in transcript
    assert "large command output" not in transcript
    assert "secret" not in transcript


def test_load_opencode_and_kilo_transcripts_read_sqlite_text_only(device):
    _write_sqlite_transcript(device, "opencode", "ses_oc")
    _write_sqlite_transcript(device, "kilo", "ses_kilo")

    opencode = evaluation.load_session_transcript("opencode", "ses_oc")
    kilo = evaluation.load_session_transcript("kilo", "ses_kilo")

    assert "USER:\nFix OpenCode sessions" in opencode
    assert "ASSISTANT:\nOpenCode sessions fixed" in opencode
    assert "Session source: kilo" in kilo


def test_extract_content_tool_summaries_and_redaction():
    content = [
        {"type": "text", "text": "Let me read that file."},
        {"type": "thinking", "text": "internal reasoning"},
        {
            "type": "tool_use",
            "name": "Read",
            "input": {"file_path": "/Users/me/project/api.py"},
        },
        {
            "type": "tool_use",
            "name": "Bash",
            "input": {"command": "export API_KEY=sk-abc123secret && curl"},
        },
        {
            "type": "tool_use",
            "name": "Edit",
            "input": {"file_path": "/Users/me/project/README.md"},
        },
    ]
    result = evaluation._extract_content(content)
    assert result is not None
    assert "Let me read that file." in result
    assert "internal reasoning" not in result
    assert "[tool:Read] /Users/me/project/api.py" in result
    assert "[tool:Edit] /Users/me/project/README.md" in result
    assert "sk-abc123secret" not in result
    assert evaluation._extract_content([]) is None
    assert evaluation._extract_content("hello") == "hello"


@pytest.mark.parametrize(
    "output",
    [
        '{"outcome":"great","summary":null,"confidence":0.5,"evidence":[]}',
        "not json at all",
    ],
)
def test_parse_evaluation_output_rejects_invalid(output):
    with pytest.raises(ValueError):
        evaluation.parse_evaluation_output(output)


def test_parse_evaluation_output_accepts_cli_noise_and_clears_no_op_titles():
    parsed = evaluation.parse_evaluation_output(
        """
metricReader is deprecated
{
  "task_title": "Update tests",
  "task_title_zh": "更新测试",
  "summary": "Updated.",
  "outcome": "no_op",
  "confidence": 1.0,
  "evidence": ["trivial"],
  "failure_reason": null,
  "project": "tokenage"
}
"""
    )
    assert parsed["outcome"] == "no_op"
    assert parsed["task_title"] is None
    assert parsed["task_title_zh"] is None
    assert parsed["project"] is None


def test_parse_evaluation_output_rejects_invalid_confidence():
    with pytest.raises(ValueError, match="Evaluation confidence"):
        evaluation.parse_evaluation_output(
            '{"outcome":"solved","confidence":1.1,"evidence":[]}'
        )


# ------------------------------------------------------------------ config


def test_load_evaluation_config_defaults_and_values(device):
    assert evaluation.load_evaluation_config() == evaluation.EvaluationConfig()

    (device / ".tokenage" / "client.yaml").write_text(
        "proxy:\n  port: 4000\nevaluation:\n"
        "  auto_enabled: false\n"
        "  quiet_delay_seconds: 30\n"
        "  evaluator: codex\n",
        encoding="utf-8",
    )
    config = evaluation.load_evaluation_config()
    assert config == evaluation.EvaluationConfig(
        auto_enabled=False,
        quiet_delay_seconds=30,
        evaluator="codex",
    )


def test_ensure_evaluation_configured_writes_defaults_once(device):
    evaluation.ensure_evaluation_configured(available=["codex", "claude"])
    config_path = device / ".tokenage" / "client.yaml"
    text = config_path.read_text(encoding="utf-8")
    assert "evaluator: claude" in text
    assert "quiet_delay_seconds: 600" in text

    evaluation.ensure_evaluation_configured(available=["codex"])
    assert config_path.read_text(encoding="utf-8") == text


def test_ensure_evaluation_configured_preserves_proxy_and_explicit_evaluator(device):
    config_path = device / ".tokenage" / "client.yaml"
    original = (
        "proxy:\n"
        "  providers:\n"
        "    DeepSeek:\n"
        "      base_url: https://api.deepseek.com\n"
        "      api_key: sk-secret\n"
        "evaluation:\n"
        "  auto_enabled: false\n"
        "  evaluator: codex\n"
    )
    config_path.write_text(original, encoding="utf-8")

    evaluation.ensure_evaluation_configured(available=["claude"])

    assert config_path.read_text(encoding="utf-8") == original


def test_ensure_evaluation_configured_inserts_into_existing_section(device):
    config_path = device / ".tokenage" / "client.yaml"
    config_path.write_text(
        "proxy:\n  port: 4000\nevaluation:\n  auto_enabled: false\n",
        encoding="utf-8",
    )

    evaluation.ensure_evaluation_configured(available=["claude"])

    text = config_path.read_text(encoding="utf-8")
    assert "  auto_enabled: false\n  evaluator: claude\n" in text
    assert evaluation.load_evaluation_config().evaluator == "claude"


def test_ensure_evaluation_configured_without_agents_writes_nothing(device):
    evaluation.ensure_evaluation_configured(available=[])
    assert not (device / ".tokenage" / "client.yaml").exists()


@pytest.mark.parametrize(
    "original",
    [
        "evaluation:\n  auto_enabled: false",
        "evaluation: {auto_enabled: false}\nproxy: {port: 4000}\n",
        "evaluation: null\nproxy: {port: 4000}\n",
    ],
)
def test_evaluator_initialization_preserves_valid_yaml_and_existing_values(
    device, original
):
    path = device / ".tokenage" / "client.yaml"
    path.write_text(original, encoding="utf-8")
    before = yaml.safe_load(original)

    evaluation.ensure_evaluation_configured(available=["claude"])

    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert after["evaluation"]["evaluator"] == "claude"
    if isinstance(before.get("evaluation"), dict):
        assert after["evaluation"]["auto_enabled"] is False
    assert after.get("proxy") == before.get("proxy")
    assert path.stat().st_mode & 0o777 == 0o600


def test_default_initialization_detects_actual_installed_evaluators(
    device, monkeypatch
):
    monkeypatch.setattr(evaluation.shutil, "which", lambda name: None)
    assert evaluation.ensure_evaluation_configured() is None
    assert not (device / ".tokenage" / "client.yaml").exists()


@pytest.mark.parametrize(
    "contents",
    [
        "evaluation: [",
        "evaluation: []",
        "[]",
        "evaluation: {auto_enabled: 'false'}",
        "evaluation: {evaluator: []}",
    ],
)
def test_invalid_config_does_not_enable_billable_evaluation(
    device, cli, http, contents
):
    _write_claude_session(device, "disabled", mtime=time.time() - 10_000)
    (device / ".tokenage" / "client.yaml").write_text(contents, encoding="utf-8")

    evaluation.run_evaluation_pass()

    assert not cli.calls
    assert not http.calls


def test_resolve_evaluator_prefers_claude_then_codex_then_opencode(device, monkeypatch):
    for installed, expected in (
        ({"claude", "codex", "opencode"}, "claude"),
        ({"codex", "opencode"}, "codex"),
        ({"opencode"}, "opencode"),
        (set(), None),
    ):
        monkeypatch.setattr(
            evaluation.shutil,
            "which",
            lambda name, installed=installed: name if name in installed else None,
        )
        assert evaluation.resolve_evaluator_agent(None) == expected

    monkeypatch.setattr(evaluation.shutil, "which", lambda name: None)
    assert evaluation.resolve_evaluator_agent("codex") is None
    assert evaluation.resolve_evaluator_agent("not-real") is None


# -------------------------------------------------------------- standalone


def test_client_evaluation_imports_without_server_stack():
    result = subprocess.run(
        [
            sys.executable,
            "-P",
            "-c",
            "import sys, client.evaluation; "
            "print([m for m in ('src', 'sqlalchemy', 'fastapi') if m in sys.modules])",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
