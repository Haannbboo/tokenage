"""Client-driven local session evaluation.

The client owns automatic evaluation of this machine's local agent sessions.
It discovers sessions from local transcripts (Claude, Codex, OpenCode, Kilo),
runs a headless evaluator CLI on the quiet ones, and reports the result to the
server it signed in to. The server derives user and device from the CLI token;
the client never reads server state or the server's database.

Local state is one latest-result JSON file per session under
``$TOKENAGE_HOME/evaluations/``. There is no queue and no leases: a pass
re-uploads a bounded batch of unsubmitted completions and evaluates one quiet
session.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import yaml

from client.auth import _write_private_text, load_credentials, save_private_object
from client.paths import client_config_path, read_object, tracker_home
from protocol.evaluation import (
    EvaluationCompletedReport,
    EvaluationResult,
    EvaluationStartedReport,
)

EVALUATION_TIMEOUT_SECONDS = 5 * 60
EVALUATION_LOG_PREVIEW_CHARS = 500
EVALUATION_HTTP_TIMEOUT_SECONDS = 10.0
PASS_INTERVAL_SECONDS = 60.0
MAX_PENDING_UPLOADS_PER_PASS = 10
PERMANENT_SUBMISSION_ERRORS = {400, 404, 409, 422}

# Default evaluator choice when client.yaml leaves it unset.
EVALUATOR_DETECTION_ORDER = ("claude", "codex", "opencode")
VALID_EVALUATOR_AGENTS = set(EVALUATOR_DETECTION_ORDER)
VALID_OUTCOMES = {"solved", "partial", "failed", "stuck", "no_op", "unknown"}

CODEX_SESSION_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)

logger = logging.getLogger("tokenage.client.evaluation")

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


# --------------------------------------------------------------- transcripts


def _normalize_client_source(client_source: str | None) -> str:
    normalized = (client_source or "").strip().lower()
    if normalized in {"codex"}:
        return "codex"
    if normalized in {"claude", "claude-code"}:
        return "claude"
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
            raw_text = item.get("text")
            if isinstance(raw_text, str) and raw_text.strip():
                parts.append(raw_text.strip())
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


def _find_codex_session_path(session_id: str) -> Path | None:
    sessions_dir = Path.home() / ".codex" / "sessions"
    by_name = next(sessions_dir.rglob(f"*{session_id}*.jsonl"), None)
    if by_name is not None:
        return by_name

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
    return next(projects_dir.rglob(f"{session_id}.jsonl"), None)


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


def _connect_sqlite_db(agent: str) -> sqlite3.Connection:
    """Open OpenCode/Kilo's transcript database read-only for local evaluation."""
    data_home = os.environ.get("XDG_DATA_HOME")
    root = Path(data_home) if data_home else Path.home() / ".local" / "share"
    db_path = root / agent / f"{agent}.db"
    if not db_path.exists():
        label = "OpenCode" if agent == "opencode" else "Kilo"
        raise TranscriptLoadError(f"{label} transcript database not found")
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
    agent: str,
) -> str:
    """Load user/assistant text turns from a SQLite transcript DB (OpenCode/Kilo)."""
    try:
        with closing(_connect_sqlite_db(agent)) as connection:
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
        error_label = "OpenCode" if agent == "opencode" else "Kilo"
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


def load_session_transcript(client_source: str | None, session_id: str) -> str:
    agent = _normalize_client_source(client_source)
    if agent == "codex":
        return _load_codex_transcript(session_id, client_source or agent)
    if agent == "claude":
        return _load_claude_transcript(session_id, client_source or agent)
    if agent in {"kilo", "opencode"}:
        return _load_sqlite_transcript(session_id, client_source or agent, agent)
    raise TranscriptLoadError(f"No local transcript source for agent: {agent}")


# ------------------------------------------------------------- invocations


