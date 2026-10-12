from __future__ import annotations

import json
import sqlite3
import subprocess

import pytest


def _write_opencode_transcript(home, session_id: str = "sess-opencode") -> None:
    db_path = home / ".xdg-data" / "opencode" / "opencode.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE message (
                id text PRIMARY KEY,
                session_id text NOT NULL,
                time_created integer NOT NULL,
                time_updated integer NOT NULL,
                data text NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE part (
                id text PRIMARY KEY,
                message_id text NOT NULL,
                session_id text NOT NULL,
                time_created integer NOT NULL,
                time_updated integer NOT NULL,
                data text NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO message VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "msg-user",
                    session_id,
                    1,
                    1,
                    json.dumps({"role": "user"}),
                ),
                (
                    "msg-assistant",
                    session_id,
                    2,
                    2,
                    json.dumps({"role": "assistant"}),
                ),
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
                    "part-reasoning",
                    "msg-assistant",
                    session_id,
                    2,
                    2,
                    json.dumps(
                        {"type": "reasoning", "text": "private reasoning omitted"}
                    ),
                ),
                (
                    "part-assistant",
                    "msg-assistant",
                    session_id,
                    3,
                    3,
                    json.dumps({"type": "text", "text": "OpenCode sessions fixed"}),
                ),
            ],
        )


def _write_kilo_transcript(home, session_id: str = "sess-kilo") -> None:
    db_path = home / ".xdg-data" / "kilo" / "kilo.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE message (
                id text PRIMARY KEY,
                session_id text NOT NULL,
                time_created integer NOT NULL,
                time_updated integer NOT NULL,
                data text NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE part (
                id text PRIMARY KEY,
                message_id text NOT NULL,
                session_id text NOT NULL,
                time_created integer NOT NULL,
                time_updated integer NOT NULL,
                data text NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO message VALUES (?, ?, ?, ?, ?)",
            [
                (
                    "msg-user",
                    session_id,
                    1,
                    1,
                    json.dumps({"role": "user"}),
                ),
                (
                    "msg-assistant",
                    session_id,
                    2,
                    2,
                    json.dumps({"role": "assistant"}),
                ),
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
                    json.dumps({"type": "text", "text": "Fix Kilo sessions"}),
                ),
                (
                    "part-reasoning",
                    "msg-assistant",
                    session_id,
                    2,
                    2,
                    json.dumps(
                        {"type": "reasoning", "text": "private reasoning omitted"}
                    ),
                ),
                (
                    "part-assistant",
                    "msg-assistant",
                    session_id,
                    3,
                    3,
                    json.dumps({"type": "text", "text": "Kilo sessions fixed"}),
                ),
            ],
        )


def test_build_evaluator_command_uses_central_codex_ephemeral(evaluation_module):
    invocation = evaluation_module.build_evaluator_invocation("Transcript text")

    assert invocation.command == [
        "codex",
        "-c",
        "otel.enabled=false",
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ignore-rules",
        "-",
    ]
    assert "Transcript text" in invocation.stdin
    assert invocation.env["OTEL_SDK_DISABLED"] == "true"
    assert invocation.env["OTEL_LOGS_EXPORTER"] == "none"


def test_load_codex_transcript_reads_user_assistant_text_only(
    evaluation_module, isolated_home
):
    session_dir = isolated_home / ".codex" / "sessions" / "2026" / "05" / "14"
    session_dir.mkdir(parents=True)
    session_path = session_dir / "rollout-2026-05-14T00-00-00-sess-codex.jsonl"
    records = [
        {"type": "session_meta", "payload": {"id": "sess-codex"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "Developer rules"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Fix the chart"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "shell",
                "arguments": "secret tool input",
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
    session_path.write_text(
        "\n".join(json.dumps(record) for record in records), encoding="utf-8"
    )

    transcript = evaluation_module.load_session_transcript("codex", "sess-codex")

    assert "USER:\nFix the chart" in transcript
    assert "ASSISTANT:\nChart fixed" in transcript
    assert "Developer rules" not in transcript
    assert "secret tool input" not in transcript


def test_load_claude_transcript_skips_tool_results(evaluation_module, isolated_home):
    session_dir = isolated_home / ".claude" / "projects" / "-project"
    session_dir.mkdir(parents=True)
    session_path = session_dir / "sess-claude.jsonl"
    records = [
        {
            "type": "user",
            "uuid": "u1",
            "message": {"role": "user", "content": "Fix the backend"},
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "message": {
                "id": "msg-1",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I updated the backend"},
                    {"type": "tool_use", "name": "Bash", "input": "secret"},
                ],
            },
        },
        {
            "type": "user",
            "uuid": "u2",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "content": "large command output",
                    }
                ],
            },
        },
    ]
    session_path.write_text(
        "\n".join(json.dumps(record) for record in records), encoding="utf-8"
    )

    transcript = evaluation_module.load_session_transcript("claude-code", "sess-claude")

    assert "USER:\nFix the backend" in transcript
    assert "ASSISTANT:\nI updated the backend" in transcript
    assert "large command output" not in transcript
    assert "secret" not in transcript


def test_load_opencode_transcript_reads_sqlite_message_parts(
    evaluation_module, isolated_home, monkeypatch
):
    monkeypatch.setenv("XDG_DATA_HOME", str(isolated_home / ".xdg-data"))
    _write_opencode_transcript(isolated_home)

    transcript = evaluation_module.load_session_transcript("opencode", "sess-opencode")

    assert "USER:\nFix OpenCode sessions" in transcript
    assert "ASSISTANT:\nOpenCode sessions fixed" in transcript
    assert "private reasoning omitted" not in transcript


