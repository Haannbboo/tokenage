"""Compatibility tests for retained evaluation job migrations."""

from sqlalchemy import inspect

STARTED_AT = "2026-05-11T00:00:00+00:00"


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
