"""Local transcript-backed session evaluation."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from .database import (
    VALID_OUTCOMES,
    create_session_evaluation_job,
    find_active_session_evaluation_job,
    get_engine,
    get_evaluation_job,
    get_session_record,
    mark_evaluation_job_failed,
    mark_evaluation_job_running,
    mark_evaluation_job_succeeded,
    promote_evaluation_job_to_manual,
    update_queued_evaluation_job_evaluator,
)

EVALUATION_TIMEOUT_SECONDS = 5 * 60
EVALUATION_LOG_PREVIEW_CHARS = 500

EVALUATOR_AGENT_CATALOG = {
    "codex": {"label": "Codex", "command": "codex"},
    "claude": {"label": "Claude Code", "command": "claude"},
}
VALID_EVALUATOR_AGENTS = set(EVALUATOR_AGENT_CATALOG)

logger = logging.getLogger(__name__)


def normalize_evaluator_type(evaluator_type: str) -> str:
    return str(evaluator_type).lower().strip()


def is_evaluator_type_available(evaluator_type: str) -> bool:
    evaluator_type = normalize_evaluator_type(evaluator_type)
    metadata = EVALUATOR_AGENT_CATALOG.get(evaluator_type)
    if metadata is None:
        return False
    return shutil.which(metadata["command"]) is not None


def list_evaluator_agents() -> list[dict[str, Any]]:
    return [
        {
            "id": evaluator_type,
            "label": metadata["label"],
            "command": metadata["command"],
            "available": shutil.which(metadata["command"]) is not None,
        }
        for evaluator_type, metadata in EVALUATOR_AGENT_CATALOG.items()
    ]


def require_available_evaluator_type(evaluator_type: str) -> str:
    evaluator_type = normalize_evaluator_type(evaluator_type)
    if evaluator_type not in VALID_EVALUATOR_AGENTS:
        raise ValueError(f"Unsupported evaluator agent: {evaluator_type}")
    if not is_evaluator_type_available(evaluator_type):
        raise ValueError(f"Evaluator not available: {evaluator_type}")
    return evaluator_type


EVALUATION_PROMPT = """You are evaluating a local agent session transcript for task outcome.

Use only the provided transcript. Do not use tools, run commands, inspect files, modify files, or continue the task.

Ignore project memory or recalled-history blocks such as <claude-mem-context>, Memory Context, observations, and prior session/task summaries. They are background context, not the task being evaluated. If the only apparent task is from one of those memory/history blocks, return outcome "no_op".

Return ONLY valid JSON matching:
{
  "task_title": string | null,
  "task_title_zh": string | null,
  "summary": string | null,
  "outcome": "solved" | "partial" | "failed" | "stuck" | "no_op" | "unknown",
  "confidence": number | null,
  "evidence": string[],
  "failure_reason": string | null,
  "project": string | null
}

Outcome definitions:
- solved: user's requested task appears completed and verified.
- partial: meaningful progress but incomplete or uncertain.
- failed: task attempted but result is wrong or broken.
- stuck: agent could not make progress or looped.
- no_op: session did not represent a substantive coding or engineering task. This includes:
  * trivial greetings or small talk ("hello", "hi", "test", "thanks")
  * sessions with only memory/context blocks and no real user request
  * sessions that ended immediately after the first message with no agent work
  * sessions where the user asked a simple question that required no code changes
  * interrupted sessions with no meaningful work completed
- unknown: the transcript itself is corrupted, missing, or unreadable — NOT for short or trivial sessions.

When in doubt between "no_op" and a substantive outcome, prefer "no_op" for sessions with fewer than 3 meaningful exchanges.

For substantive LLM-evaluated user tasks, task_title is a concise English title and task_title_zh is a concise Chinese title. Each title must be 2-6 words, preserve the same meaning, and avoid markdown or punctuation. If there is no substantive task, set both titles to null.

Keep evidence short. Do not include markdown.