def test_has_local_session_transcript_uses_path_or_metadata(
    evaluation_module, isolated_home, monkeypatch
):
    monkeypatch.setenv("XDG_DATA_HOME", str(isolated_home / ".xdg-data"))
    codex_dir = isolated_home / ".codex" / "sessions" / "2026" / "05" / "14"
    codex_dir.mkdir(parents=True)
    (codex_dir / "rollout-2026-05-14T00-00-00-sess-codex.jsonl").write_text(
        "", encoding="utf-8"
    )

    claude_dir = isolated_home / ".claude" / "projects" / "-tmp-project"
    claude_dir.mkdir(parents=True)
    (claude_dir / "sess-claude.jsonl").write_text("", encoding="utf-8")

    _write_opencode_transcript(isolated_home)

    assert evaluation_module.has_local_session_transcript("codex", "sess-codex")
    assert evaluation_module.has_local_session_transcript("claude-code", "sess-claude")
    assert evaluation_module.has_local_session_transcript("opencode", "sess-opencode")
    assert not evaluation_module.has_local_session_transcript(
        "gemini-cli", "sess-gemini"
    )
    assert not evaluation_module.has_local_session_transcript(
        "proxy-client", "sess-any"
    )


def test_has_local_session_transcript_uses_index_for_opencode(evaluation_module):
    index = evaluation_module.LocalSessionTranscriptIndex(
        opencode_session_ids=frozenset({"sess-opencode"})
    )

    assert evaluation_module.has_local_session_transcript(
        "opencode",
        "sess-opencode",
        local_index=index,
    )
    assert not evaluation_module.has_local_session_transcript(
        "opencode",
        "sess-missing",
        local_index=index,
    )
    assert not evaluation_module.has_local_session_transcript(
        "gemini-cli",
        "sess-opencode",
        local_index=index,
    )


def test_evaluation_prompt_treats_memory_tasks_as_no_op(evaluation_module):
    prompt = evaluation_module.EVALUATION_PROMPT

    assert "claude-mem-context" in prompt
    assert "background context, not the task being evaluated" in prompt
    assert 'return outcome "no_op"' in prompt


def test_evaluation_prompt_requests_localized_task_titles(evaluation_module):
    prompt = evaluation_module.EVALUATION_PROMPT

    assert '"task_title_zh": string | null' in prompt
    assert "task_title is a concise English title" in prompt
    assert "task_title_zh is a concise Chinese title" in prompt
    assert "2-6 words" in prompt
    assert "If there is no substantive task, set both titles to null" in prompt


def test_detects_claude_mem_observer_session(evaluation_module, isolated_home):
    session_dir = (
        isolated_home
        / ".claude"
        / "projects"
        / "-Users-hanbo--claude-mem-observer-sessions"
    )
    session_dir.mkdir(parents=True)
    (session_dir / "sess-mem.jsonl").write_text("", encoding="utf-8")

    assert evaluation_module.is_claude_mem_observer_session("sess-mem") is True
    assert evaluation_module.is_claude_mem_observer_session("sess-normal") is False


def test_parse_evaluation_output_rejects_invalid_outcome(evaluation_module):
    with pytest.raises(ValueError, match="Invalid outcome"):
        evaluation_module.parse_evaluation_output(
            '{"outcome":"great","task_title":null,"summary":null,"confidence":0.8,"evidence":[],"failure_reason":null}'
        )


def test_parse_evaluation_output_accepts_json_with_cli_noise(evaluation_module):
    parsed = evaluation_module.parse_evaluation_output(
        """
The 'metricReader' option is deprecated. Please use 'metricReaders' instead.
{
  "task_title": "Update frontend regression tests",
  "task_title_zh": "更新前端回归测试",
  "summary": "Updated and committed frontend tests.",
  "outcome": "solved",
  "confidence": 1.0,
  "evidence": ["Verified tests passed"],
  "failure_reason": null
}
"""
    )

    assert parsed["outcome"] == "solved"
    assert parsed["task_title"] == "Update frontend regression tests"
    assert parsed["task_title_zh"] == "更新前端回归测试"
    assert parsed["evidence"] == ["Verified tests passed"]


def test_parse_evaluation_output_tolerates_missing_task_title_zh(evaluation_module):
    parsed = evaluation_module.parse_evaluation_output(
        '{"task_title":"Fix dashboard","summary":"Done","outcome":"solved","confidence":0.8,"evidence":[],"failure_reason":null}'
    )

    assert parsed["task_title"] == "Fix dashboard"
    assert parsed["task_title_zh"] is None


@pytest.mark.parametrize("outcome", ["no_op", "unknown"])
def test_parse_evaluation_output_clears_titles_for_non_substantive_outcomes(
    evaluation_module, outcome
):
    parsed = evaluation_module.parse_evaluation_output(
        json.dumps(
            {
                "task_title": "Fix dashboard",
                "task_title_zh": "修复仪表板",
                "summary": "No substantive task was evaluated.",
                "outcome": outcome,
                "confidence": 0.8,
                "evidence": [],
                "failure_reason": None,
            }
        )
    )

    assert parsed["task_title"] is None
    assert parsed["task_title_zh"] is None


@pytest.mark.parametrize("confidence", [True, -0.1, 1.1, 82])
def test_parse_evaluation_output_rejects_invalid_confidence(
    evaluation_module, confidence
):
    with pytest.raises(ValueError, match="Evaluation confidence"):
        evaluation_module.parse_evaluation_output(
            f'{{"task_title":null,"summary":null,"outcome":"solved","confidence":{str(confidence).lower()},"evidence":[],"failure_reason":null}}'
        )