def _build_codex_evaluator_invocation(prompt: str) -> AgentInvocation:
    return AgentInvocation(
        command=[
            "codex",
            "-c",
            'otel.exporter="none"',
            "-c",
            'otel.trace_exporter="none"',
            "-c",
            'otel.metrics_exporter="none"',
            "-c",
            "otel.log_user_prompt=false",
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
            "--tools",
            "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--settings",
            json.dumps(
                {
                    "disableAllHooks": True,
                    "env": {
                        "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
                        "OTEL_SDK_DISABLED": "true",
                        "OTEL_LOGS_EXPORTER": "none",
                        "OTEL_TRACES_EXPORTER": "none",
                        "OTEL_METRICS_EXPORTER": "none",
                    },
                }
            ),
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


def _build_opencode_evaluator_invocation(prompt: str) -> AgentInvocation:
    # ``run`` reads the prompt from stdin when it has no message arguments, and
    # ``--pure`` skips external plugins, so evaluator runs are not tracked.
    # Its own configured default provider/model applies.
    return AgentInvocation(
        command=["opencode", "run", "--pure"],
        stdin=prompt,
        env={
            "OTEL_SDK_DISABLED": "true",
            "OTEL_LOGS_EXPORTER": "none",
            "OTEL_TRACES_EXPORTER": "none",
            "OTEL_METRICS_EXPORTER": "none",
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
        },
    )


def build_evaluator_invocation(
    transcript: str,
    evaluator: str = "claude",
) -> AgentInvocation:
    prompt = f"{EVALUATION_PROMPT}\n\n<session_transcript>\n{transcript}\n</session_transcript>"
    if evaluator not in VALID_EVALUATOR_AGENTS:
        raise ValueError(f"Unsupported evaluator agent: {evaluator}")
    builders = {
        "codex": _build_codex_evaluator_invocation,
        "claude": _build_claude_evaluator_invocation,
        "opencode": _build_opencode_evaluator_invocation,
    }
    return builders[evaluator](prompt)


# ------------------------------------------------------- no-op detection


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
        "evidence": [_sanitize_evaluator_log_text(str(error))],
        "failure_reason": None,
        "project": None,
    }


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


# ----------------------------------------------------------- configuration


@dataclass(frozen=True)
class EvaluationConfig:
    auto_enabled: bool = True
    quiet_delay_seconds: int = 600
    evaluator: str | None = None


def _as_positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return default
    return value


def load_evaluation_config() -> EvaluationConfig:
    """Read the ``evaluation:`` section of $TOKENAGE_HOME/client.yaml."""
    path = client_config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return EvaluationConfig()
    except (OSError, UnicodeError):
        logger.warning("cannot read client.yaml; evaluation skipped")
        return EvaluationConfig(auto_enabled=False)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        logger.warning("invalid YAML in client.yaml; evaluation skipped")
        return EvaluationConfig(auto_enabled=False)
    if data is None:
        return EvaluationConfig()
    if not isinstance(data, dict):
        return EvaluationConfig(auto_enabled=False)
    raw = data.get("evaluation")
    if raw is None:
        return EvaluationConfig()
    if not isinstance(raw, dict):
        return EvaluationConfig(auto_enabled=False)
    evaluator = raw.get("evaluator")
    if evaluator is not None and not isinstance(evaluator, str):
        return EvaluationConfig(auto_enabled=False)
    if not isinstance(evaluator, str) or not evaluator.strip():
        evaluator = None
    else:
        evaluator = evaluator.strip().lower()
    auto_enabled = raw.get("auto_enabled", True)
    return EvaluationConfig(
        auto_enabled=auto_enabled if isinstance(auto_enabled, bool) else False,
        quiet_delay_seconds=_as_positive_int(raw.get("quiet_delay_seconds"), 600),
        evaluator=evaluator,
    )


def detect_evaluator_agent() -> str | None:
    """First installed evaluator CLI in the default priority order."""
    for name in EVALUATOR_DETECTION_ORDER:
        if shutil.which(name):
            return name
    return None


def resolve_evaluator_agent(configured: str | None) -> str | None:
    """The evaluator to run: the explicit one, or the first detected one."""
    if not configured:
        return detect_evaluator_agent()
    name = configured.strip().lower()
    if name not in VALID_EVALUATOR_AGENTS:
        logger.warning("unsupported evaluation.evaluator %r; evaluation skipped", name)
        return None
    if not shutil.which(name):
        logger.info("evaluator %r is not installed; evaluation skipped", name)
        return None
    return name


def ensure_evaluation_configured(available: list[str] | None = None) -> str | None:
    """Initialize ``evaluation.evaluator`` in client.yaml from detected agents.

    Called by setup and login. Preserves existing settings, including an
    explicit evaluator and the proxy section. When no evaluator
    CLI is installed, nothing is written and detection repeats at service
    startup. Returns the evaluator that is (now) configured, if any.
    """
    evaluator = (
        detect_evaluator_agent()
        if available is None
        else next(
            (name for name in EVALUATOR_DETECTION_ORDER if name in available), None
        )
    )
    if evaluator is None:
        return None

    path = client_config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeError):
        logger.warning("cannot read %s; evaluator not recorded", path)
        return None

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        logger.warning("invalid YAML in client.yaml; evaluator not recorded")
        return None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return None
    raw = data.get("evaluation")
    if raw is None:
        raw = data["evaluation"] = {
            "auto_enabled": True,
            "quiet_delay_seconds": 600,
        }
    if not isinstance(raw, dict):
        return None
    if raw.get("evaluator"):
        return str(raw["evaluator"]).strip().lower()
    raw["evaluator"] = evaluator
    _write_private_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return evaluator