For "project": infer the project or repository name from file paths, repo names, directory names, or project-specific terms in the conversation. Return the short name (e.g., "tokenage", "cw-tracker"), not a full path. If you cannot determine the project, return null.
"""


@dataclass(frozen=True)
class AgentInvocation:
    command: list[str]
    stdin: str | None = None
    env: dict[str, str] | None = None


class TranscriptLoadError(ValueError):
    """Raised when a local agent transcript cannot be read."""


@dataclass(frozen=True)
class LocalSessionTranscriptIndex:
    codex_path_names: tuple[str, ...] = ()
    claude_session_ids: frozenset[str] = frozenset()
    opencode_session_ids: frozenset[str] = frozenset()
    kilo_session_ids: frozenset[str] = frozenset()


def _normalize_client_source(client_source: str | None) -> str:
    normalized = (client_source or "").strip().lower()
    if normalized in {"codex"}:
        return "codex"
    if normalized in {"claude", "claude-code"}:
        return "claude"
    if normalized in {"gemini", "gemini-cli"}:
        return "gemini"
    if normalized in {"opencode"}:
        return "opencode"
    if normalized in {"kilo"}:
        return "kilo"
    raise ValueError(f"Unsupported session source: {client_source or 'unknown'}")


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                yield json.loads(stripped)
            except json.JSONDecodeError:
                continue


def _extract_content(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if not isinstance(value, list):
        return None

    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in {"text", "input_text", "output_text"}:
            text = item.get("text")  # type: ignore[assignment]
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        elif item_type == "tool_use":
            name = item.get("name", "?")
            inp = item.get("input", {})
            if not isinstance(inp, dict):
                parts.append(f"[tool:{name}]")
            elif name == "Read":
                parts.append(f"[tool:Read] {inp.get('file_path', '?')}")
            elif name == "Write":
                parts.append(f"[tool:Write] {inp.get('file_path', '?')}")
            elif name == "Edit":
                parts.append(f"[tool:Edit] {inp.get('file_path', '?')}")
            elif name == "Bash":
                cmd = _sanitize_evaluator_log_text(str(inp.get("command", "?")))
                parts.append(f"[tool:Bash] {cmd[:150]}")
            elif name == "WebSearch":
                query = _sanitize_evaluator_log_text(str(inp.get("query", "?")))
                parts.append(f"[tool:WebSearch] {query[:150]}")
            else:
                safe = _sanitize_evaluator_log_text(str(inp))
                parts.append(f"[tool:{name}] {safe[:100]}")
    if not parts:
        return None
    return "\n".join(parts)


def _format_transcript_turns(
    *,
    client_source: str,
    session_id: str,
    turns: list[tuple[str, str]],
) -> str:
    if not turns:
        raise TranscriptLoadError(
            f"No user/assistant transcript text found: {session_id}"
        )

    lines = [
        f"Session source: {client_source}",
        f"Session id: {session_id}",
        "Transcript: user/assistant text with tool call summaries (name + target). Tool outputs omitted.",
        "",
    ]
    for role, text in turns:
        lines.append(f"{role.upper()}:")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip()


def _find_codex_session_path_by_name(session_id: str) -> Path | None:
    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.exists():
        return None

    for path in sessions_dir.rglob(f"*{session_id}*.jsonl"):
        return path
    return None


def _find_codex_session_path(session_id: str) -> Path | None:
    by_name = _find_codex_session_path_by_name(session_id)
    if by_name is not None:
        return by_name

    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.exists():
        return None

    for path in sessions_dir.rglob("*.jsonl"):
        for record in _iter_jsonl(path):
            payload = record.get("payload") if isinstance(record, dict) else None
            if isinstance(payload, dict) and payload.get("id") == session_id:
                return path
    return None


def _load_codex_transcript(session_id: str, client_source: str) -> str:
    path = _find_codex_session_path(session_id)
    if path is None:
        raise TranscriptLoadError(f"Codex transcript not found: {session_id}")

    turns: list[tuple[str, str]] = []
    for record in _iter_jsonl(path):
        if not isinstance(record, dict) or record.get("type") != "response_item":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "message":
            continue
        role = payload.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = _extract_content(payload.get("content"))
        if text:
            turns.append((role, text))
    return _format_transcript_turns(
        client_source=client_source, session_id=session_id, turns=turns
    )


def _find_claude_session_path(session_id: str) -> Path | None:
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None
    for path in projects_dir.rglob(f"{session_id}.jsonl"):
        return path
    return None


def _load_claude_transcript(session_id: str, client_source: str) -> str:
    path = _find_claude_session_path(session_id)
    if path is None:
        raise TranscriptLoadError(f"Claude transcript not found: {session_id}")

    turns: list[tuple[str, str]] = []
    # Claude Code emits the same message ID as multiple JSONL records
    # (thinking, tool_use, text). Accumulate content blocks per message ID.
    pending_id: str | None = None
    pending_role: str = ""
    pending_content: list[Any] = []
    for record in _iter_jsonl(path):
        if not isinstance(record, dict) or record.get("type") not in {
            "user",
            "assistant",
        }:
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role") or record.get("type")
        if role not in {"user", "assistant"}:
            continue
        message_id = message.get("id") or record.get("uuid")
        # Flush when message ID changes
        if message_id != pending_id and pending_id is not None:
            text = _extract_content(pending_content)
            if text:
                turns.append((pending_role, text))
            pending_content = []
        pending_id = message_id
        pending_role = role
        content = message.get("content")
        if isinstance(content, list):
            pending_content.extend(content)
        elif isinstance(content, str) and content.strip():
            pending_content.append({"type": "text", "text": content})
    # Flush last message
    if pending_id is not None:
        text = _extract_content(pending_content)
        if text:
            turns.append((pending_role, text))
    return _format_transcript_turns(
        client_source=client_source, session_id=session_id, turns=turns
    )


def _opencode_db_path() -> Path:
    """Return OpenCode's local SQLite transcript database path."""
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        return Path(data_home) / "opencode" / "opencode.db"
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def _connect_opencode_db() -> sqlite3.Connection:
    """Open OpenCode's transcript database read-only for local evaluation."""
    db_path = _opencode_db_path()
    if not db_path.exists():
        raise TranscriptLoadError("OpenCode transcript database not found")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)