def test_run_session_evaluation_job_sanitizes_agent_failure_errors(
    evaluation_module,
    database_module,
    isolated_home,
    monkeypatch,
    caplog,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-secret",
                client_source="codex",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    session_dir = isolated_home / ".codex" / "sessions" / "2026" / "05" / "14"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout-sess-secret.jsonl").write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Secret task"}],
                },
            }
        ),
        encoding="utf-8",
    )

    job = database_module.create_session_evaluation_job(
        session_id="sess-secret",
        client_source="codex",
        db_path=db_path,
        user_id="test-user",
    )

    def fake_run(**kwargs):
        return subprocess.CompletedProcess(
            args=kwargs["args"],
            returncode=1,
            stdout='{"api_key":"sk-stdout-secret","authorization":"Bearer stdout-token"}',
            stderr=(
                "/Users/hanbo/private/token.txt /home/hanbo/token.txt "
                "failed with api_key=secret OPENAI_API_KEY=sk-env-secret "
                "Authorization: Bearer bearer-secret --api-key cli-secret"
            ),
        )

    monkeypatch.setattr(evaluation_module.subprocess, "run", fake_run)

    evaluation_module.run_session_evaluation_job(job["job_id"], db_path=db_path)

    polled = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert polled is not None
    assert polled["status"] == "failed"
    assert polled["error"] == "Evaluation agent failed"
    assert "Evaluation agent subprocess failed" in caplog.text
    assert "returncode=1" in caplog.text
    assert "api_key=secret" not in caplog.text
    assert "sk-stdout-secret" not in caplog.text
    assert "stdout-token" not in caplog.text
    assert "sk-env-secret" not in caplog.text
    assert "bearer-secret" not in caplog.text
    assert "cli-secret" not in caplog.text
    assert "/Users/hanbo/private/token.txt" not in caplog.text
    assert "/home/hanbo/token.txt" not in caplog.text