# ------------------------------------------------------- local discovery


@dataclass(frozen=True)
class LocalSessionCandidate:
    session_id: str
    client_source: str
    last_activity: float
    revision: str


def _jsonl_revision(path: Path) -> str:
    stat = path.stat()
    return f"jsonl:{stat.st_mtime_ns}:{stat.st_size}"


def _jsonl_paths(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.jsonl") if path.is_file()]


def _claude_candidates() -> list[LocalSessionCandidate]:
    candidates = []
    for path in _jsonl_paths(Path.home() / ".claude" / "projects"):
        if "subagents" in path.parts or path.stem.startswith("agent-"):
            continue
        try:
            revision = _jsonl_revision(path)
        except OSError:
            continue
        candidates.append(
            LocalSessionCandidate(
                session_id=path.stem,
                client_source="claude",
                last_activity=path.stat().st_mtime,
                revision=revision,
            )
        )
    return candidates


def _codex_candidates() -> list[LocalSessionCandidate]:
    candidates = []
    for path in _jsonl_paths(Path.home() / ".codex" / "sessions"):
        match = CODEX_SESSION_ID_RE.search(path.name)
        if match is None:
            continue
        try:
            revision = _jsonl_revision(path)
        except OSError:
            continue
        candidates.append(
            LocalSessionCandidate(
                session_id=match.group(0),
                client_source="codex",
                last_activity=path.stat().st_mtime,
                revision=revision,
            )
        )
    return candidates


def _sqlite_candidates(client_source: str) -> list[LocalSessionCandidate]:
    """Sessions with their newest message/part change, straight from their rows.

    The shared DB file's mtime says nothing about one session, so activity and
    revision come from MAX(time_updated) over that session's messages and parts.
    """
    try:
        with closing(_connect_sqlite_db(client_source)) as connection:
            message_rows = connection.execute(
                "SELECT session_id, MAX(time_updated) FROM message GROUP BY session_id"
            ).fetchall()
            part_rows = connection.execute(
                """
                SELECT m.session_id, MAX(p.time_updated)
                FROM part p
                JOIN message m ON p.message_id = m.id
                GROUP BY m.session_id
                """
            ).fetchall()
    except (TranscriptLoadError, sqlite3.Error):
        return []

    activity: dict[str, int] = {}
    for session_id, newest in [*message_rows, *part_rows]:
        if not isinstance(session_id, str) or not isinstance(newest, (int, float)):
            continue
        activity[session_id] = max(activity.get(session_id, 0), int(newest))

    return [
        LocalSessionCandidate(
            session_id=session_id,
            client_source=client_source,
            last_activity=newest / 1000 if newest > 10**11 else float(newest),
            revision=f"sqlite:{newest}",
        )
        for session_id, newest in activity.items()
    ]


def discover_local_sessions() -> list[LocalSessionCandidate]:
    """All local sessions, oldest last-local-activity first, tie session id."""
    candidates = (
        _claude_candidates()
        + _codex_candidates()
        + _sqlite_candidates("opencode")
        + _sqlite_candidates("kilo")
    )
    candidates.sort(
        key=lambda candidate: (candidate.last_activity, candidate.session_id)
    )
    return candidates


# --------------------------------------------------------- local results


def evaluations_dir() -> Path:
    return tracker_home() / "evaluations"


def safe_session_filename(session_id: str) -> str:
    """A deterministic file name with no path segments for any session id."""
    encoded = quote(session_id, safe="")
    if len(encoded) <= 120:
        return f"{encoded}.json"
    return f"{hashlib.sha256(session_id.encode('utf-8')).hexdigest()}.json"


def _result_path(session_id: str) -> Path:
    return evaluations_dir() / safe_session_filename(session_id)


def _read_result(session_id: str) -> dict[str, Any] | None:
    try:
        return read_object(_result_path(session_id))
    except ValueError:
        return None


def _write_result(record: dict[str, Any]) -> None:
    save_private_object(_result_path(str(record["session_id"])), record)


@dataclass(frozen=True)
class EvaluationIdentity:
    server_url: str
    user_id: str
    cli_token: str


def load_evaluation_identity() -> EvaluationIdentity | None:
    """The signed-in identity, or None when signed out or incomplete."""
    try:
        credentials = load_credentials() or {}
    except ValueError:
        return None
    server_url = credentials.get("server_url")
    user_id = credentials.get("user_id")
    cli_token = credentials.get("cli_token")
    if not isinstance(server_url, str) or not server_url:
        return None
    if not isinstance(cli_token, str) or not cli_token:
        return None
    if not isinstance(user_id, str) or not user_id:
        return None
    return EvaluationIdentity(
        server_url=server_url, user_id=user_id, cli_token=cli_token
    )


# ------------------------------------------------------------- evaluation


def _bounded_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Scrub and bound an evaluator result, then validate the wire schema."""
    evidence = [
        _sanitize_evaluator_log_text(item)[:512]
        for item in raw.get("evidence", [])
        if isinstance(item, str)
    ][:16]

    def text(field: str, limit: int) -> str | None:
        value = raw.get(field)
        if not isinstance(value, str):
            return None
        return _sanitize_evaluator_log_text(value)[:limit] or None

    sanitized = {
        "outcome": raw.get("outcome"),
        "confidence": raw.get("confidence"),
        "task_title": text("task_title", 256),
        "task_title_zh": text("task_title_zh", 256),
        "summary": text("summary", 2048),
        "evidence": evidence,
        "failure_reason": text("failure_reason", 1024),
        "project": text("project", 128),
    }
    return EvaluationResult.model_validate(sanitized).model_dump(mode="json")


def _run_evaluator(
    candidate: LocalSessionCandidate, evaluator: str
) -> tuple[dict[str, Any] | None, str | None]:
    """(result, error): exactly one is set."""
    if candidate.client_source == "claude" and is_claude_mem_observer_session(
        candidate.session_id
    ):
        return _no_op_claude_mem_evaluation(), None

    try:
        transcript = load_session_transcript(
            candidate.client_source, candidate.session_id
        )
    except (TranscriptLoadError, ValueError) as exc:
        return _unknown_transcript_unavailable_evaluation(exc), None

    if _transcript_is_evaluator_session(transcript):
        return _no_op_evaluator_session_evaluation(), None

    invocation = build_evaluator_invocation(transcript, evaluator=evaluator)
    try:
        completed = subprocess.run(
            args=invocation.command,
            input=invocation.stdin,
            text=True,
            capture_output=True,
            timeout=EVALUATION_TIMEOUT_SECONDS,
            env={**os.environ, **(invocation.env or {})},
        )
    except subprocess.TimeoutExpired:
        return None, "Evaluation agent timed out"
    except OSError:
        logger.warning("evaluation agent could not start")
        return None, "Evaluation agent could not start"

    if completed.returncode != 0:
        logger.warning(
            "Evaluation agent subprocess failed: returncode=%s",
            completed.returncode,
        )
        return None, "Evaluation agent failed"

    try:
        return _bounded_result(parse_evaluation_output(completed.stdout)), None
    except ValueError:
        logger.warning("invalid evaluation output")
        return None, "Evaluation agent returned invalid JSON"


def _post_report(
    identity: EvaluationIdentity, suffix: str, report: dict[str, Any]
) -> int | None:
    try:
        response = httpx.post(
            f"{identity.server_url}/evaluation-jobs/{suffix}",
            json=report,
            headers={"Authorization": f"Bearer {identity.cli_token}"},
            timeout=EVALUATION_HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "evaluation %s report rejected: HTTP %s", suffix, exc.response.status_code
        )
        return exc.response.status_code
    except Exception:
        logger.warning("evaluation %s report upload failed", suffix)
        return None
    return response.status_code


def _submit_result(identity: EvaluationIdentity, record: dict[str, Any]) -> int | None:
    status = _post_report(identity, "completed", record["completed"])
    if status is not None and 200 <= status < 300:
        record["submitted"] = True
    elif status in PERMANENT_SUBMISSION_ERRORS:
        # A rejected result stays inspectable locally, without flooding the API
        # with sessions that were never tracked or invalid report payloads.
        record["submission_error"] = status
    # The file's mtime doubles as the retry ordering: even a failed attempt moves
    # behind other pending sessions without another queue or history record.
    _write_result(record)
    return status


def evaluate_candidate(
    candidate: LocalSessionCandidate,
    *,
    evaluator: str,
    identity: EvaluationIdentity,
) -> None:
    """Run one evaluation, persist it, then upload it under the bound identity."""
    started = EvaluationStartedReport(
        job_id=uuid.uuid4().hex,
        session_id=candidate.session_id,
        client_source=candidate.client_source,
        evaluator_type=evaluator,  # type: ignore[arg-type]
        started_at=datetime.now(UTC),
    )
    # Informational only: a missed start is covered by the completion metadata.
    status = _post_report(identity, "started", started.model_dump(mode="json"))
    if status in {401, 403}:
        # An explicit identity rejection pauses paid work until sign-in is fixed;
        # an unreachable server still never blocks local evaluation.
        return
    if status == 404:
        # This session cannot be reported; cover its revision without paid work.
        _write_result(
            {
                "session_id": candidate.session_id,
                "client_source": candidate.client_source,
                "revision": candidate.revision,
                "server_url": identity.server_url,
                "user_id": identity.user_id,
                "completed": None,
                "submitted": False,
                "submission_error": 404,
            }
        )
        return

    try:
        result, error = _run_evaluator(candidate, evaluator)
    except Exception:
        logger.warning("evaluation failed")
        result, error = None, "Evaluation agent failed"
    if error is not None:
        error = _sanitize_evaluator_log_text(error)[:1024]
    completed = EvaluationCompletedReport.model_validate(
        {
            **started.model_dump(),
            "finished_at": max(datetime.now(UTC), started.started_at),
            "status": "failed" if result is None else "succeeded",
            "result": result,
            "error": error,
        }
    )

    record: dict[str, Any] = {
        "session_id": candidate.session_id,
        "client_source": candidate.client_source,
        "revision": candidate.revision,
        "job_id": started.job_id,
        "server_url": identity.server_url,
        "user_id": identity.user_id,
        "started": started.model_dump(mode="json"),
        "completed": completed.model_dump(mode="json"),
        "submitted": False,
    }
    # Persist before any upload: a crash or HTTP failure never loses the result.
    _write_result(record)

    # The result belongs to the identity that started the run. If the user
    # signed out or switched accounts while the evaluator ran, keep it local.
    if load_evaluation_identity() != identity:
        return
    _submit_result(identity, record)


def _local_result_covers(
    candidate: LocalSessionCandidate, identity: EvaluationIdentity
) -> bool:
    record = _read_result(candidate.session_id)
    if record is None:
        return False
    if record.get("completed") is None:
        if (
            record.get("submission_error") != 404
            or record.get("server_url") != identity.server_url
            or record.get("user_id") != identity.user_id
        ):
            return False
    return record.get("revision") == candidate.revision


def retry_pending_uploads(identity: EvaluationIdentity) -> bool:
    """Re-upload completed results that belong to this server and user."""
    directory = evaluations_dir()
    if not directory.is_dir():
        return True
    attempts = 0
    for path in sorted(
        directory.glob("*.json"), key=lambda path: path.stat().st_mtime_ns
    ):
        if attempts >= MAX_PENDING_UPLOADS_PER_PASS:
            break
        if load_evaluation_identity() != identity:
            return False
        try:
            record = read_object(path)
        except ValueError:
            continue
        if (
            not isinstance(record, dict)
            or record.get("submitted")
            or record.get("submission_error") in PERMANENT_SUBMISSION_ERRORS
        ):
            continue
        completed = record.get("completed")
        if not isinstance(completed, dict):
            continue
        # Account switching must never leak a previous user's results.
        if record.get("server_url") != identity.server_url:
            continue
        if record.get("user_id") != identity.user_id:
            continue
        status = _submit_result(identity, record)
        attempts += 1
        if status in {401, 403}:
            return False
    return True


def run_evaluation_pass(*, now: float | None = None) -> None:
    """One pass: retry uploads, then evaluate one quiet session."""
    identity = load_evaluation_identity()
    if identity is None:
        return
    if not retry_pending_uploads(identity):
        return

    config = load_evaluation_config()
    if not config.auto_enabled:
        return
    evaluator = resolve_evaluator_agent(config.evaluator)
    if evaluator is None:
        return

    current = time.time() if now is None else now
    quiet_cutoff = current - config.quiet_delay_seconds
    for candidate in discover_local_sessions():
        if load_evaluation_identity() != identity:
            return
        if candidate.last_activity > quiet_cutoff:
            break  # sorted: everything after this is even more recent
        if _local_result_covers(candidate, identity):
            continue
        evaluate_candidate(candidate, evaluator=evaluator, identity=identity)
        return


class EvaluationWorker:
    """Runs evaluation passes on a daemon thread inside the client service.

    The thread never raises into the service loop; a failed pass is logged and
    retried on the next interval.
    """

    def __init__(self, pass_interval: float = PASS_INTERVAL_SECONDS) -> None:
        self._pass_interval = pass_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="tokenage-evaluation", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            started_at = time.monotonic()
            try:
                run_evaluation_pass()
            except Exception:
                logger.exception("evaluation pass failed")
            elapsed = time.monotonic() - started_at
            self._stop.wait(max(0.0, self._pass_interval - elapsed))