def _kilo_db_path() -> Path:
    """Return Kilo's local SQLite transcript database path."""
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        return Path(data_home) / "kilo" / "kilo.db"
    return Path.home() / ".local" / "share" / "kilo" / "kilo.db"


def _connect_kilo_db() -> sqlite3.Connection:
    """Open Kilo's transcript database read-only for local evaluation."""
    db_path = _kilo_db_path()
    if not db_path.exists():
        raise TranscriptLoadError("Kilo transcript database not found")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)


def _extract_opencode_part_text(value: Any) -> str | None:
    """Extract user-visible text from one OpenCode message part."""
    if not isinstance(value, dict):
        return None
    if value.get("type") != "text":
        return None
    if value.get("synthetic") or value.get("ignored"):
        return None
    return _extract_content(value.get("text"))


def _load_sqlite_transcript(
    session_id: str,
    client_source: str,
    connect_db: Callable[[], sqlite3.Connection],
    error_label: str,
) -> str:
    """Load user/assistant text turns from a SQLite transcript DB (OpenCode/Kilo)."""
    try:
        with closing(connect_db()) as connection:
            rows = connection.execute(
                """
                SELECT m.id, m.data, p.data
                FROM message m
                LEFT JOIN part p ON p.message_id = m.id
                WHERE m.session_id = ?
                ORDER BY m.time_created, m.id, p.time_created, p.id
                """,
                (session_id,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise TranscriptLoadError(
            f"{error_label} transcript not found: {session_id}"
        ) from exc

    turns: list[tuple[str, str]] = []
    current_message_id: str | None = None
    current_role: str | None = None
    current_parts: list[str] = []

    def flush_current() -> None:
        if current_role in {"user", "assistant"} and current_parts:
            turns.append((current_role, "\n".join(current_parts)))

    for message_id, message_data, part_data in rows:
        if message_id != current_message_id:
            flush_current()
            current_message_id = message_id
            current_parts = []
            current_role = None
            try:
                message_payload = json.loads(message_data)
            except (TypeError, json.JSONDecodeError):
                message_payload = {}
            if isinstance(message_payload, dict):
                role = message_payload.get("role")
                current_role = role if role in {"user", "assistant"} else None

        if current_role not in {"user", "assistant"} or part_data is None:
            continue
        try:
            part_payload = json.loads(part_data)
        except (TypeError, json.JSONDecodeError):
            continue
        text = _extract_opencode_part_text(part_payload)
        if text:
            current_parts.append(text)

    flush_current()
    return _format_transcript_turns(
        client_source=client_source, session_id=session_id, turns=turns
    )


def _load_opencode_transcript(session_id: str, client_source: str) -> str:
    """Load user and assistant text turns for an OpenCode session."""
    return _load_sqlite_transcript(
        session_id, client_source, _connect_opencode_db, "OpenCode"
    )


def _load_kilo_transcript(session_id: str, client_source: str) -> str:
    """Load user and assistant text turns for a Kilo session."""
    return _load_sqlite_transcript(session_id, client_source, _connect_kilo_db, "Kilo")


def _list_sqlite_session_ids_with_text(
    connect_db: Callable[[], sqlite3.Connection],
) -> frozenset[str]:
    """Return session IDs from a SQLite transcript DB that have at least one text part."""
    try:
        with closing(connect_db()) as connection:
            rows = connection.execute(
                """
                SELECT m.session_id, m.data, p.data
                FROM message m
                JOIN part p ON p.message_id = m.id
                WHERE m.data LIKE '%role%'
                  AND p.data LIKE '%text%'
                """
            ).fetchall()
    except (TranscriptLoadError, sqlite3.Error):
        return frozenset()

    session_ids: set[str] = set()
    for session_id, message_data, part_data in rows:
        try:
            message_payload = json.loads(message_data)
            part_payload = json.loads(part_data)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(message_payload, dict):
            continue
        if message_payload.get("role") not in {"user", "assistant"}:
            continue
        if _extract_opencode_part_text(part_payload) and isinstance(session_id, str):
            session_ids.add(session_id)
    return frozenset(session_ids)


def _list_kilo_session_ids_with_text() -> frozenset[str]:
    return _list_sqlite_session_ids_with_text(_connect_kilo_db)


def _list_opencode_session_ids_with_text() -> frozenset[str]:
    return _list_sqlite_session_ids_with_text(_connect_opencode_db)


def _sqlite_session_has_turn_text(
    session_id: str, connect_db: Callable[[], sqlite3.Connection]
) -> bool:
    """Check whether one SQLite transcript session has user/assistant text."""
    try:
        with closing(connect_db()) as connection:
            rows = connection.execute(
                """
                SELECT m.data, p.data
                FROM message m
                JOIN part p ON p.message_id = m.id
                WHERE m.session_id = ?
                  AND m.data LIKE '%role%'
                  AND p.data LIKE '%text%'
                """,
                (session_id,),
            ).fetchall()
    except (TranscriptLoadError, sqlite3.Error):
        return False
    for message_data, part_data in rows:
        try:
            message_payload = json.loads(message_data)
            part_payload = json.loads(part_data)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(message_payload, dict):
            continue
        if message_payload.get("role") not in {"user", "assistant"}:
            continue
        if _extract_opencode_part_text(part_payload):
            return True
    return False


def _opencode_session_has_turn_text(session_id: str) -> bool:
    return _sqlite_session_has_turn_text(session_id, _connect_opencode_db)


def _kilo_session_has_turn_text(session_id: str) -> bool:
    return _sqlite_session_has_turn_text(session_id, _connect_kilo_db)


def build_local_session_transcript_index() -> LocalSessionTranscriptIndex:
    codex_dir = Path.home() / ".codex" / "sessions"
    codex_path_names = (
        tuple(path.name for path in codex_dir.rglob("*.jsonl"))
        if codex_dir.exists()
        else ()
    )

    claude_dir = Path.home() / ".claude" / "projects"
    claude_session_ids = (
        frozenset(path.stem for path in claude_dir.rglob("*.jsonl"))
        if claude_dir.exists()
        else frozenset()
    )

    return LocalSessionTranscriptIndex(
        codex_path_names=codex_path_names,
        claude_session_ids=claude_session_ids,
        opencode_session_ids=_list_opencode_session_ids_with_text(),
        kilo_session_ids=_list_kilo_session_ids_with_text(),
    )


def has_local_session_transcript(
    client_source: str | None,
    session_id: str,
    *,
    local_index: LocalSessionTranscriptIndex | None = None,
) -> bool:
    try:
        agent = _normalize_client_source(client_source)
    except ValueError:
        return False

    if local_index is not None:
        if agent == "codex":
            return any(
                session_id in path_name for path_name in local_index.codex_path_names
            )
        if agent == "claude":
            return session_id in local_index.claude_session_ids
        if agent == "kilo":
            return session_id in local_index.kilo_session_ids
        return agent == "opencode" and session_id in local_index.opencode_session_ids

    if agent == "codex":
        return _find_codex_session_path_by_name(session_id) is not None
    if agent == "claude":
        return _find_claude_session_path(session_id) is not None
    if agent == "kilo":
        return _kilo_session_has_turn_text(session_id)
    if agent == "opencode":
        return _opencode_session_has_turn_text(session_id)
    return False


def load_session_transcript(client_source: str | None, session_id: str) -> str:
    agent = _normalize_client_source(client_source)
    if agent == "codex":
        return _load_codex_transcript(session_id, client_source or agent)
    if agent == "claude":
        return _load_claude_transcript(session_id, client_source or agent)
    if agent == "kilo":
        return _load_kilo_transcript(session_id, client_source or agent)
    if agent == "opencode":
        return _load_opencode_transcript(session_id, client_source or agent)
    raise TranscriptLoadError(f"No local transcript source for agent: {agent}")


def _build_codex_evaluator_invocation(prompt: str) -> AgentInvocation:
    return AgentInvocation(
        command=[
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
        ],
        stdin=prompt,
        env={
            "OTEL_SDK_DISABLED": "true",
            "OTEL_LOGS_EXPORTER": "none",
            "OTEL_TRACES_EXPORTER": "none",
            "OTEL_METRICS_EXPORTER": "none",
        },
    )


def _build_claude_evaluator_invocation(prompt: str) -> AgentInvocation:
    return AgentInvocation(
        command=[
            "claude",
            "--print",
            "--no-session-persistence",
        ],
        stdin=prompt,
        env={
            "OTEL_SDK_DISABLED": "true",
            "OTEL_LOGS_EXPORTER": "none",
            "OTEL_TRACES_EXPORTER": "none",
            "OTEL_METRICS_EXPORTER": "none",
            "CLAUDE_CODE_NO_AUTO_GIT": "1",
            "CLAUDE_CODE_NO_BROWSER": "1",
            "CLAUDE_CODE_HEADLESS": "1",
        },
    )


def build_evaluator_invocation(
    transcript: str,
    evaluator: str = "codex",
) -> AgentInvocation:
    prompt = f"{EVALUATION_PROMPT}\n\n<session_transcript>\n{transcript}\n</session_transcript>"
    if evaluator not in VALID_EVALUATOR_AGENTS:
        raise ValueError(f"Unsupported evaluator agent: {evaluator}")
    builders = {
        "codex": _build_codex_evaluator_invocation,
        "claude": _build_claude_evaluator_invocation,
    }
    return builders[evaluator](prompt)


def is_claude_mem_observer_session(session_id: str) -> bool:
    claude_projects_dir = Path.home() / ".claude" / "projects"
    if not claude_projects_dir.exists():
        return False

    session_filename = f"{session_id}.jsonl"
    for session_path in claude_projects_dir.glob(f"*/{session_filename}"):
        if "claude-mem-observer-sessions" in session_path.as_posix():
            return True
    return False


def _no_op_claude_mem_evaluation() -> dict[str, Any]:
    return {
        "outcome": "no_op",
        "confidence": 1.0,
        "task_title": None,
        "task_title_zh": None,
        "summary": "Session was generated by Claude-Mem observer, not a user task.",
        "evidence": ["Claude session file is under the Claude-Mem observer project"],
        "failure_reason": None,
        "project": None,
    }


def _no_op_evaluator_session_evaluation() -> dict[str, Any]:
    return {
        "outcome": "no_op",
        "confidence": 1.0,
        "task_title": None,
        "task_title_zh": None,
        "summary": "Session was generated by tokenage's evaluator, not a user task.",
        "evidence": ["Session matched tokenage evaluator telemetry"],
        "failure_reason": None,
        "project": None,
    }


def _unknown_transcript_unavailable_evaluation(error: Exception) -> dict[str, Any]:
    return {
        "outcome": "unknown",
        "confidence": 0.0,
        "task_title": None,
        "task_title_zh": None,
        "summary": "Session transcript could not be loaded from local agent storage.",
        "evidence": [str(error)],
        "failure_reason": None,
        "project": None,
    }


def _sanitize_evaluator_log_text(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    text = re.sub(
        r"(?i)\b([A-Z0-9_]*(?:api[_-]?key|token|secret|password)[A-Z0-9_]*)"
        r"\s*=\s*\S+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(
        r'(?i)("?[A-Z0-9_ -]*(?:api[_-]?key|token|secret|password|authorization)'
        r'[A-Z0-9_ -]*"?\s*:\s*")[^"]*(")',
        r"\1[redacted]\2",
        text,
    )
    text = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)\S+",
        r"\1[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)(--(?:api-?key|token|secret|password)\s+)\S+",
        r"\1[redacted]",
        text,
    )
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "sk-[redacted]", text)
    text = re.sub(r"/(?:Users|home|var/folders)/[^\s\"']+", "[path]", text)
    text = re.sub(r"[A-Za-z]:\\Users\\[^\s\"']+", "[path]", text)
    if len(text) > EVALUATION_LOG_PREVIEW_CHARS:
        text = f"{text[:EVALUATION_LOG_PREVIEW_CHARS]}..."
    return text