def test_start_session_evaluation_job_rejects_manual_evaluation(
    evaluation_module,
    database_module,
    isolated_home,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="manual-protected",
                client_source="codex",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                source="manual",
                outcome="solved",
                evaluated_at="2026-05-11T10:35:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    with pytest.raises(ValueError, match="Manual evaluation exists"):
        evaluation_module.start_session_evaluation_job(
            "manual-protected",
            db_path=db_path,
            user_id="test-user",
        )


def test_start_session_evaluation_job_queues_without_running(
    evaluation_module,
    database_module,
    isolated_home,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-queued",
                client_source="codex",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    job = evaluation_module.start_session_evaluation_job(
        "sess-queued",
        db_path=db_path,
        user_id="test-user",
    )

    assert job["status"] == "queued"
    assert job["trigger"] == "manual"
    assert database_module.count_running_evaluation_jobs(db_path=db_path) == 0


def test_start_session_evaluation_job_records_selected_evaluator(
    evaluation_module,
    database_module,
    isolated_home,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-selected-evaluator",
                client_source="codex",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    job = evaluation_module.start_session_evaluation_job(
        "sess-selected-evaluator",
        evaluator_type="claude",
        db_path=db_path,
        user_id="test-user",
    )

    assert job["evaluator_type"] == "claude"


def test_start_session_evaluation_job_promotes_queued_auto_job(
    evaluation_module,
    database_module,
    isolated_home,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-promote",
                client_source="codex",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    auto_job = database_module.create_session_evaluation_job(
        session_id="sess-promote",
        client_source="codex",
        trigger="auto",
        evaluator_type="codex",
        db_path=db_path,
        user_id="test-user",
    )

    manual_job = evaluation_module.start_session_evaluation_job(
        "sess-promote",
        evaluator_type="claude",
        db_path=db_path,
        user_id="test-user",
    )

    assert manual_job["job_id"] == auto_job["job_id"]
    assert manual_job["trigger"] == "manual"
    assert manual_job["evaluator_type"] == "claude"
    stored = database_module.get_evaluation_job(
        auto_job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert stored["trigger"] == "manual"
    assert stored["evaluator_type"] == "claude"


def test_run_session_evaluation_job_saves_llm_evaluation_before_success(
    evaluation_module,
    database_module,
    isolated_home,
    monkeypatch,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-llm",
                client_source="codex",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    job = database_module.create_session_evaluation_job(
        session_id="sess-llm",
        client_source="codex",
        db_path=db_path,
        user_id="test-user",
    )

    session_dir = isolated_home / ".codex" / "sessions" / "2026" / "05" / "14"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout-sess-llm.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "Fix dashboard"}
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "Completed the requested dashboard fix.",
                                }
                            ],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    def fake_run(**kwargs):
        assert kwargs["args"] == [
            "codex",
            "-c",
            "otel.enabled=false",
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ignore-rules",
            "-",
        ]
        assert "Return ONLY valid JSON" in kwargs["input"]
        assert "USER:\nFix dashboard" in kwargs["input"]
        assert "ASSISTANT:\nCompleted the requested dashboard fix." in kwargs["input"]
        assert kwargs["env"]["OTEL_SDK_DISABLED"] == "true"
        return subprocess.CompletedProcess(
            args=kwargs["args"],
            returncode=0,
            stdout='{"task_title":"Fix dashboard","task_title_zh":"修复仪表板","summary":"Completed the requested dashboard fix.","outcome":"solved","confidence":0.82,"evidence":["Final response said tests passed"],"failure_reason":null}',
            stderr="",
        )

    monkeypatch.setattr(evaluation_module.subprocess, "run", fake_run)

    evaluation_module.run_session_evaluation_job(job["job_id"], db_path=db_path)

    saved = database_module.get_session_evaluation(
        "sess-llm", db_path=db_path, user_id="test-user"
    )
    assert saved is not None
    assert saved["outcome"] == "solved"
    assert saved["source"] == "llm"
    assert saved["confidence"] == pytest.approx(0.82)
    assert saved["task_title"] == "Fix dashboard"
    assert saved["task_title_zh"] == "修复仪表板"

    polled = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert polled is not None
    assert polled["status"] == "succeeded"
    assert polled["error"] is None


def test_run_session_evaluation_job_saves_opencode_evaluation(
    evaluation_module,
    database_module,
    isolated_home,
    monkeypatch,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(isolated_home / ".xdg-data"))
    _write_opencode_transcript(isolated_home)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-opencode",
                client_source="opencode",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    job = database_module.create_session_evaluation_job(
        session_id="sess-opencode",
        client_source="opencode",
        db_path=db_path,
        user_id="test-user",
    )

    def fake_run(**kwargs):
        assert "Return ONLY valid JSON" in kwargs["input"]
        assert "Session source: opencode" in kwargs["input"]
        assert "USER:\nFix OpenCode sessions" in kwargs["input"]
        assert "ASSISTANT:\nOpenCode sessions fixed" in kwargs["input"]
        assert "private reasoning omitted" not in kwargs["input"]
        return subprocess.CompletedProcess(
            args=kwargs["args"],
            returncode=0,
            stdout='{"task_title":"Fix OpenCode sessions","task_title_zh":"修复OpenCode会话","summary":"OpenCode session evaluation completed.","outcome":"solved","confidence":0.91,"evidence":["Transcript showed the fix was completed"],"failure_reason":null}',
            stderr="",
        )

    monkeypatch.setattr(evaluation_module.subprocess, "run", fake_run)

    evaluation_module.run_session_evaluation_job(job["job_id"], db_path=db_path)

    saved = database_module.get_session_evaluation(
        "sess-opencode", db_path=db_path, user_id="test-user"
    )
    assert saved is not None
    assert saved["outcome"] == "solved"
    assert saved["source"] == "llm"
    assert saved["confidence"] == pytest.approx(0.91)
    assert saved["task_title"] == "Fix OpenCode sessions"
    assert saved["task_title_zh"] == "修复OpenCode会话"

    polled = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert polled is not None
    assert polled["status"] == "succeeded"
    assert polled["error"] is None


def test_summarize_session_with_llm_marks_evaluator_session_no_op(
    evaluation_module,
    database_module,
    isolated_home,
    monkeypatch,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-evaluator",
                client_source="codex",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    session_dir = isolated_home / ".codex" / "sessions" / "2026" / "05" / "14"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout-sess-evaluator.jsonl").write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"{evaluation_module.EVALUATION_PROMPT}\n\n<session_transcript>...</session_transcript>",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    def fail_if_called(**kwargs):
        raise AssertionError("Evaluator sessions should not invoke another evaluator")

    monkeypatch.setattr(evaluation_module.subprocess, "run", fail_if_called)

    result = evaluation_module.summarize_session_with_llm(
        "sess-evaluator",
        db_path=db_path,
        user_id="test-user",
    )

    saved = database_module.get_session_evaluation(
        "sess-evaluator", db_path=db_path, user_id="test-user"
    )
    assert result["outcome"] == "no_op"
    assert result["task_title"] is None
    assert result["task_title_zh"] is None
    assert saved is not None
    assert saved["outcome"] == "no_op"
    assert saved["source"] == "llm"
    assert saved["task_title"] is None
    assert saved["task_title_zh"] is None


def test_execute_session_evaluation_job_does_not_overwrite_later_manual_evaluation(
    evaluation_module,
    database_module,
    isolated_home,
    monkeypatch,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-manual-race",
                client_source="codex",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    session_dir = isolated_home / ".codex" / "sessions" / "2026" / "05" / "14"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout-sess-manual-race.jsonl").write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Fix dashboard"}],
                },
            }
        ),
        encoding="utf-8",
    )
    job = database_module.create_session_evaluation_job(
        session_id="sess-manual-race",
        client_source="codex",
        trigger="auto",
        db_path=db_path,
        user_id="test-user",
    )
    database_module.claim_next_evaluation_job(db_path=db_path)
    database_module.upsert_session_evaluation(
        session_id="sess-manual-race",
        outcome="failed",
        source="manual",
        evidence=["User marked outcome manually"],
        db_path=db_path,
        user_id="test-user",
    )

    def fake_run(**kwargs):
        return subprocess.CompletedProcess(
            args=kwargs["args"],
            returncode=0,
            stdout='{"task_title":"Fix dashboard","summary":"Done","outcome":"solved","confidence":0.9,"evidence":["LLM result"],"failure_reason":null}',
            stderr="",
        )

    monkeypatch.setattr(evaluation_module.subprocess, "run", fake_run)

    evaluation_module.execute_session_evaluation_job(job["job_id"], db_path=db_path)

    saved = database_module.get_session_evaluation(
        "sess-manual-race",
        db_path=db_path,
        user_id="test-user",
    )
    polled = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert saved is not None
    assert saved["source"] == "manual"
    assert saved["outcome"] == "failed"
    assert saved["evidence"] == ["User marked outcome manually"]
    assert polled is not None
    assert polled["status"] == "succeeded"


