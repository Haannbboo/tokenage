"""Exercise the actual client reports against the API, without an evaluator CLI."""

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from fastapi.testclient import TestClient

from client import evaluation


def test_local_evaluation_roundtrip_lost_response_resume_and_manual_precedence(
    api_module, database_module, isolated_home, monkeypatch
):
    from src.auth.tokens import get_or_create_user, mint_device_tokens

    monkeypatch.setenv("TOKENAGE_HOME", str(isolated_home / ".tokenage"))
    monkeypatch.setenv("XDG_DATA_HOME", str(isolated_home / "data"))
    monkeypatch.setitem(api_module.CONFIG, "auth", {"provider": "google"})
    user = get_or_create_user("evaluation@example.com")
    cli_token, _, device = mint_device_tokens(user.id, "installation", "test-device")
    server = "https://server.example"
    evaluation.save_private_object(
        isolated_home / ".tokenage" / "credentials.json",
        {"server_url": server, "user_id": user.id, "cli_token": cli_token},
    )
    session_id = "roundtrip-session"
    with database_module.Session(database_module.get_engine()) as db:
        db.add(
            database_module.SessionRecord(
                session_id=session_id,
                user_id=user.id,
                client_source="claude-code",
                started=1_791_900_000_000_000,
                ended=1_791_900_001_000_000,
                updated_at=datetime.now(UTC).isoformat(),
            )
        )
        db.commit()
    path = isolated_home / ".claude" / "projects" / "project" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "uuid": "first-message",
                "message": {"role": "user", "content": "private transcript sentinel"},
            }
        ),
        encoding="utf-8",
    )
    os.utime(path, (time.time() - 1000,) * 2)
    monkeypatch.setattr(evaluation.shutil, "which", lambda name: "/bin/claude")
    cli_calls = []

    def run_cli(**kwargs):
        cli_calls.append(kwargs)
        return subprocess.CompletedProcess(
            kwargs["args"],
            0,
            stdout=json.dumps({"outcome": "solved", "summary": "Verified the fix"}),
        )

    monkeypatch.setattr(evaluation.subprocess, "run", run_cli)
    api = TestClient(api_module.app, base_url=server)
    completions = []

    def post_report(url, *, json, headers, timeout):
        if url.endswith("/started"):
            # Evaluation must proceed even when this informational report is lost.
            return httpx.Response(503, request=httpx.Request("POST", url))
        assert "private transcript sentinel" not in str(json)
        response = api.post(urlsplit(url).path, json=json, headers=headers)
        assert response.status_code == 204, response.text
        completions.append(json)
        if len(completions) == 1:
            # The server committed, but the client never received its response.
            raise httpx.ReadTimeout("lost response", request=httpx.Request("POST", url))
        return response

    monkeypatch.setattr(evaluation.httpx, "post", post_report)
    evaluation.run_evaluation_pass()
    first = evaluation._read_result(session_id)
    assert first["submitted"] is False
    headers = {"Authorization": f"Bearer {cli_token}"}
    result = api.get(f"/sessions/{session_id}/evaluation", headers=headers).json()
    assert result["evaluation"]["outcome"] == "solved"

    assert (
        api.put(
            f"/sessions/{session_id}/evaluation",
            json={"outcome": "partial", "source": "manual"},
            headers=headers,
        ).status_code
        == 200
    )
    evaluation.run_evaluation_pass()
    assert len(cli_calls) == 1
    assert completions[0]["job_id"] == completions[1]["job_id"]
    assert evaluation._read_result(session_id)["submitted"] is True

    # Resuming creates another run and replaces the sole local result file.
    with path.open("a", encoding="utf-8") as transcript:
        transcript.write(
            "\n"
            + json.dumps(
                {
                    "type": "user",
                    "uuid": "resumed",
                    "message": {"role": "user", "content": "Continue"},
                }
            )
        )
    evaluation.run_evaluation_pass(now=time.time() + 601)
    assert len(cli_calls) == 2
    assert evaluation._read_result(session_id)["job_id"] != first["job_id"]
    assert len(list(evaluation.evaluations_dir().glob("*.json"))) == 1
    current = api.get(f"/sessions/{session_id}/evaluation", headers=headers).json()
    assert current["evaluation"]["source"] == "manual"
    assert current["evaluation"]["outcome"] == "partial"
    runs = api.get(f"/sessions/{session_id}/evaluation-jobs", headers=headers).json()[
        "jobs"
    ]
    assert len(runs) == 2
    assert all(run["device_id"] == device.id for run in runs)
    assert all(run["status"] == "succeeded" for run in runs)
    assert all("outcome" not in run and "result" not in run for run in runs)