def _decode_evaluation_payload(output: str) -> Any:
    stripped = output.strip()
    decoder = json.JSONDecoder()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    fallback: Any = None
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if fallback is None:
            fallback = candidate
        if isinstance(candidate, dict) and "outcome" in candidate:
            return candidate

    if fallback is not None:
        return fallback
    raise ValueError("Evaluation agent returned invalid JSON")


def _first_user_turn(transcript: str) -> str | None:
    marker = "\nUSER:\n"
    start = transcript.find(marker)
    if start == -1:
        return None
    start += len(marker)
    next_turns = [
        index
        for index in (
            transcript.find("\nASSISTANT:\n", start),
            transcript.find("\nUSER:\n", start),
        )
        if index != -1
    ]
    end = min(next_turns) if next_turns else len(transcript)
    return transcript[start:end].strip()


def _transcript_is_evaluator_session(transcript: str) -> bool:
    first_user = _first_user_turn(transcript)
    if first_user is None:
        return False
    if not first_user.startswith("You are evaluating"):
        return False
    return (
        "Return ONLY valid JSON matching" in first_user
        and '"outcome"' in first_user
        and "failure_reason" in first_user
    )


def is_local_evaluator_session(client_source: str | None, session_id: str) -> bool:
    try:
        transcript = load_session_transcript(client_source, session_id)
    except (TranscriptLoadError, ValueError):
        return False
    return _transcript_is_evaluator_session(transcript)