def test_run_session_evaluation_job_returns_when_job_cannot_start(
    evaluation_module,
    database_module,
    isolated_home,
    monkeypatch,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-stale-job",
                client_source="codex",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    job = database_module.create_session_evaluation_job(
        session_id="sess-stale-job",
        client_source="codex",
        db_path=db_path,
        user_id="test-user",
    )
    database_module.claim_next_evaluation_job(db_path=db_path)
    database_module.mark_evaluation_job_failed(
        job["job_id"],
        "Evaluation job timed out",
        db_path=db_path,
    )

    evaluator_called = False

    def fail_if_called(*args, **kwargs):
        nonlocal evaluator_called
        evaluator_called = True
        raise AssertionError("Evaluator should not run for a non-startable job")

    monkeypatch.setattr(evaluation_module, "summarize_session_with_llm", fail_if_called)

    evaluation_module.run_session_evaluation_job(job["job_id"], db_path=db_path)

    polled = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    saved = database_module.get_session_evaluation(
        "sess-stale-job",
        db_path=db_path,
        user_id="test-user",
    )
    assert polled is not None
    assert polled["status"] == "failed"
    assert polled["error"] == "Evaluation job timed out"
    assert saved is None
    assert evaluator_called is False


def test_run_session_evaluation_job_saves_unknown_when_transcript_missing(
    evaluation_module,
    database_module,
    isolated_home,
    monkeypatch,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-missing",
                client_source="codex",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    job = database_module.create_session_evaluation_job(
        session_id="sess-missing",
        client_source="codex",
        db_path=db_path,
        user_id="test-user",
    )

    def fail_if_called(**kwargs):
        raise AssertionError("Evaluator should not run without a transcript")

    monkeypatch.setattr(evaluation_module.subprocess, "run", fail_if_called)

    evaluation_module.run_session_evaluation_job(job["job_id"], db_path=db_path)

    saved = database_module.get_session_evaluation(
        "sess-missing", db_path=db_path, user_id="test-user"
    )
    assert saved is not None
    assert saved["outcome"] == "unknown"
    assert saved["source"] == "llm"
    assert saved["confidence"] == pytest.approx(0.0)
    assert saved["task_title"] is None
    assert saved["task_title_zh"] is None

    polled = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert polled is not None
    assert polled["status"] == "succeeded"
    assert polled["error"] is None


def test_run_session_evaluation_job_marks_claude_mem_sessions_no_op(
    evaluation_module,
    database_module,
    isolated_home,
    monkeypatch,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)
    session_dir = (
        isolated_home
        / ".claude"
        / "projects"
        / "-Users-hanbo--claude-mem-observer-sessions"
    )
    session_dir.mkdir(parents=True)
    (session_dir / "sess-mem.jsonl").write_text("", encoding="utf-8")

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-mem",
                client_source="claude-code",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    job = database_module.create_session_evaluation_job(
        session_id="sess-mem",
        client_source="claude-code",
        db_path=db_path,
        user_id="test-user",
    )

    def fail_if_called(**kwargs):
        raise AssertionError("Claude should not be invoked for Claude-Mem sessions")

    monkeypatch.setattr(evaluation_module.subprocess, "run", fail_if_called)

    evaluation_module.run_session_evaluation_job(job["job_id"], db_path=db_path)

    saved = database_module.get_session_evaluation(
        "sess-mem", db_path=db_path, user_id="test-user"
    )
    assert saved is not None
    assert saved["outcome"] == "no_op"
    assert saved["source"] == "llm"
    assert saved["confidence"] == pytest.approx(1.0)
    assert saved["task_title"] is None
    assert saved["task_title_zh"] is None

    polled = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert polled is not None
    assert polled["status"] == "succeeded"
    assert polled["error"] is None


def test_has_local_session_transcript_uses_index_for_kilo(evaluation_module):
    index = evaluation_module.LocalSessionTranscriptIndex(
        kilo_session_ids=frozenset({"sess-kilo"})
    )

    assert evaluation_module.has_local_session_transcript(
        "kilo",
        "sess-kilo",
        local_index=index,
    )
    assert not evaluation_module.has_local_session_transcript(
        "kilo",
        "sess-missing",
        local_index=index,
    )


def test_load_kilo_transcript_reads_sqlite_message_parts(
    evaluation_module, isolated_home, monkeypatch
):
    monkeypatch.setenv("XDG_DATA_HOME", str(isolated_home / ".xdg-data"))
    _write_kilo_transcript(isolated_home)

    transcript = evaluation_module.load_session_transcript("kilo", "sess-kilo")

    assert "USER:\nFix Kilo sessions" in transcript
    assert "ASSISTANT:\nKilo sessions fixed" in transcript
    assert "private reasoning omitted" not in transcript


def test_has_local_session_transcript_uses_path_or_metadata_includes_kilo(
    evaluation_module, isolated_home, monkeypatch
):
    monkeypatch.setenv("XDG_DATA_HOME", str(isolated_home / ".xdg-data"))
    _write_kilo_transcript(isolated_home)

    assert evaluation_module.has_local_session_transcript("kilo", "sess-kilo")


def test_normalize_client_source_accepts_kilo(evaluation_module):
    assert evaluation_module._normalize_client_source("kilo") == "kilo"


def test_run_session_evaluation_job_saves_kilo_evaluation(
    evaluation_module,
    database_module,
    isolated_home,
    monkeypatch,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(isolated_home / ".xdg-data"))
    _write_kilo_transcript(isolated_home)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-kilo",
                client_source="kilo",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    job = database_module.create_session_evaluation_job(
        session_id="sess-kilo",
        client_source="kilo",
        db_path=db_path,
        user_id="test-user",
    )

    def fake_run(**kwargs):
        assert "Return ONLY valid JSON" in kwargs["input"]
        assert "Session source: kilo" in kwargs["input"]
        assert "USER:\nFix Kilo sessions" in kwargs["input"]
        assert "ASSISTANT:\nKilo sessions fixed" in kwargs["input"]
        assert "private reasoning omitted" not in kwargs["input"]
        return subprocess.CompletedProcess(
            args=kwargs["args"],
            returncode=0,
            stdout='{"task_title":"Fix Kilo sessions","task_title_zh":"修复Kilo会话","summary":"Kilo session evaluation completed.","outcome":"solved","confidence":0.91,"evidence":["Transcript showed the fix was completed"],"failure_reason":null}',
            stderr="",
        )

    monkeypatch.setattr(evaluation_module.subprocess, "run", fake_run)

    evaluation_module.run_session_evaluation_job(job["job_id"], db_path=db_path)

    saved = database_module.get_session_evaluation(
        "sess-kilo", db_path=db_path, user_id="test-user"
    )
    assert saved is not None
    assert saved["outcome"] == "solved"
    assert saved["source"] == "llm"
    assert saved["confidence"] == pytest.approx(0.91)
    assert saved["task_title"] == "Fix Kilo sessions"
    assert saved["task_title_zh"] == "修复Kilo会话"

    polled = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert polled is not None
    assert polled["status"] == "succeeded"
    assert polled["error"] is None


def test_build_evaluator_invocation_uses_claude_command(evaluation_module):
    invocation = evaluation_module.build_evaluator_invocation(
        "Transcript text", evaluator="claude"
    )

    assert invocation.command == [
        "claude",
        "--print",
        "--no-session-persistence",
    ]
    assert invocation.stdin is not None
    assert "Transcript text" in invocation.stdin
    assert "Return ONLY valid JSON" in invocation.stdin
    assert invocation.env["OTEL_SDK_DISABLED"] == "true"
    assert invocation.env["OTEL_LOGS_EXPORTER"] == "none"
    assert invocation.env["CLAUDE_CODE_NO_AUTO_GIT"] == "1"
    assert invocation.env["CLAUDE_CODE_NO_BROWSER"] == "1"
    assert invocation.env["CLAUDE_CODE_HEADLESS"] == "1"


def test_build_evaluator_invocation_rejects_invalid_agent(evaluation_module):
    with pytest.raises(ValueError, match="Unsupported evaluator agent"):
        evaluation_module.build_evaluator_invocation(
            "Transcript text", evaluator="invalid"
        )


def test_list_evaluator_agents_exposes_labels_and_availability(
    evaluation_module,
    monkeypatch,
):
    monkeypatch.setattr(
        evaluation_module.shutil,
        "which",
        lambda command: f"/usr/bin/{command}" if command == "codex" else None,
    )

    agents = evaluation_module.list_evaluator_agents()

    assert agents == [
        {
            "id": "codex",
            "label": "Codex",
            "command": "codex",
            "available": True,
        },
        {
            "id": "claude",
            "label": "Claude Code",
            "command": "claude",
            "available": False,
        },
    ]


def test_require_available_evaluator_type_rejects_unknown_and_unavailable(
    evaluation_module,
    monkeypatch,
):
    monkeypatch.setattr(evaluation_module.shutil, "which", lambda command: None)

    with pytest.raises(ValueError, match="Unsupported evaluator agent"):
        evaluation_module.require_available_evaluator_type("invalid")
    with pytest.raises(ValueError, match="Evaluator not available: claude"):
        evaluation_module.require_available_evaluator_type("claude")


def test_summarize_session_with_llm_uses_claude_evaluator(
    evaluation_module,
    database_module,
    isolated_home,
    monkeypatch,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-claude-eval",
                client_source="codex",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    session_dir = isolated_home / ".codex" / "sessions" / "2026" / "05" / "14"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout-sess-claude-eval.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "Fix dashboard"}
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Done"}],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    def fake_run(**kwargs):
        assert kwargs["args"] == ["claude", "--print", "--no-session-persistence"]
        assert kwargs["input"] is not None
        assert "Return ONLY valid JSON" in kwargs["input"]
        assert "USER:\nFix dashboard" in kwargs["input"]
        assert kwargs["env"]["OTEL_SDK_DISABLED"] == "true"
        assert kwargs["env"]["CLAUDE_CODE_NO_AUTO_GIT"] == "1"
        assert kwargs["env"]["CLAUDE_CODE_NO_BROWSER"] == "1"
        assert kwargs["env"]["CLAUDE_CODE_HEADLESS"] == "1"
        return subprocess.CompletedProcess(
            args=kwargs["args"],
            returncode=0,
            stdout='{"task_title":"Fix dashboard","task_title_zh":"修复仪表板","summary":"Done","outcome":"solved","confidence":0.9,"evidence":["Done"],"failure_reason":null}',
            stderr="",
        )

    monkeypatch.setattr(evaluation_module.subprocess, "run", fake_run)

    result = evaluation_module.summarize_session_with_llm(
        "sess-claude-eval",
        db_path=db_path,
        evaluator="claude",
        user_id="test-user",
    )

    assert result["outcome"] == "solved"
    assert result["confidence"] == pytest.approx(0.9)


def test_execute_session_evaluation_job_passes_claude_evaluator(
    evaluation_module,
    database_module,
    isolated_home,
    monkeypatch,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-exec-claude",
                client_source="codex",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    session_dir = isolated_home / ".codex" / "sessions" / "2026" / "05" / "14"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout-sess-exec-claude.jsonl").write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Fix backend"}],
                },
            }
        ),
        encoding="utf-8",
    )

    job = database_module.create_session_evaluation_job(
        session_id="sess-exec-claude",
        client_source="codex",
        evaluator_type="claude",
        db_path=db_path,
        user_id="test-user",
    )

    def fake_run(**kwargs):
        assert kwargs["args"] == ["claude", "--print", "--no-session-persistence"]
        assert kwargs["input"] is not None
        return subprocess.CompletedProcess(
            args=kwargs["args"],
            returncode=0,
            stdout='{"task_title":"Fix backend","summary":"Done","outcome":"solved","confidence":0.8,"evidence":["Done"],"failure_reason":null}',
            stderr="",
        )

    monkeypatch.setattr(evaluation_module.subprocess, "run", fake_run)

    database_module.mark_evaluation_job_running(job["job_id"], db_path=db_path)
    evaluation_module.execute_session_evaluation_job(
        job["job_id"],
        db_path=db_path,
    )

    saved = database_module.get_session_evaluation(
        "sess-exec-claude", db_path=db_path, user_id="test-user"
    )
    polled = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert saved is not None
    assert saved["outcome"] == "solved"
    assert polled is not None
    assert polled["status"] == "succeeded"


