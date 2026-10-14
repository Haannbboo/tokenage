"""Client-reported evaluation runs: report routes, tenancy, and retirement of
the old server-side queue."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, update
from sqlalchemy.orm import Session

TS = 1776384000000000  # 2026-04-17T00:00:00+00:00

STARTED_AT = "2026-05-11T00:00:00+00:00"
FINISHED_AT = "2026-05-11T00:05:00+00:00"

STARTED_REPORT = {
    "job_id": "job-1",
    "session_id": "sess-1",
    "client_source": "codex",
    "evaluator_type": "codex",
    "started_at": STARTED_AT,
}

RESULT = {
    "outcome": "solved",
    "confidence": 0.9,
    "task_title": "Fixed the bug",
    "task_title_zh": "修复错误",
    "summary": "Done",
    "evidence": ["user confirmed"],
    "failure_reason": None,
    "project": "tokenage",
}


def _completed_report(**overrides) -> dict:
    report = {**STARTED_REPORT, "finished_at": FINISHED_AT, "result": RESULT}
    report.update(overrides)
    return report


def _user(fresh_db, email="owner@example.com"):
    from src.auth.tokens import get_or_create_user

    return get_or_create_user(email, db_path=fresh_db.db_path)


def _device_cli_token(fresh_db, email="reporter@example.com", install="install-1"):
    from src.auth.tokens import mint_device_tokens

    user = _user(fresh_db, email)
    cli, ingest, device = mint_device_tokens(
        user.id, install, "laptop", db_path=fresh_db.db_path
    )
    return cli, ingest, device, user


def _session(fresh_db, user_id, session_id="sess-1"):
    from src.database import get_engine
    from src.database.models import SessionRecord

    with Session(get_engine(fresh_db.db_path)) as session:
        record = SessionRecord(
            session_id=session_id,
            user_id=user_id,
            client_source="codex",
            started=TS,
            ended=TS + 1_000_000,
            updated_at="2026-04-17T00:00:00+00:00",
        )
        session.add(record)
        session.commit()


def _jobs(fresh_db):
    from src.database import get_engine
    from src.database.models import EvaluationJob

    with Session(get_engine(fresh_db.db_path)) as session:
        return session.execute(select(EvaluationJob)).scalars().all()


def _session_evaluation(fresh_db, session_id="sess-1"):
    from src.database import get_engine
    from src.database.models import SessionRecord

    with Session(get_engine(fresh_db.db_path)) as session:
        return session.get(SessionRecord, session_id)


def _report_client(api_module, token):
    client = TestClient(api_module.app)
    client.headers["Authorization"] = f"Bearer {token}"
    return client


# ---------------------------------------------------------------------------
# Report route auth: device-bound CLI credentials only
# ---------------------------------------------------------------------------


def test_reports_reject_loopback_owner(api_module, fresh_db):
    _session(fresh_db, _user(fresh_db).id)
    client = TestClient(api_module.app)

    assert (
        client.post("/evaluation-jobs/started", json=STARTED_REPORT).status_code == 403
    )
    assert (
        client.post("/evaluation-jobs/completed", json=_completed_report()).status_code
        == 403
    )
    assert _jobs(fresh_db) == []


def test_reports_reject_web_token(api_module, fresh_db):
    from src.auth.tokens import mint_token

    user = _user(fresh_db)
    _session(fresh_db, user.id)
    web_token, _ = mint_token(user.email, kind="web", db_path=fresh_db.db_path)
    client = _report_client(api_module, web_token)

    assert (
        client.post("/evaluation-jobs/started", json=STARTED_REPORT).status_code == 403
    )
    assert (
        client.post("/evaluation-jobs/completed", json=_completed_report()).status_code
        == 403
    )
    assert _jobs(fresh_db) == []


def test_reports_reject_ingest_token(api_module, fresh_db):
    _cli, ingest, _device, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    client = _report_client(api_module, ingest)

    assert (
        client.post("/evaluation-jobs/started", json=STARTED_REPORT).status_code == 401
    )
    assert (
        client.post("/evaluation-jobs/completed", json=_completed_report()).status_code
        == 401
    )
    assert _jobs(fresh_db) == []


def test_reports_reject_cli_token_without_device(api_module, fresh_db):
    from src.auth.tokens import mint_token

    user = _user(fresh_db)
    _session(fresh_db, user.id)
    cli_token, _ = mint_token(user.email, kind="cli", db_path=fresh_db.db_path)
    client = _report_client(api_module, cli_token)

    assert (
        client.post("/evaluation-jobs/started", json=STARTED_REPORT).status_code == 403
    )
    assert (
        client.post("/evaluation-jobs/completed", json=_completed_report()).status_code
        == 403
    )
    assert _jobs(fresh_db) == []


# ---------------------------------------------------------------------------
# Started reports
# ---------------------------------------------------------------------------


def test_started_report_creates_running_job(api_module, fresh_db):
    cli, _ingest, device, _user_row = _device_cli_token(fresh_db)
    _session(fresh_db, _user_row.id)

    response = _report_client(api_module, cli).post(
        "/evaluation-jobs/started", json=STARTED_REPORT
    )

    assert response.status_code == 204
    jobs = _jobs(fresh_db)
    assert len(jobs) == 1
    assert jobs[0].job_id == "job-1"
    assert jobs[0].status == "running"
    assert jobs[0].trigger == "auto"
    assert jobs[0].device_id == device.id
    assert jobs[0].started_at == STARTED_AT
    assert jobs[0].error is None


def test_started_report_is_idempotent(api_module, fresh_db):
    cli, _ingest, device, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    client = _report_client(api_module, cli)

    assert (
        client.post("/evaluation-jobs/started", json=STARTED_REPORT).status_code == 204
    )
    repeat = client.post(
        "/evaluation-jobs/started",
        json={**STARTED_REPORT, "started_at": "2026-05-11T00:01:00+00:00"},
    )

    assert repeat.status_code == 204
    jobs = _jobs(fresh_db)
    assert len(jobs) == 1
    assert jobs[0].started_at == STARTED_AT
    assert jobs[0].device_id == device.id


def test_started_report_for_foreign_session_is_404(api_module, fresh_db):
    cli, _ingest, _device, _user_row = _device_cli_token(fresh_db)
    other = _user(fresh_db, "other@example.com")
    _session(fresh_db, other.id, session_id="sess-1")

    response = _report_client(api_module, cli).post(
        "/evaluation-jobs/started", json=STARTED_REPORT
    )

    assert response.status_code == 404
    assert _jobs(fresh_db) == []


# ---------------------------------------------------------------------------
# Completed reports
# ---------------------------------------------------------------------------


def test_completed_report_without_start_writes_session_result(api_module, fresh_db):
    cli, _ingest, _device, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)

    response = _report_client(api_module, cli).post(
        "/evaluation-jobs/completed", json=_completed_report()
    )

    assert response.status_code == 204
    jobs = _jobs(fresh_db)
    assert len(jobs) == 1
    assert jobs[0].status == "succeeded"
    assert jobs[0].finished_at == FINISHED_AT
    record = _session_evaluation(fresh_db)
    assert record.outcome == "solved"
    assert record.source == "llm"
    assert record.task_title == "Fixed the bug"
    assert record.task_title_zh == "修复错误"
    assert record.project == "tokenage"
    assert record.evaluated_at == FINISHED_AT


def test_completed_failed_report_stores_error_only(api_module, fresh_db):
    cli, _ingest, _device, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)

    response = _report_client(api_module, cli).post(
        "/evaluation-jobs/completed",
        json=_completed_report(result=None, status="failed", error="evaluator crashed"),
    )

    assert response.status_code == 204
    jobs = _jobs(fresh_db)
    assert jobs[0].status == "failed"
    assert jobs[0].error == "evaluator crashed"
    record = _session_evaluation(fresh_db)
    assert record.outcome is None
    assert record.source is None


def test_completed_report_is_idempotent(api_module, fresh_db):
    cli, _ingest, _device, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    client = _report_client(api_module, cli)

    assert (
        client.post("/evaluation-jobs/completed", json=_completed_report()).status_code
        == 204
    )
    repeat = client.post(
        "/evaluation-jobs/completed",
        json=_completed_report(
            result={**RESULT, "outcome": "failed", "failure_reason": "rewritten"},
        ),
    )

    assert repeat.status_code == 204
    assert len(_jobs(fresh_db)) == 1
    record = _session_evaluation(fresh_db)
    assert record.outcome == "solved"
    assert record.failure_reason is None


def test_started_report_does_not_downgrade_finished_job(api_module, fresh_db):
    cli, _ingest, _device, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    client = _report_client(api_module, cli)

    client.post("/evaluation-jobs/completed", json=_completed_report())
    assert (
        client.post("/evaluation-jobs/started", json=STARTED_REPORT).status_code == 204
    )

    jobs = _jobs(fresh_db)
    assert jobs[0].status == "succeeded"
    assert jobs[0].finished_at == FINISHED_AT


@pytest.mark.parametrize("endpoint", ["started", "completed"])
def test_same_device_cannot_reuse_run_id_for_another_session(
    api_module, fresh_db, endpoint
):
    cli, _, _, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    _session(fresh_db, user.id, "sess-2")
    client = _report_client(api_module, cli)
    assert (
        client.post("/evaluation-jobs/started", json=STARTED_REPORT).status_code == 204
    )
    report = STARTED_REPORT if endpoint == "started" else _completed_report()

    response = client.post(
        f"/evaluation-jobs/{endpoint}", json={**report, "session_id": "sess-2"}
    )

    assert response.status_code == 409
    assert _jobs(fresh_db)[0].session_id == "sess-1"
    assert _jobs(fresh_db)[0].status == "running"
    assert _session_evaluation(fresh_db, "sess-2").outcome is None


def test_manual_annotation_committed_after_session_read_is_preserved(
    api_module, fresh_db, monkeypatch
):
    from src.database import evaluation_jobs
    from src.database.models import SessionRecord

    cli, _, _, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    original = evaluation_jobs.get_session_record

    def stale_record(db, *args, **kwargs):
        record = original(db, *args, **kwargs)
        # Keep the ORM object's earlier source=None while the database now
        # contains a manual annotation, as with a competing writer on SQLite.
        db.execute(
            update(SessionRecord)
            .where(SessionRecord.session_id == "sess-1")
            .values(source="manual", outcome="partial")
            .execution_options(synchronize_session=False)
        )
        return record

    monkeypatch.setattr(evaluation_jobs, "get_session_record", stale_record)
    response = _report_client(api_module, cli).post(
        "/evaluation-jobs/completed", json=_completed_report()
    )

    assert response.status_code == 204
    assert _session_evaluation(fresh_db).source == "manual"
    assert _session_evaluation(fresh_db).outcome == "partial"


def test_manual_evaluation_wins_over_completed_report(api_module, fresh_db):
    from src.database import upsert_session_evaluation

    cli, _ingest, _device, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    upsert_session_evaluation(
        session_id="sess-1",
        outcome="partial",
        source="manual",
        user_id=user.id,
        db_path=fresh_db.db_path,
    )

    response = _report_client(api_module, cli).post(
        "/evaluation-jobs/completed", json=_completed_report()
    )

    assert response.status_code == 204
    record = _session_evaluation(fresh_db)
    assert record.outcome == "partial"
    assert record.source == "manual"
    assert _jobs(fresh_db)[0].status == "succeeded"


def test_older_completed_report_cannot_overwrite_newer_result(api_module, fresh_db):
    cli, _ingest, _device, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    client = _report_client(api_module, cli)

    # An older attempt stays running while a newer run finishes first.
    client.post(
        "/evaluation-jobs/started", json={**STARTED_REPORT, "job_id": "job-old"}
    )
    client.post(
        "/evaluation-jobs/completed",
        json=_completed_report(
            job_id="job-new",
            finished_at="2026-05-11T00:10:00+00:00",
            result={**RESULT, "outcome": "solved"},
        ),
    )
    late = client.post(
        "/evaluation-jobs/completed",
        json=_completed_report(
            job_id="job-old",
            started_at="2026-05-11T00:00:00+00:00",
            finished_at="2026-05-11T00:03:00+00:00",
            result={**RESULT, "outcome": "failed", "failure_reason": "stale"},
        ),
    )

    assert late.status_code == 204
    record = _session_evaluation(fresh_db)
    assert record.outcome == "solved"
    assert record.failure_reason is None
    # The late run is still recorded as run metadata.
    assert {job.job_id: job.status for job in _jobs(fresh_db)} == {
        "job-old": "succeeded",
        "job-new": "succeeded",
    }


def test_fast_client_clock_does_not_block_later_evaluations(api_module, fresh_db):
    from datetime import UTC, datetime

    cli, _, _, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    client = _report_client(api_module, cli)
    assert (
        client.post(
            "/evaluation-jobs/completed",
            json=_completed_report(finished_at="2099-01-01T00:00:00+00:00"),
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/evaluation-jobs/completed",
            json=_completed_report(
                job_id="correct-clock",
                finished_at=datetime.now(UTC).isoformat(),
                result={**RESULT, "outcome": "partial"},
            ),
        ).status_code
        == 204
    )
    assert _session_evaluation(fresh_db).outcome == "partial"


def test_completed_report_for_foreign_session_is_404(api_module, fresh_db):
    cli, _ingest, _device, _user_row = _device_cli_token(fresh_db)
    other = _user(fresh_db, "other@example.com")
    _session(fresh_db, other.id)

    response = _report_client(api_module, cli).post(
        "/evaluation-jobs/completed", json=_completed_report()
    )

    assert response.status_code == 404
    assert _jobs(fresh_db) == []


# ---------------------------------------------------------------------------
# Cross-user / cross-device job collisions
# ---------------------------------------------------------------------------


def test_cross_user_job_id_collision_does_not_mutate(api_module, fresh_db):
    owner_cli, _ingest, owner_device, owner = _device_cli_token(
        fresh_db, email="owner@example.com", install="install-owner"
    )
    _session(fresh_db, owner.id)
    _report_client(api_module, owner_cli).post(
        "/evaluation-jobs/started", json=STARTED_REPORT
    )

    intruder_cli, _ingest2, _device2, intruder = _device_cli_token(
        fresh_db, email="intruder@example.com", install="install-intruder"
    )
    _session(fresh_db, intruder.id, session_id="sess-intruder")
    intruder_client = _report_client(api_module, intruder_cli)

    started = intruder_client.post(
        "/evaluation-jobs/started",
        json={**STARTED_REPORT, "session_id": "sess-intruder"},
    )
    completed = intruder_client.post(
        "/evaluation-jobs/completed",
        json=_completed_report(session_id="sess-intruder"),
    )

    assert started.status_code == 409
    assert completed.status_code == 409
    jobs = _jobs(fresh_db)
    assert len(jobs) == 1
    assert jobs[0].user_id == owner.id
    assert jobs[0].device_id == owner_device.id
    assert jobs[0].status == "running"
    assert _session_evaluation(fresh_db, session_id="sess-1").outcome is None


def test_cross_device_same_user_job_id_collision_does_not_mutate(api_module, fresh_db):
    first_cli, _ingest, first_device, user = _device_cli_token(
        fresh_db, install="install-1"
    )
    _session(fresh_db, user.id)
    _report_client(api_module, first_cli).post(
        "/evaluation-jobs/started", json=STARTED_REPORT
    )

    second_cli, _ingest2, _second_device, _user2 = _device_cli_token(
        fresh_db, email=user.email, install="install-2"
    )
    response = _report_client(api_module, second_cli).post(
        "/evaluation-jobs/completed", json=_completed_report()
    )

    assert response.status_code == 409
    jobs = _jobs(fresh_db)
    assert len(jobs) == 1
    assert jobs[0].device_id == first_device.id
    assert jobs[0].status == "running"


def test_job_reads_are_user_scoped(api_module, fresh_db):
    owner_cli, _ingest, _device, owner = _device_cli_token(
        fresh_db, email="owner@example.com", install="install-owner"
    )
    _session(fresh_db, owner.id)
    _report_client(api_module, owner_cli).post(
        "/evaluation-jobs/completed", json=_completed_report()
    )

    intruder_cli, _ingest2, _device2, intruder = _device_cli_token(
        fresh_db, email="intruder@example.com", install="install-intruder"
    )
    _session(fresh_db, intruder.id, session_id="sess-2")
    intruder_client = _report_client(api_module, intruder_cli)

    assert intruder_client.get("/evaluation-jobs/job-1").status_code == 404
    history = intruder_client.get("/sessions/sess-1/evaluation-jobs").json()
    assert history == {"jobs": []}
    active = intruder_client.get("/evaluation-jobs/active").json()
    assert active == {"jobs": {}}


# ---------------------------------------------------------------------------
# Read routes: run metadata only, no queue positions or payloads
# ---------------------------------------------------------------------------


def test_active_jobs_return_running_runs_only(api_module, fresh_db):
    cli, _ingest, _device, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    client = _report_client(api_module, cli)

    client.post("/evaluation-jobs/started", json=STARTED_REPORT)
    client.post(
        "/evaluation-jobs/completed",
        json=_completed_report(job_id="job-2"),
    )

    response = client.get("/evaluation-jobs/active")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"jobs"}
    job = payload["jobs"]["sess-1"]
    assert job["job_id"] == "job-1"
    assert job["status"] == "running"
    assert "queue_position" not in job
    assert "ahead_count" not in job
    assert "outcome" not in job


def test_session_history_returns_metadata_only(api_module, fresh_db):
    from src.database import upsert_session_evaluation

    cli, _ingest, _device, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    upsert_session_evaluation(
        session_id="sess-1",
        outcome="solved",
        source="llm",
        user_id=user.id,
        db_path=fresh_db.db_path,
    )
    client = _report_client(api_module, cli)
    client.post("/evaluation-jobs/completed", json=_completed_report())

    response = client.get("/sessions/sess-1/evaluation-jobs")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"jobs"}
    job = payload["jobs"][0]
    assert job["status"] == "succeeded"
    assert "outcome" not in job
    assert "queue_position" not in job
    assert "ahead_count" not in job
    assert "result" not in job


def test_job_rows_never_store_result_snapshots(fresh_db):
    from src.database.models import EvaluationJob

    columns = {column.name for column in EvaluationJob.__table__.columns}
    assert columns == {
        "job_id",
        "user_id",
        "device_id",
        "kind",
        "session_id",
        "client_source",
        "trigger",
        "evaluator_type",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "error",
    }


def test_legacy_succeeded_job_is_not_labeled_with_current_outcome(api_module, fresh_db):
    from src.database import get_engine, text, upsert_session_evaluation

    cli, _ingest, _device, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    with get_engine(fresh_db.db_path).begin() as connection:
        connection.execute(
            text(
                "INSERT INTO evaluation_jobs (job_id, user_id, kind, session_id, "
                "status, created_at, finished_at) VALUES ('legacy-job', :user, "
                "'session_evaluation', 'sess-1', 'succeeded', :now, :now)"
            ),
            {"user": user.id, "now": FINISHED_AT},
        )
    upsert_session_evaluation(
        session_id="sess-1",
        outcome="failed",
        source="manual",
        user_id=user.id,
        db_path=fresh_db.db_path,
    )

    history = (
        _report_client(api_module, cli).get("/sessions/sess-1/evaluation-jobs").json()
    )

    assert history["jobs"][0]["job_id"] == "legacy-job"
    assert "outcome" not in history["jobs"][0]


# ---------------------------------------------------------------------------
# Migration: device_id column, legacy index, stale queue rows
# ---------------------------------------------------------------------------


def test_migrate_adds_evaluation_job_device_id(fresh_db):
    from src.database import get_engine, text

    engine = get_engine(fresh_db.db_path)
    # Rebuild the table without device_id: SQLite cannot drop a column that a
    # foreign-key constraint references.
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE evaluation_jobs RENAME TO evaluation_jobs_old")
        )
        connection.execute(
            text(
                "CREATE TABLE evaluation_jobs (job_id VARCHAR PRIMARY KEY, "
                "user_id VARCHAR NOT NULL REFERENCES users (id), kind VARCHAR NOT NULL, "
                "session_id VARCHAR NOT NULL, client_source VARCHAR, "
                "trigger VARCHAR NOT NULL DEFAULT 'auto', evaluator_type VARCHAR "
                "NOT NULL DEFAULT 'codex', status VARCHAR NOT NULL, "
                "created_at VARCHAR NOT NULL, started_at VARCHAR, finished_at VARCHAR, "
                "error VARCHAR)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO evaluation_jobs SELECT job_id, user_id, kind, session_id, "
                "client_source, trigger, evaluator_type, status, created_at, "
                "started_at, finished_at, error FROM evaluation_jobs_old"
            )
        )
        connection.execute(text("DROP TABLE evaluation_jobs_old"))

    changes = fresh_db.schema_migrations_module.migrate_database(fresh_db.db_path)

    assert "evaluation_jobs.device_id" in changes
    assert "device_id" in {
        column["name"] for column in inspect(engine).get_columns("evaluation_jobs")
    }
    assert fresh_db.schema_migrations_module.migrate_database(fresh_db.db_path) == []


def test_migrate_retires_legacy_queue_index_and_rows(fresh_db):
    from src.database import get_engine, text

    engine = get_engine(fresh_db.db_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, created_at) VALUES ('u-legacy', "
                "'legacy@example.com', 0)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX ix_evaluation_jobs_one_active_per_session "
                "ON evaluation_jobs (kind, session_id) "
                "WHERE status IN ('queued', 'running')"
            )
        )
        for job_id, status in (
            ("legacy-queued", "queued"),
            ("legacy-running", "running"),
        ):
            connection.execute(
                text(
                    "INSERT INTO evaluation_jobs (job_id, user_id, kind, session_id, "
                    "status, created_at) VALUES (:job_id, 'u-legacy', "
                    "'session_evaluation', :session, :status, :created)"
                ),
                {
                    "job_id": job_id,
                    "session": f"sess-{job_id}",
                    "status": status,
                    "created": STARTED_AT,
                },
            )

    changes = fresh_db.schema_migrations_module.migrate_database(fresh_db.db_path)

    assert "evaluation_jobs.drop_one_active_index" in changes
    assert "evaluation_jobs.fail_legacy_active" in changes
    assert "ix_evaluation_jobs_one_active_per_session" not in {
        index["name"] for index in inspect(engine).get_indexes("evaluation_jobs")
    }
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT job_id, status, error, finished_at FROM evaluation_jobs "
                "ORDER BY job_id"
            )
        ).all()
    assert {(row.job_id, row.status) for row in rows} == {
        ("legacy-queued", "failed"),
        ("legacy-running", "failed"),
    }
    assert all(row.error == "Evaluation moved to the client" for row in rows)
    assert all(row.finished_at == STARTED_AT for row in rows)
    assert fresh_db.schema_migrations_module.migrate_database(fresh_db.db_path) == []


def test_migration_does_not_retire_running_client_evaluations(api_module, fresh_db):
    cli, _, _, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    client = _report_client(api_module, cli)
    assert (
        client.post("/evaluation-jobs/started", json=STARTED_REPORT).status_code == 204
    )

    fresh_db.schema_migrations_module.migrate_database(fresh_db.db_path)

    assert _jobs(fresh_db)[0].status == "running"
    assert (
        client.post("/evaluation-jobs/completed", json=_completed_report()).status_code
        == 204
    )
    assert _session_evaluation(fresh_db).outcome == "solved"


def test_old_start_only_report_stays_in_history_but_not_active(api_module, fresh_db):
    from src.database import get_engine
    from src.database.models import EvaluationJob

    cli, _, _, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    client = _report_client(api_module, cli)
    client.post("/evaluation-jobs/started", json=STARTED_REPORT)
    with Session(get_engine(fresh_db.db_path)) as db:
        db.execute(
            update(EvaluationJob)
            .where(EvaluationJob.job_id == "job-1")
            .values(created_at=STARTED_AT)
        )
        db.commit()

    assert client.get("/evaluation-jobs/active").json() == {"jobs": {}}
    assert len(client.get("/sessions/sess-1/evaluation-jobs").json()["jobs"]) == 1


def test_active_view_prefers_latest_run_for_same_session(api_module, fresh_db):
    cli, _, _, user = _device_cli_token(fresh_db)
    _session(fresh_db, user.id)
    client = _report_client(api_module, cli)
    client.post("/evaluation-jobs/started", json=STARTED_REPORT)
    client.post(
        "/evaluation-jobs/started", json={**STARTED_REPORT, "job_id": "new-attempt"}
    )
    assert (
        client.get("/evaluation-jobs/active").json()["jobs"]["sess-1"]["job_id"]
        == "new-attempt"
    )
