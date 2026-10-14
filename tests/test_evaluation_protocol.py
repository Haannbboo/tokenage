"""Bounded evaluation reports are the client/server trust boundary."""

import pytest
from pydantic import ValidationError

from protocol.evaluation import EvaluationCompletedReport, EvaluationResult


def completion(**changes):
    return {
        "job_id": "run-1",
        "session_id": "session-1",
        "client_source": "claude-code",
        "evaluator_type": "claude",
        "started_at": "2026-10-14T09:00:00Z",
        "finished_at": "2026-10-14T09:01:00Z",
        "result": {"outcome": "solved", "confidence": 1},
        **changes,
    }


def test_completion_contains_enough_metadata_without_started_report():
    report = EvaluationCompletedReport.model_validate(completion())
    assert report.result.outcome == "solved"
    assert report.model_dump(mode="json")["started_at"].endswith("Z")
    failed = EvaluationCompletedReport.model_validate(
        completion(status="failed", result=None, error="Evaluation timed out")
    )
    assert failed.result is None


@pytest.mark.parametrize(
    "changes",
    [
        {"user_id": "forged-user"},
        {"device_id": "forged-device"},
        {"transcript": "private text"},
        {"result": None},
        {"status": "failed"},
        {"finished_at": "2026-10-14T08:00:00Z"},
        {"started_at": "2026-10-14T09:00:00"},
        {"session_id": "x" * 257},
        {"error": "x" * 1025},
    ],
)
def test_report_rejects_invalid_identity_payloads_and_timestamps(changes):
    with pytest.raises(ValidationError):
        EvaluationCompletedReport.model_validate(completion(**changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": "invented"},
        {"confidence": True},
        {"confidence": float("nan")},
        {"confidence": float("inf")},
        {"confidence": 1.1},
        {"summary": "x" * 2049},
        {"evidence": ["x"] * 17},
        {"evidence": ["x" * 513]},
        {"project": "x" * 129},
    ],
)
def test_evaluation_results_are_bounded(changes):
    with pytest.raises(ValidationError):
        EvaluationResult.model_validate({"outcome": "solved", **changes})