def test_run_session_evaluation_job_with_claude_evaluator(
    evaluation_module,
    database_module,
    isolated_home,
    monkeypatch,
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-run-claude",
                client_source="codex",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-11T10:30:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    session_dir = isolated_home / ".codex" / "sessions" / "2026" / "05" / "14"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout-sess-run-claude.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Fix UI"}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "UI fixed"}],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    job = database_module.create_session_evaluation_job(
        session_id="sess-run-claude",
        client_source="codex",
        evaluator_type="claude",
        db_path=db_path,
        user_id="test-user",
    )

    def fake_run(**kwargs):
        assert kwargs["args"] == ["claude", "--print", "--no-session-persistence"]
        assert kwargs["input"] is not None
        assert "Return ONLY valid JSON" in kwargs["input"]
        assert kwargs["env"]["CLAUDE_CODE_NO_AUTO_GIT"] == "1"
        assert kwargs["env"]["CLAUDE_CODE_NO_BROWSER"] == "1"
        assert kwargs["env"]["CLAUDE_CODE_HEADLESS"] == "1"
        return subprocess.CompletedProcess(
            args=kwargs["args"],
            returncode=0,
            stdout='{"task_title":"Fix UI","summary":"UI fixed","outcome":"solved","confidence":0.85,"evidence":["Done"],"failure_reason":null}',
            stderr="",
        )

    monkeypatch.setattr(evaluation_module.subprocess, "run", fake_run)

    evaluation_module.run_session_evaluation_job(
        job["job_id"],
        db_path=db_path,
    )

    saved = database_module.get_session_evaluation(
        "sess-run-claude", db_path=db_path, user_id="test-user"
    )
    polled = database_module.get_evaluation_job(
        job["job_id"], db_path=db_path, user_id="test-user"
    )
    assert saved is not None
    assert saved["outcome"] == "solved"
    assert saved["confidence"] == pytest.approx(0.85)
    assert polled is not None
    assert polled["status"] == "succeeded"


