"""Persistence helpers for asynchronous session evaluation jobs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .engine import get_engine
from .models import EvaluationJob, SessionRecord
from .sessions import get_session_record

SESSION_EVALUATION_JOB_KIND = "session_evaluation"
ACTIVE_EVALUATION_JOB_STATUSES = {"queued", "running"}
EVALUATION_JOB_TIMEOUT_SECONDS = 5 * 60
VALID_EVALUATION_JOB_TRIGGERS = {"manual", "auto"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    normalized = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _job_to_dict(job: EvaluationJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "kind": job.kind,
        "session_id": job.session_id,
        "user_id": job.user_id,
        "client_source": job.client_source,
        "status": job.status,
        "trigger": job.trigger,
        "evaluator_type": job.evaluator_type,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
    }


def create_session_evaluation_job(
    *,
    session_id: str,
    client_source: str | None,
    user_id: str,
    trigger: str = "manual",
    evaluator_type: str = "codex",
    created_at: str | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    if trigger not in VALID_EVALUATION_JOB_TRIGGERS:
        raise ValueError(f"Invalid evaluation job trigger: {trigger}")

    job = EvaluationJob(
        job_id=str(uuid4()),
        kind=SESSION_EVALUATION_JOB_KIND,
        session_id=session_id,
        user_id=user_id,
        client_source=client_source,
        trigger=trigger,
        evaluator_type=evaluator_type,
        status="queued",
        created_at=created_at or _now_iso(),
    )

    with Session(get_engine(db_path)) as session:
        session.add(job)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            active_job = (
                session.execute(
                    select(EvaluationJob)
                    .where(
                        and_(
                            EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
                            EvaluationJob.session_id == session_id,
                            EvaluationJob.user_id == user_id,
                            EvaluationJob.status.in_(ACTIVE_EVALUATION_JOB_STATUSES),
                        )
                    )
                    .order_by(EvaluationJob.created_at.desc())
                )
                .scalars()
                .first()
            )
            if active_job is None:
                raise
            return _job_to_dict(active_job)
        session.refresh(job)
        return _job_to_dict(job)


def get_evaluation_job(
    job_id: str,
    user_id: str | None = None,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    # ponytail: user_id=None is the evaluation worker's all-tenants view; it goes
    # away when evaluation moves to the client.
    with Session(get_engine(db_path)) as session:
        query = select(EvaluationJob).where(EvaluationJob.job_id == job_id)
        if user_id is not None:
            query = query.where(EvaluationJob.user_id == user_id)
        job = session.scalar(query)
        return _job_to_dict(job) if job else None


def promote_evaluation_job_to_manual(
    job_id: str,
    *,
    evaluator_type: str | None = None,
    user_id: str,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    values: dict[str, Any] = {"trigger": "manual"}
    if evaluator_type is not None:
        values["evaluator_type"] = evaluator_type
    with Session(get_engine(db_path)) as session:
        filters = [
            EvaluationJob.job_id == job_id,
            EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
            EvaluationJob.status == "queued",
            EvaluationJob.trigger == "auto",
            EvaluationJob.user_id == user_id,
        ]
        session.execute(update(EvaluationJob).where(and_(*filters)).values(**values))
        session.commit()
        job = session.get(EvaluationJob, job_id)
        return _job_to_dict(job) if job else None


def update_queued_evaluation_job_evaluator(
    job_id: str,
    *,
    evaluator_type: str,
    user_id: str,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    with Session(get_engine(db_path)) as session:
        filters = [
            EvaluationJob.job_id == job_id,
            EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
            EvaluationJob.status == "queued",
            EvaluationJob.user_id == user_id,
        ]
        result = session.execute(
            update(EvaluationJob)
            .where(and_(*filters))
            .values(evaluator_type=evaluator_type)
        )
        session.commit()
        if result.rowcount != 1:  # type: ignore[attr-defined]
            return None
        job = session.get(EvaluationJob, job_id)
        return _job_to_dict(job) if job else None


def _queued_job_order():
    return (
        case((EvaluationJob.trigger == "manual", 0), else_=1).asc(),
        SessionRecord.updated_at.desc(),
        EvaluationJob.created_at.asc(),
    )


def _next_queued_job_id(session: Session) -> str | None:
    return session.scalar(
        select(EvaluationJob.job_id)
        .join(
            SessionRecord,
            SessionRecord.session_id == EvaluationJob.session_id,
        )
        .where(
            and_(
                EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
                EvaluationJob.status == "queued",
            )
        )
        .order_by(*_queued_job_order())
    )


def claim_next_evaluation_job(
    *,
    now: str | None = None,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    started_at = now or _now_iso()
    with Session(get_engine(db_path)) as session:
        while True:
            job_id = _next_queued_job_id(session)
            if job_id is None:
                return None
            result = session.execute(
                update(EvaluationJob)
                .where(
                    and_(
                        EvaluationJob.job_id == job_id,
                        EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
                        EvaluationJob.status == "queued",
                    )
                )
                .values(status="running", started_at=started_at, error=None)
            )
            session.commit()
            if result.rowcount != 1:  # type: ignore[attr-defined]
                continue
            job = session.get(EvaluationJob, job_id)
            return _job_to_dict(job) if job else None


def count_running_evaluation_jobs(db_path: str | None = None) -> int:
    with Session(get_engine(db_path)) as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(EvaluationJob)
                .where(
                    and_(
                        EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
                        EvaluationJob.status == "running",
                    )
                )
            )
            or 0
        )


def fail_stale_running_evaluation_jobs(
    *,
    now: str | None = None,
    db_path: str | None = None,
) -> int:
    now_dt = _parse_iso(now) if now is not None else datetime.now(UTC)
    stale_before = now_dt - timedelta(seconds=EVALUATION_JOB_TIMEOUT_SECONDS)
    failed = 0
    with Session(get_engine(db_path)) as session:
        jobs = (
            session.execute(
                select(EvaluationJob).where(
                    and_(
                        EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
                        EvaluationJob.status == "running",
                    )
                )
            )
            .scalars()
            .all()
        )
        for job in jobs:
            marker = job.started_at or job.created_at
            if _parse_iso(marker) <= stale_before:
                failed += _fail_stale_evaluation_job(
                    session,
                    job,
                    now_dt.isoformat(),
                )
        session.commit()
    return failed


def _stale_marker(job: EvaluationJob):
    if job.status == "running" and job.started_at:
        return job.started_at, EvaluationJob.started_at
    return job.created_at, EvaluationJob.created_at


def _fail_stale_evaluation_job(
    session: Session,
    job: EvaluationJob,
    finished_at: str,
) -> int:
    marker, marker_column = _stale_marker(job)
    result = session.execute(
        update(EvaluationJob)
        .where(
            and_(
                EvaluationJob.job_id == job.job_id,
                EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
                EvaluationJob.status == job.status,
                marker_column == marker,
            )
        )
        .values(
            status="failed",
            error="Evaluation job timed out",
            finished_at=finished_at,
        )
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def list_active_evaluation_jobs(
    *,
    session_ids: list[str] | None = None,
    user_id: str | None = None,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    # ponytail: user_id=None is the evaluation worker's all-tenants view.
    with Session(get_engine(db_path)) as session:
        query = select(EvaluationJob).where(
            and_(
                EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
                EvaluationJob.status.in_(ACTIVE_EVALUATION_JOB_STATUSES),
            )
        )
        if session_ids:
            query = query.where(EvaluationJob.session_id.in_(session_ids))
        if user_id is not None:
            query = query.where(EvaluationJob.user_id == user_id)
        jobs = session.execute(query).scalars().all()
        return [_job_to_dict(job) for job in jobs]


def list_active_evaluation_jobs_with_progress(
    *,
    session_ids: list[str] | None = None,
    user_id: str,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    progress = _active_progress_map(user_id=user_id, db_path=db_path)
    jobs = list_active_evaluation_jobs(
        session_ids=session_ids, user_id=user_id, db_path=db_path
    )
    return [
        {
            **job,
            **progress.get(
                job["job_id"],
                {"ahead_count": 0, "queue_position": None},
            ),
        }
        for job in jobs
    ]


def _active_progress_map(
    user_id: str, db_path: str | None = None
) -> dict[str, dict[str, int]]:
    with Session(get_engine(db_path)) as session:
        running_query = select(EvaluationJob).where(
            and_(
                EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
                EvaluationJob.status == "running",
            )
        )
        running_query = running_query.where(EvaluationJob.user_id == user_id)
        running = (
            session.execute(
                running_query.order_by(
                    EvaluationJob.started_at.asc(), EvaluationJob.created_at.asc()
                )
            )
            .scalars()
            .all()
        )

        queued_query = (
            select(EvaluationJob)
            .join(SessionRecord, SessionRecord.session_id == EvaluationJob.session_id)
            .where(
                and_(
                    EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
                    EvaluationJob.status == "queued",
                )
            )
        )
        queued_query = queued_query.where(EvaluationJob.user_id == user_id)
        queued = (
            session.execute(queued_query.order_by(*_queued_job_order())).scalars().all()
        )

    progress: dict[str, dict[str, int]] = {}
    running_count = len(running)
    for job in running:
        progress[job.job_id] = {"ahead_count": 0, "queue_position": 1}
    for index, job in enumerate(queued):
        ahead_count = running_count + index
        progress[job.job_id] = {
            "ahead_count": ahead_count,
            "queue_position": ahead_count + 1,
        }
    return progress


def get_evaluation_job_progress(
    job_id: str,
    user_id: str,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    job = get_evaluation_job(job_id, user_id=user_id, db_path=db_path)
    if job is None:
        return None
    progress = _active_progress_map(user_id=user_id, db_path=db_path).get(
        job_id,
        {"ahead_count": 0, "queue_position": None},
    )
    return {**job, **progress}


def list_session_evaluation_jobs_with_progress(
    session_id: str,
    *,
    user_id: str,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    progress = _active_progress_map(user_id=user_id, db_path=db_path)
    engine = get_engine(db_path)
    with Session(engine) as session:
        rec = get_session_record(session, session_id, user_id=user_id)
        outcome = rec.outcome if rec else None
        jobs = (
            session.execute(
                select(EvaluationJob)
                .where(
                    and_(
                        EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
                        EvaluationJob.session_id == session_id,
                        EvaluationJob.user_id == user_id,
                    )
                )
                .order_by(EvaluationJob.created_at.desc())
            )
            .scalars()
            .all()
        )
    return [
        {
            **_job_to_dict(job),
            **progress.get(
                job.job_id,
                {"ahead_count": 0, "queue_position": None},
            ),
            **({"outcome": outcome} if job.status == "succeeded" and outcome else {}),
        }
        for job in jobs
    ]


def find_active_session_evaluation_job(
    *,
    session_id: str,
    user_id: str,
    now: str | None = None,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    now_dt = _parse_iso(now) if now is not None else datetime.now(UTC)
    stale_before = now_dt - timedelta(seconds=EVALUATION_JOB_TIMEOUT_SECONDS)

    with Session(get_engine(db_path)) as session:
        jobs = (
            session.execute(
                select(EvaluationJob)
                .where(
                    and_(
                        EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
                        EvaluationJob.session_id == session_id,
                        EvaluationJob.user_id == user_id,
                        EvaluationJob.status.in_(ACTIVE_EVALUATION_JOB_STATUSES),
                    )
                )
                .order_by(EvaluationJob.created_at.desc())
            )
            .scalars()
            .all()
        )

        for job in jobs:
            marker = job.started_at if job.status == "running" else job.created_at
            if _parse_iso(marker or job.created_at) <= stale_before:
                _fail_stale_evaluation_job(
                    session,
                    job,
                    now_dt.isoformat(),
                )
                continue

            session.commit()
            return _job_to_dict(job)

        session.commit()
        return None


def mark_evaluation_job_running(
    job_id: str,
    *,
    db_path: str | None = None,
) -> bool:
    now = _now_iso()
    with Session(get_engine(db_path)) as session:
        result = session.execute(
            update(EvaluationJob)
            .where(
                and_(
                    EvaluationJob.job_id == job_id,
                    EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
                    EvaluationJob.status == "queued",
                )
            )
            .values(status="running", started_at=now, error=None)
        )
        session.commit()
        return result.rowcount == 1  # type: ignore[attr-defined]


def mark_evaluation_job_succeeded(
    job_id: str,
    *,
    db_path: str | None = None,
) -> None:
    now = _now_iso()
    with Session(get_engine(db_path)) as session:
        session.execute(
            update(EvaluationJob)
            .where(
                and_(
                    EvaluationJob.job_id == job_id,
                    EvaluationJob.status == "running",
                )
            )
            .values(status="succeeded", finished_at=now, error=None)
        )
        session.commit()


def mark_evaluation_job_failed(
    job_id: str,
    error: str,
    *,
    db_path: str | None = None,
) -> None:
    now = _now_iso()
    with Session(get_engine(db_path)) as session:
        session.execute(
            update(EvaluationJob)
            .where(
                and_(
                    EvaluationJob.job_id == job_id,
                    EvaluationJob.status == "running",
                )
            )
            .values(status="failed", finished_at=now, error=error)
        )
        session.commit()