def parse_evaluation_output(output: str) -> dict[str, Any]:
    try:
        payload = _decode_evaluation_payload(output)
    except ValueError as exc:
        raise ValueError("Evaluation agent returned invalid JSON") from exc

    if not isinstance(payload, dict):
        raise ValueError("Evaluation agent returned non-object JSON")

    outcome = payload.get("outcome")
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"Invalid outcome: {outcome}")

    evidence = payload.get("evidence", [])
    if not isinstance(evidence, list) or not all(
        isinstance(item, str) for item in evidence
    ):
        raise ValueError("Evaluation evidence must be a list of strings")

    confidence = payload.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, int | float)
        or not 0 <= confidence <= 1
    ):
        raise ValueError("Evaluation confidence must be between 0 and 1 or null")

    has_substantive_task = outcome not in {"no_op", "unknown"}

    return {
        "outcome": outcome,
        "confidence": float(confidence) if confidence is not None else None,
        "task_title": payload.get("task_title") if has_substantive_task else None,
        "task_title_zh": payload.get("task_title_zh") if has_substantive_task else None,
        "summary": payload.get("summary"),
        "evidence": evidence,
        "failure_reason": payload.get("failure_reason"),
        "project": payload.get("project") if has_substantive_task else None,
    }


def _upsert_llm_evaluation(
    *,
    session_id: str,
    evaluation: dict[str, Any],
    db_path: str | None,
    user_id: str,
    skip_if_manual: bool = False,
) -> None:
    from .database import upsert_session_evaluation

    upsert_session_evaluation(
        session_id=session_id,
        outcome=evaluation["outcome"],
        source="llm",
        confidence=evaluation["confidence"],
        task_title=evaluation.get("task_title"),
        task_title_zh=evaluation.get("task_title_zh"),
        summary=evaluation.get("summary"),
        evidence=evaluation.get("evidence"),
        failure_reason=evaluation.get("failure_reason"),
        project=evaluation.get("project"),
        user_id=user_id,
        skip_if_manual=skip_if_manual,
        db_path=db_path,
    )


