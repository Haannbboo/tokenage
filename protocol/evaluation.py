"""Client-initiated evaluation reports. Identity comes from the device token."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

EvaluatorType = Literal["claude", "codex", "opencode"]
EvaluationOutcome = Literal["solved", "partial", "failed", "stuck", "no_op", "unknown"]
Evidence = Annotated[str, Field(max_length=512)]


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: EvaluationOutcome
    confidence: float | None = Field(
        default=None, ge=0, le=1, allow_inf_nan=False, strict=True
    )
    task_title: str | None = Field(default=None, max_length=256)
    task_title_zh: str | None = Field(default=None, max_length=256)
    summary: str | None = Field(default=None, max_length=2048)
    evidence: list[Evidence] = Field(default_factory=list, max_length=16)
    failure_reason: str | None = Field(default=None, max_length=1024)
    project: str | None = Field(default=None, max_length=128)


class EvaluationStartedReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=256)
    client_source: str = Field(min_length=1, max_length=64)
    evaluator_type: EvaluatorType
    started_at: AwareDatetime


class EvaluationCompletedReport(EvaluationStartedReport):
    finished_at: AwareDatetime
    status: Literal["succeeded", "failed"] = "succeeded"
    result: EvaluationResult | None = None
    error: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def validate_completion(self):
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if self.status == "succeeded" and self.result is None:
            raise ValueError("successful evaluations require a result")
        if self.status == "failed" and self.result is not None:
            raise ValueError("failed evaluations cannot contain a result")
        return self
