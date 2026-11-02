"""Retired evaluation endpoints reject old clients without modifying data."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

# Frozen from the old client's EvaluationStartedReport and completed report.
STARTED = {
    "job_id": "job-1",
    "session_id": "session-1",
    "client_source": "codex",
    "evaluator_type": "codex",
    "started_at": "2026-05-11T00:00:00Z",
}
COMPLETED = {
    **STARTED,
    "finished_at": "2026-05-11T00:05:00Z",
    "status": "succeeded",
    "result": {
        "outcome": "solved",
        "confidence": 0.9,
        "task_title": "Fixed",
        "task_title_zh": None,
        "summary": "Done",
        "evidence": [],
        "failure_reason": None,
        "project": None,
    },
    "error": None,
}


def test_retired_routes_are_json_404_and_do_not_write(
    api_module, fresh_db, monkeypatch
):
    from src.auth.tokens import get_or_create_user, mint_device_tokens
    from src.config.app import CONFIG
    from src.database.models import EvaluationJob, SessionRecord

    user = get_or_create_user("retirement@example.com", db_path=fresh_db.db_path)
    token, _, _ = mint_device_tokens(
        user.id, "install", "device", db_path=fresh_db.db_path
    )
    monkeypatch.setitem(CONFIG, "auth", {"provider": "google"})
    client = TestClient(api_module.app, headers={"Authorization": f"Bearer {token}"})
    engine = fresh_db.database_module.get_engine()
    with Session(engine) as db:
        db.add(
            SessionRecord(
                session_id="session-1",
                user_id=user.id,
                client_source="codex",
                started=1,
                ended=2,
                updated_at="2026-01-01",
                outcome="failed",
                task_title="historic",
            )
        )
        db.commit()
    requests = [
        ("POST", "/evaluation-jobs/started", STARTED),
        ("POST", "/evaluation-jobs/completed", COMPLETED),
        ("GET", "/evaluation-jobs/active", None),
        ("GET", "/evaluation-jobs/job-1", None),
        ("GET", "/sessions/session-1/evaluation-jobs", None),
        ("GET", "/sessions/session-1/evaluation", None),
        ("PUT", "/sessions/session-1/evaluation", {"outcome": "solved"}),
        ("DELETE", "/sessions/session-1/evaluation", None),
        ("GET", "/sessions/daily-effectiveness", None),
        ("GET", "/model-effectiveness", None),
        ("POST", "/evaluation-jobs", None),
        *[
            ("OPTIONS", path, None)
            for path in (
                "/evaluation-jobs",
                "/evaluation-jobs/active",
                "/sessions/session-1/evaluation",
                "/sessions/session-1/evaluation-jobs",
                "/sessions/daily-effectiveness",
                "/model-effectiveness",
            )
        ],
    ]
    sessions = client.get("/sessions", params={"hide_noop": "true"}).json()
    assert sessions["total"] == 1
    assert sessions["sessions"][0]["session_id"] == "session-1"
    assert "evaluation" not in sessions["sessions"][0]
    for method, path, payload in requests:
        response = client.request(method, path, json=payload)
        assert response.status_code == 404, (method, path, response.text)
        assert response.json() == {"detail": "Not Found"}
    with Session(engine) as db:
        assert db.scalars(select(EvaluationJob)).all() == []
        row = db.get(SessionRecord, "session-1")
        assert (row.outcome, row.task_title) == ("failed", "historic")