def _session_has_manual_evaluation(
    session_id: str,
    *,
    db_path: str | None = None,
    user_id: str,
) -> bool:
    with Session(get_engine(db_path)) as session:
        record = get_session_record(session, session_id, user_id=user_id)
        return bool(record is not None and record.source == "manual")


def mark_evaluator_session_no_op(
    session_id: str,
    *,
    db_path: str | None = None,
    user_id: str,
) -> bool:
    if _session_has_manual_evaluation(session_id, db_path=db_path, user_id=user_id):
        return False
    _upsert_llm_evaluation(
        session_id=session_id,
        evaluation=_no_op_evaluator_session_evaluation(),
        db_path=db_path,
        user_id=user_id,
    )
    return True


def summarize_session_with_llm(
    session_id: str,
    *,
    db_path: str | None = None,
    update: bool = True,
    evaluator: str = "codex",
    user_id: str,
) -> dict[str, Any]:
    """Evaluate and summarize a session synchronously using the central evaluator."""
    with Session(get_engine(db_path)) as session:
        record = get_session_record(session, session_id, user_id=user_id)
        if not record:
            raise ValueError(f"Session not found: {session_id}")
        session.expunge(record)

    client_source = record.client_source
    try:
        normalized_source = _normalize_client_source(client_source)
    except ValueError as exc:
        evaluation = _unknown_transcript_unavailable_evaluation(exc)
        if update:
            _upsert_llm_evaluation(
                session_id=record.session_id,
                evaluation=evaluation,
                db_path=db_path,
                user_id=user_id,
                skip_if_manual=True,
            )
        return evaluation

    if normalized_source == "claude" and is_claude_mem_observer_session(
        record.session_id
    ):
        evaluation = _no_op_claude_mem_evaluation()
        if update:
            _upsert_llm_evaluation(
                session_id=record.session_id,
                evaluation=evaluation,
                db_path=db_path,
                user_id=user_id,
                skip_if_manual=True,
            )
        return evaluation

    try:
        transcript = load_session_transcript(client_source, record.session_id)
    except (TranscriptLoadError, ValueError) as exc:
        evaluation = _unknown_transcript_unavailable_evaluation(exc)
        if update:
            _upsert_llm_evaluation(
                session_id=record.session_id,
                evaluation=evaluation,
                db_path=db_path,
                user_id=user_id,
                skip_if_manual=True,
            )
        return evaluation

    if _transcript_is_evaluator_session(transcript):
        evaluation = _no_op_evaluator_session_evaluation()
        if update:
            _upsert_llm_evaluation(
                session_id=record.session_id,
                evaluation=evaluation,
                db_path=db_path,
                user_id=user_id,
                skip_if_manual=True,
            )
        return evaluation

    invocation = build_evaluator_invocation(transcript, evaluator=evaluator)
    completed = subprocess.run(
        args=invocation.command,
        input=invocation.stdin,
        text=True,
        capture_output=True,
        timeout=EVALUATION_TIMEOUT_SECONDS,
        env={**os.environ, **(invocation.env or {})},
    )
    if completed.returncode != 0:
        logger.warning(
            "Evaluation agent subprocess failed: returncode=%s stdout=%r stderr=%r",
            completed.returncode,
            _sanitize_evaluator_log_text(completed.stdout),
            _sanitize_evaluator_log_text(completed.stderr),
        )
        raise ValueError("Evaluation agent failed")

    evaluation = parse_evaluation_output(completed.stdout)
    if update:
        _upsert_llm_evaluation(
            session_id=record.session_id,
            evaluation=evaluation,
            db_path=db_path,
            user_id=user_id,
            skip_if_manual=True,
        )
    return evaluation