def test_extract_content_includes_tool_use_summaries(evaluation_module):
    content = [
        {"type": "text", "text": "Let me read that file."},
        {
            "type": "tool_use",
            "name": "Read",
            "input": {"file_path": "/Users/me/project/api.py"},
        },
        {
            "type": "tool_use",
            "name": "Bash",
            "input": {"command": "uv sync", "description": "Install deps"},
        },
        {
            "type": "tool_use",
            "name": "Write",
            "input": {
                "file_path": "/Users/me/project/main.py",
                "content": "print('hi')",
            },
        },
        {
            "type": "tool_use",
            "name": "Edit",
            "input": {"file_path": "/Users/me/project/README.md"},
        },
    ]
    result = evaluation_module._extract_content(content)
    assert "Let me read that file." in result
    assert "[tool:Read] /Users/me/project/api.py" in result
    assert "[tool:Bash] uv sync" in result
    assert "[tool:Write] /Users/me/project/main.py" in result
    assert "[tool:Edit] /Users/me/project/README.md" in result


def test_extract_content_redacts_secrets_in_bash_commands(evaluation_module):
    content = [
        {
            "type": "tool_use",
            "name": "Bash",
            "input": {
                "command": "export API_KEY=sk-abc123secret && curl -H 'Authorization: Bearer tok_xyz' https://example.com"
            },
        },
    ]
    result = evaluation_module._extract_content(content)
    assert "sk-abc123secret" not in result
    assert "tok_xyz" not in result
    assert "[tool:Bash]" in result


def test_extract_content_skips_thinking_blocks(evaluation_module):
    content = [
        {"type": "thinking", "text": "internal reasoning"},
        {"type": "text", "text": "Here's my answer."},
    ]
    result = evaluation_module._extract_content(content)
    assert result == "Here's my answer."


def test_extract_content_handles_string_content(evaluation_module):
    result = evaluation_module._extract_content("hello world")
    assert result == "hello world"


def test_extract_content_returns_none_for_empty(evaluation_module):
    assert evaluation_module._extract_content([]) is None
    assert (
        evaluation_module._extract_content([{"type": "thinking", "text": "meh"}])
        is None
    )
    assert evaluation_module._extract_content(None) is None


