"""Metadata-only client events. Each event type has a closed wire schema."""

import unicodedata
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def sanitize_project(value: str) -> str:
    value = unicodedata.normalize("NFC", value.strip())
    if (
        not value
        or value in {".", ".."}
        or len(value) > 100
        or any(
            c in "/\\"
            or unicodedata.category(c).startswith("C")
            or unicodedata.category(c) in {"Zl", "Zp"}
            for c in value
        )
    ):
        raise ValueError("invalid project name")
    return value


ProjectName = Annotated[str, AfterValidator(sanitize_project)]
Agent = Literal["claude", "codex", "opencode", "kilo"]
SessionId = Annotated[
    str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.:-]+$")
]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionProjectData(ClosedModel):
    project: ProjectName


class SessionProjectEvent(ClosedModel):
    type: Literal["session.project"]
    agent: Agent
    session_id: SessionId
    ts: int = Field(ge=0)
    data: SessionProjectData


# Add future typed events to this union, never an unvalidated dict payload.
ClientEvent = Annotated[SessionProjectEvent, Field(discriminator="type")]


class EventBatch(ClosedModel):
    events: list[ClientEvent] = Field(min_length=1, max_length=100)


class SpoolReport(ClosedModel):
    schema_version: Literal[1]
    agent: Agent
    session_id: SessionId
    project: ProjectName
    ts: int = Field(ge=0)

    def event(self) -> SessionProjectEvent:
        return SessionProjectEvent(
            type="session.project",
            agent=self.agent,
            session_id=self.session_id,
            ts=self.ts,
            data=SessionProjectData(project=self.project),
        )