def run_session_evaluation_job(
    job_id: str,
    *,
    db_path: str | None = None,
    evaluator: str | None = None,
) -> None:
    if not mark_evaluation_job_running(job_id, db_path=db_path):
        return
    execute_session_evaluation_job(job_id, db_path=db_path, evaluator=evaluator)


def execute_session_evaluation_job(
    job_id: str,
    *,
    db_path: str | None = None,
    evaluator: str | None = None,
) -> None:
    try:
        job = get_evaluation_job(job_id, db_path=db_path)
        if not job:
            raise ValueError(f"Evaluation job not found: {job_id}")
        evaluator_type = evaluator or job.get("evaluator_type") or "codex"

        if _session_has_manual_evaluation(
            job["session_id"], db_path=db_path, user_id=job["user_id"]
        ):
            mark_evaluation_job_succeeded(job_id, db_path=db_path)
            return

        summarize_session_with_llm(
            job["session_id"],
            db_path=db_path,
            update=True,
            evaluator=evaluator_type,
            user_id=job["user_id"],
        )
        mark_evaluation_job_succeeded(job_id, db_path=db_path)
    except Exception as exc:
        mark_evaluation_job_failed(job_id, str(exc), db_path=db_path)


def start_session_evaluation_job(
    session_id: str,
    *,
    trigger: str = "manual",
    evaluator_type: str = "codex",
    user_id: str,
    db_path: str | None = None,
) -> dict[str, Any]:
    evaluator_type = normalize_evaluator_type(evaluator_type)
    if evaluator_type not in VALID_EVALUATOR_AGENTS:
        raise ValueError(f"Unsupported evaluator agent: {evaluator_type}")

    with Session(get_engine(db_path)) as session:
        record = get_session_record(session, session_id, user_id=user_id)
        if not record:
            raise ValueError(f"Session not found: {session_id}")
        if record.source == "manual":
            raise ValueError(f"Manual evaluation exists for session: {session_id}")
        client_source = record.client_source

    _normalize_client_source(client_source)

    active = find_active_session_evaluation_job(
        session_id=session_id,
        user_id=user_id,
        db_path=db_path,
    )
    if active is not None:
        if trigger == "manual" and active["trigger"] == "auto":
            promoted = promote_evaluation_job_to_manual(
                active["job_id"],
                evaluator_type=evaluator_type,
                user_id=user_id,
                db_path=db_path,
            )
            if promoted is not None:
                return promoted
        if active["status"] == "queued" and active["trigger"] == trigger:
            updated = update_queued_evaluation_job_evaluator(
                active["job_id"],
                evaluator_type=evaluator_type,
                user_id=user_id,
                db_path=db_path,
            )
            if updated is not None:
                return updated
        return active

    job = create_session_evaluation_job(
        session_id=session_id,
        client_source=client_source,
        user_id=user_id,
        trigger=trigger,
        evaluator_type=evaluator_type,
        db_path=db_path,
    )
    return job