def test_parse_evaluation_output_extracts_project(evaluation_module):
    parsed = evaluation_module.parse_evaluation_output(
        json.dumps(
            {
                "task_title": "Fix auth",
                "task_title_zh": "修复认证",
                "summary": "Fixed auth bug.",
                "outcome": "solved",
                "confidence": 0.9,
                "evidence": ["auth fixed"],
                "failure_reason": None,
                "project": "tokenage",
            }
        )
    )
    assert parsed["project"] == "tokenage"


def test_parse_evaluation_output_allows_null_project(evaluation_module):
    parsed = evaluation_module.parse_evaluation_output(
        json.dumps(
            {
                "task_title": "Fix auth",
                "task_title_zh": "修复认证",
                "summary": "Fixed auth bug.",
                "outcome": "solved",
                "confidence": 0.9,
                "evidence": ["auth fixed"],
                "failure_reason": None,
                "project": None,
            }
        )
    )
    assert parsed["project"] is None


def test_parse_evaluation_output_defaults_project_to_none(evaluation_module):
    """Backwards compat: if LLM omits project field, default to None."""
    parsed = evaluation_module.parse_evaluation_output(
        json.dumps(
            {
                "task_title": "Fix auth",
                "task_title_zh": "修复认证",
                "summary": "Fixed auth bug.",
                "outcome": "solved",
                "confidence": 0.9,
                "evidence": ["auth fixed"],
                "failure_reason": None,
            }
        )
    )
    assert parsed["project"] is None


def test_parse_evaluation_output_clears_project_for_no_op(evaluation_module):
    parsed = evaluation_module.parse_evaluation_output(
        json.dumps(
            {
                "task_title": "Greeting",
                "task_title_zh": "问候",
                "summary": "Just a hello.",
                "outcome": "no_op",
                "confidence": 1.0,
                "evidence": [],
                "failure_reason": None,
                "project": "some-project",
            }
        )
    )
    assert parsed["project"] is None


def test_upsert_session_evaluation_stores_project(
    evaluation_module, database_module, isolated_home
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-proj",
                client_source="claude-code",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-27T10:00:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    database_module.upsert_session_evaluation(
        session_id="sess-proj",
        outcome="solved",
        source="llm",
        confidence=0.9,
        task_title="Fix auth",
        task_title_zh="修复认证",
        summary="Fixed.",
        evidence=["done"],
        failure_reason=None,
        project="my-cool-project",
        db_path=db_path,
        user_id="test-user",
    )

    result = database_module.get_session_evaluation(
        "sess-proj", db_path=db_path, user_id="test-user"
    )
    assert result is not None
    assert result["project"] == "my-cool-project"


def test_get_session_evaluation_includes_project(
    evaluation_module, database_module, isolated_home
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-proj2",
                client_source="claude-code",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-27T10:00:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    database_module.upsert_session_evaluation(
        session_id="sess-proj2",
        outcome="solved",
        source="manual",
        project="hub-router",
        db_path=db_path,
        user_id="test-user",
    )

    result = database_module.get_session_evaluation(
        "sess-proj2", db_path=db_path, user_id="test-user"
    )
    assert result["project"] == "hub-router"


def test_delete_session_evaluation_clears_project(
    evaluation_module, database_module, isolated_home
):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-proj3",
                client_source="claude-code",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-27T10:00:00+00:00",
                outcome="solved",
                source="llm",
                project="to-be-deleted",
                user_id="test-user",
            )
        )
        session.commit()

    database_module.delete_session_evaluation(
        "sess-proj3", db_path=db_path, user_id="test-user"
    )

    with database_module.Session(database_module.get_engine(db_path)) as session:
        rec = session.get(database_module.SessionRecord, "sess-proj3")
        assert rec.project is None


def test_upsert_project_source_overwrite_behavior(database_module, isolated_home):
    db_path = str(isolated_home / "usage.db")
    database_module.init_db(db_path)

    with database_module.Session(database_module.get_engine(db_path)) as session:
        session.add(
            database_module.SessionRecord(
                session_id="sess-overwrite",
                client_source="claude-code",
                started=1778493600000000,
                ended=1778495400000000,
                updated_at="2026-05-27T10:00:00+00:00",
                user_id="test-user",
            )
        )
        session.commit()

    # 1) LLM sets project
    database_module.upsert_session_evaluation(
        session_id="sess-overwrite",
        outcome="solved",
        source="llm",
        project="alpha",
        db_path=db_path,
        user_id="test-user",
    )
    assert (
        database_module.get_session_evaluation(
            "sess-overwrite", db_path=db_path, user_id="test-user"
        )["project"]
        == "alpha"
    )

    # 2) Manual eval with project=None preserves existing LLM-set project
    database_module.upsert_session_evaluation(
        session_id="sess-overwrite",
        outcome="partial",
        source="manual",
        project=None,
        db_path=db_path,
        user_id="test-user",
    )
    assert (
        database_module.get_session_evaluation(
            "sess-overwrite", db_path=db_path, user_id="test-user"
        )["project"]
        == "alpha"
    )

    # 3) LLM eval with project=None clears it
    database_module.upsert_session_evaluation(
        session_id="sess-overwrite",
        outcome="failed",
        source="llm",
        project=None,
        db_path=db_path,
        user_id="test-user",
    )
    assert (
        database_module.get_session_evaluation(
            "sess-overwrite", db_path=db_path, user_id="test-user"
        )["project"]
        is None
    )
