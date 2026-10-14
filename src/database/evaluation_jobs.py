"""Persistence for client-reported session evaluation runs.

The client runs the evaluator on its own machine and reports start/finish
events; the server keeps run metadata only. The latest result lives on the
session row (see ``_apply_llm_result``), never on the job.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .engine import get_engine
from .models import EvaluationJob, SessionRecord
from .sessions import get_session_record

SESSION_EVALUATION_JOB_KIND = "session_evaluation"
TERMINAL_EVALUATION_JOB_STATUSES = frozenset({"succeeded", "failed"})
# Evaluators time out after five minutes; old start-only reports are history,
# not proof that a client process is still running. No server lease is involved.
ACTIVE_RUN_WINDOW_SECONDS = 15 * 60


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _job_to_dict(job: EvaluationJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "kind": job.kind,
        "session_id": job.session_id,
        "user_id": job.user_id,
        "device_id": job.device_id,
        "client_source": job.client_source,
        "status": job.status,
        "trigger": job.trigger,
        "evaluator_type": job.evaluator_type,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
    }


def _job_belongs_to(
    job: EvaluationJob, *, user_id: str, device_id: str, session_id: str
) -> bool:
    return (
        job.user_id == user_id
        and job.device_id == device_id
        and job.session_id == session_id
    )


def record_evaluation_started(
    *,
    job_id: str,
    session_id: str,
    client_source: str,
    evaluator_type: str,
    started_at: datetime,
    user_id: str,
    device_id: str,
    db_path: str | None = None,
) -> str:
    """Record a started run. Returns "ok" or "conflict".

    Repeated reports for the same job id are idempotent; a job id that already
    belongs to another user or device is a conflict and mutates nothing.
    Raises ValueError when the session does not belong to the caller.
    """
    started_at = started_at.astimezone(UTC)
    with Session(get_engine(db_path)) as session:
        job = session.get(EvaluationJob, job_id)
        if job is not None:
            if not _job_belongs_to(
                job, user_id=user_id, device_id=device_id, session_id=session_id
            ):
                return "conflict"
            if job.status not in TERMINAL_EVALUATION_JOB_STATUSES:
                job.started_at = job.started_at or started_at.isoformat()
                session.commit()
            return "ok"

        if get_session_record(session, session_id, user_id=user_id) is None:
            raise ValueError(f"Session not found: {session_id}")

        session.add(
            EvaluationJob(
                job_id=job_id,
                user_id=user_id,
                device_id=device_id,
                kind=SESSION_EVALUATION_JOB_KIND,
                session_id=session_id,
                client_source=client_source,
                trigger="auto",
                evaluator_type=evaluator_type,
                status="running",
                created_at=_now_iso(),
                started_at=started_at.isoformat(),
            )
        )
        try:
            session.commit()
        except IntegrityError:
            # A concurrent retry won the insert; accept it when it is ours.
            session.rollback()
            winner = session.get(EvaluationJob, job_id)
            if winner is None:
                raise
            return (
                "ok"
                if _job_belongs_to(
                    winner, user_id=user_id, device_id=device_id, session_id=session_id
                )
                else "conflict"
            )
        return "ok"


def record_evaluation_completed(
    *,
    job_id: str,
    session_id: str,
    client_source: str,
    evaluator_type: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    result: dict[str, Any] | None,
    error: str | None,
    user_id: str,
    device_id: str,
    db_path: str | None = None,
) -> str:
    """Record a finished run, creating the run when no start report arrived.

    Returns "ok" or "conflict". Repeats for an already-finished job id are
    idempotent and do not rewrite the session result. Raises ValueError when
    the session does not belong to the caller.
    """
    started_at = started_at.astimezone(UTC)
    finished_at = finished_at.astimezone(UTC)
    for attempt in range(2):
        with Session(get_engine(db_path)) as session:
            try:
                record = get_session_record(
                    session, session_id, user_id=user_id, with_for_update=True
                )
                if record is None:
                    raise ValueError(f"Session not found: {session_id}")
                job = session.get(EvaluationJob, job_id)
                if job is not None:
                    if not _job_belongs_to(
                        job, user_id=user_id, device_id=device_id, session_id=session_id
                    ):
                        return "conflict"
                    if job.status in TERMINAL_EVALUATION_JOB_STATUSES:
                        return "ok"
                    # Only the winning completion may apply a result, even if
                    # two requests read the same running row before either writes.
                    changed = session.execute(
                        update(EvaluationJob)
                        .where(
                            EvaluationJob.job_id == job_id,
                            EvaluationJob.status.not_in(
                                TERMINAL_EVALUATION_JOB_STATUSES
                            ),
                        )
                        .values(
                            status=status,
                            finished_at=finished_at.isoformat(),
                            error=error if status == "failed" else None,
                        )
                        .execution_options(synchronize_session=False)
                    )
                    if changed.rowcount != 1:  # type: ignore[attr-defined]
                        return "ok"
                else:
                    session.add(
                        EvaluationJob(
                            job_id=job_id,
                            user_id=user_id,
                            device_id=device_id,
                            kind=SESSION_EVALUATION_JOB_KIND,
                            session_id=session_id,
                            client_source=client_source,
                            trigger="auto",
                            evaluator_type=evaluator_type,
                            status=status,
                            created_at=_now_iso(),
                            started_at=started_at.isoformat(),
                            finished_at=finished_at.isoformat(),
                            error=error if status == "failed" else None,
                        )
                    )
                    session.flush()
                if status == "succeeded" and result is not None:
                    _apply_llm_result(session, record, result, finished_at)
                session.commit()
                return "ok"
            except IntegrityError:
                session.rollback()
                # A simultaneous start may have created a still-running row;
                # re-read it rather than acknowledging a completion we lost.
                if attempt:
                    raise
    raise RuntimeError("evaluation completion could not be recorded")


def _apply_llm_result(
    session: Session,
    record: SessionRecord,
    result: dict[str, Any],
    finished_at: datetime,
) -> None:
    """Write a completed run's result onto the session.

    Conditions are checked in the UPDATE, not against a potentially stale ORM
    object: SQLite does not support the caller's SELECT FOR UPDATE row lock.
    """
    # A fast client clock must not pin the session's latest result in the future
    # and silently prevent subsequent evaluations after its clock is corrected.
    finished_at = min(finished_at, datetime.now(UTC))
    evidence = result.get("evidence") or []
    session.execute(
        update(SessionRecord)
        .where(
            SessionRecord.session_id == record.session_id,
            SessionRecord.user_id == record.user_id,
            or_(SessionRecord.source.is_(None), SessionRecord.source != "manual"),
            or_(
                SessionRecord.evaluated_at.is_(None),
                SessionRecord.evaluated_at <= finished_at.isoformat(),
            ),
        )
        .values(
            outcome=result["outcome"],
            source="llm",
            confidence=result.get("confidence"),
            task_title=result.get("task_title"),
            task_title_zh=result.get("task_title_zh"),
            summary=result.get("summary"),
            evidence_json=json.dumps(evidence) if evidence else None,
            failure_reason=result.get("failure_reason"),
            project=result.get("project"),
            evaluated_at=finished_at.isoformat(),
        )
        .execution_options(synchronize_session=False)
    )


def get_evaluation_job(
    job_id: str,
    *,
    user_id: str,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    with Session(get_engine(db_path)) as session:
        job = session.scalar(
            select(EvaluationJob).where(
                EvaluationJob.job_id == job_id,
                EvaluationJob.user_id == user_id,
            )
        )
        return _job_to_dict(job) if job else None


def list_active_evaluation_jobs(
    *,
    session_ids: list[str] | None = None,
    user_id: str,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Recently reported running attempts; start-only orphans remain in history."""
    query = select(EvaluationJob).where(
        EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
        EvaluationJob.status == "running",
        EvaluationJob.user_id == user_id,
        EvaluationJob.created_at
        >= (
            datetime.now(UTC) - timedelta(seconds=ACTIVE_RUN_WINDOW_SECONDS)
        ).isoformat(),
    )
    if session_ids:
        query = query.where(EvaluationJob.session_id.in_(session_ids))
    with Session(get_engine(db_path)) as session:
        jobs = session.scalars(query.order_by(EvaluationJob.created_at.desc()))
        return [_job_to_dict(job) for job in jobs]


def list_session_evaluation_jobs(
    session_id: str,
    *,
    user_id: str,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Run history for one session, newest first."""
    with Session(get_engine(db_path)) as session:
        jobs = session.scalars(
            select(EvaluationJob)
            .where(
                EvaluationJob.kind == SESSION_EVALUATION_JOB_KIND,
                EvaluationJob.session_id == session_id,
                EvaluationJob.user_id == user_id,
            )
            .order_by(EvaluationJob.created_at.desc())
        )
        return [_job_to_dict(job) for job in jobs]
