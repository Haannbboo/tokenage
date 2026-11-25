"""Antigravity hooks: local state and spool files only, no network.

Standard library only, because every hook is a cold start inside the agent's
turn. Antigravity reads hook stdout: PreToolUse must print nothing (any output,
even ``{}``, denies the tool call) and every other event must print ``{}``.

Only usage metadata leaves the transcript: step index, time, model, token
counts and tool names. Content, thinking and tool arguments are never copied.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import sys
import time
import unicodedata
import uuid
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import TypeGuard

from client.hooks import raw_project_name
from client.paths import credentials_path, tracker_home

try:
    import fcntl as _flock_module
except ImportError:  # No flock: parallel tool hooks may then lose an update.
    fcntl: ModuleType | None = None
else:
    fcntl = _flock_module

EVENTS = (
    "session-start",
    "pre-invocation",
    "post-invocation",
    "pre-tool-use",
    "post-tool-use",
    "stop",
)
TIMEOUT_SECONDS = 0.8
STDIN_MAX_BYTES = 4 * 1024 * 1024  # toolCall.args can carry a whole file
EVENT_MAX_FILES = 1000  # client.event_spool.MAX_FILES
OTLP_MAX_FILES = 1000  # client.otlp_spool.MAX_FILES
OTLP_MAX_BYTES = 65536  # client.otlp_spool.MAX_BYTES
AGY_MAX_BUFFERED_TOOLS = 100  # Matches the collector's per-message tool cap.
OTLP_MAX_RECORDS = 200  # per spool file; the collector takes at most 1000
SCAN_MAX_BYTES = 8 * 1024 * 1024
SCAN_MAX_LINES = 8192
SCAN_MAX_STEPS = 100  # Bound encoding work for transcripts with short rows.
STATE_RETENTION_SECONDS = 7 * 86400
STATE_MAX_FILES = 500
PENDING_TOOLS_MAX = 500
TOKEN_KEYS = (
    "input_tokens",
    "cache_read_tokens",
    "output_tokens",
    "cache_creation_tokens",
)
_SESSION_ID = re.compile(r"[A-Za-z0-9_.:-]{1,256}")  # protocol.events.SessionId
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:@+\-]{1,128}")  # Matches src.otlp.


def binding_of(data: object) -> str | None:
    """Ties spool files to the signed-in server and device without spooling a
    token. None when signed out. Same value as client.event_spool._identity."""
    if not isinstance(data, dict):
        return None
    server, token = data.get("server_url"), data.get("ingest_token")
    if not isinstance(server, str) or not isinstance(token, str):
        return None
    if not server or not token:
        return None
    return hashlib.sha256(f"{server}\n{token}".encode()).hexdigest()


def binding() -> str | None:
    """binding_of the credentials on disk. Read it once per hook: a second read
    can belong to another login."""
    try:
        return binding_of(json.loads(credentials_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def event_dir() -> Path:
    return tracker_home() / "spool"


def otlp_dir() -> Path:
    return event_dir() / "otlp"


def state_dir() -> Path:
    return tracker_home() / "run" / "agy_state"


def _private_dir(directory: Path) -> bool:
    directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.mkdir(exist_ok=True, mode=0o700)
    if directory.is_symlink():
        return False
    directory.chmod(0o700)
    return True


def _write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _spool(directory: Path, text: str, max_files: int, *, bound: str | None) -> bool:
    """True only when the file is durably in the spool."""
    try:
        if bound is None or not _private_dir(directory):
            return False
        if sum(1 for _ in directory.glob("*.json")) >= max_files:
            return False
        _write_atomic(directory / f"{uuid.uuid4()}.{bound}.json", text)
    except OSError:
        return False
    return True


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _project(cwd: str) -> str | None:
    """protocol.events.sanitize_project, without importing pydantic."""
    value = unicodedata.normalize("NFC", raw_project_name(cwd)[:100].strip())
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
        return None
    return value


def _session_start(conversation: str, payload: dict, bound: str | None) -> None:
    cwd = payload.get("cwd")
    paths = payload.get("workspacePaths")
    if not isinstance(cwd, str) and isinstance(paths, list) and paths:
        cwd = paths[0]
    if not isinstance(cwd, str) or bound is None:
        return
    project = _project(cwd)
    if project is None:
        return
    report = {
        "schema_version": 1,
        "agent": "agy",
        "session_id": conversation,
        "project": project,
        "ts": time.time_ns() // 1000,
    }
    _spool(event_dir(), json.dumps(report), EVENT_MAX_FILES, bound=bound)


# ----------------------------------------------------------------- state


def _fresh_state() -> dict:
    return {
        "binding": None,  # The login this conversation's state belongs to.
        "resync": True,  # A PreInvocation must establish a starting step.
        "last_step_index": -1,
        "byte_offset": 0,
        "discarding_line": False,
        "transcript_inode": None,
        "pending_invocation": None,
        "pending_tools": [],
    }


def _load_state(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    state = _fresh_state()
    for key, kind in (
        ("binding", str),
        ("last_step_index", int),
        ("byte_offset", int),
        ("transcript_inode", int),
        ("pending_invocation", dict),
        ("pending_tools", list),
    ):
        if isinstance(data.get(key), kind) and not isinstance(data[key], bool):
            state[key] = data[key]
    state["resync"] = data.get("resync") is not False
    state["discarding_line"] = data.get("discarding_line") is True
    state["pending_tools"] = [t for t in state["pending_tools"] if isinstance(t, dict)]
    return state


def _same_file(fd: int, path: Path) -> bool:
    try:
        return os.fstat(fd).st_ino == path.stat().st_ino
    except OSError:
        return False


def _lock(path: Path) -> int:
    """An exclusive lock on ``path``. Closing the descriptor unlocks."""
    while True:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if fcntl is None:
                return fd
            fcntl.flock(fd, fcntl.LOCK_EX)
            if _same_file(fd, path):  # Else _prune removed it while we waited.
                return fd
        except BaseException:
            os.close(fd)
            raise
        os.close(fd)


def _unlink_idle_lock(path: Path) -> None:
    """Remove a lock file only while holding it, so no hook loses its lock."""
    if fcntl is None:
        return
    try:
        fd = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if _same_file(fd, path):
            path.unlink(missing_ok=True)
    except OSError:
        pass  # Held by a running hook.
    finally:
        os.close(fd)


def _prune(directory: Path) -> None:
    cutoff = time.time() - STATE_RETENTION_SECONDS
    kept = []
    for path in directory.iterdir():
        if path.suffix not in {".json", ".tmp"}:
            continue
        try:
            modified = path.lstat().st_mtime
            if modified < cutoff:
                path.unlink(missing_ok=True)
            elif path.suffix == ".json":
                kept.append((modified, path))
        except OSError:
            pass
    for _, path in sorted(kept)[: max(0, len(kept) - STATE_MAX_FILES)]:
        path.unlink(missing_ok=True)
    for path in directory.glob("*.lock"):
        if not path.with_suffix(".json").exists():
            _unlink_idle_lock(path)


def _with_state(conversation: str, update) -> None:
    """Run ``update(state, seen)`` under a per-conversation lock and save it."""
    directory = state_dir()
    if not _private_dir(directory):
        return
    digest = hashlib.sha256(conversation.encode()).hexdigest()
    path = directory / f"{digest}.json"
    lock = _lock(directory / f"{digest}.lock")
    try:
        state = _load_state(path)
        seen = state is not None
        state = state or _fresh_state()
        update(state, seen)
        _write_atomic(path, json.dumps(state))
        if not seen:
            _prune(directory)
    finally:
        os.close(lock)


# ------------------------------------------------------------ tool hooks


def _tool_id(conversation: str, call_id: str) -> str:
    return hashlib.sha256(
        f"{conversation}\0{call_id}".encode("utf-8", "surrogatepass")
    ).hexdigest()[:32]


def _tool(conversation: str, payload: dict) -> tuple[str, str] | None:
    call = payload.get("toolCall")
    if not isinstance(call, dict):
        return None
    call_id, name = call.get("id"), call.get("name")
    if not isinstance(name, str) or not _valid_identifier(name):
        return None
    if not isinstance(call_id, str) or not call_id:
        # No id: the step and tool name still pair PreToolUse with PostToolUse.
        step = payload.get("stepIdx")
        if not _is_int(step):
            return None
        call_id = f"step:{step}:{name}"
    return _tool_id(conversation, call_id), name


def _pending_tool(state: dict, call_id: str, name: str, now_ms: int) -> dict:
    for entry in state["pending_tools"]:
        if entry.get("call_id") == call_id:
            return entry
    entry = {
        "call_id": call_id,
        "tool_name": name,
        "ts_ms": now_ms,
        "duration_ms": None,
    }
    state["pending_tools"].append(entry)
    del state["pending_tools"][:-PENDING_TOOLS_MAX]
    return entry


def _pre_invocation(state: dict, payload: dict, now_ms: int) -> None:
    initial = payload.get("initialNumSteps")
    if state["resync"] and _is_int(initial):
        # A conversation that predates the hooks or this login: never replay
        # its history.
        state["last_step_index"] = max(state["last_step_index"], initial - 1)
        state.update(resync=False, pending_tools=[])
        path = payload.get("transcriptPath")
        if isinstance(path, str):
            try:
                with open(path, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    end = info.st_size
                    if end:
                        stream.seek(end - 1)
                        if stream.read(1) != b"\n":
                            start = max(0, end - SCAN_MAX_BYTES)
                            stream.seek(start)
                            newline = stream.read(end - start).rfind(b"\n")
                            end = start + newline + 1 if newline >= 0 else 0
                    state.update(
                        byte_offset=end,
                        transcript_inode=info.st_ino,
                        discarding_line=False,
                    )
            except OSError:
                pass
    number = payload.get("invocationNum")
    state["pending_invocation"] = {
        "invocation_num": number if _is_int(number) else None,
        "started_at_ms": now_ms,
        "first_tool_ts_ms": None,
    }


def _pre_tool_use(state: dict, conversation: str, payload: dict, now_ms: int) -> None:
    tool = _tool(conversation, payload)
    if tool is None:
        return
    call_id, name = tool
    invocation = state["pending_invocation"]
    if invocation is not None and invocation.get("first_tool_ts_ms") is None:
        invocation["first_tool_ts_ms"] = now_ms
    entry = _pending_tool(state, call_id, name, now_ms)
    entry.update(tool_name=name, ts_ms=now_ms, duration_ms=None)


def _post_tool_use(state: dict, conversation: str, payload: dict, now_ms: int) -> None:
    tool = _tool(conversation, payload)
    if tool is None:
        return
    call_id, name = tool
    known = any(entry.get("call_id") == call_id for entry in state["pending_tools"])
    entry = _pending_tool(state, call_id, name, now_ms)
    if known and _is_int(entry.get("ts_ms")):
        entry["duration_ms"] = max(0, now_ms - entry["ts_ms"])


# ------------------------------------------------------------ transcript


def _row(raw: object, last: int) -> dict | None:
    """The privacy allowlist: nothing outside these fields is ever copied."""
    if (
        not isinstance(raw, dict)
        or raw.get("type") != "PLANNER_RESPONSE"
        or raw.get("status") != "DONE"
    ):
        return None
    step = raw.get("step_index")
    if not _is_int(step) or step <= last:
        return None
    row: dict = {"step_index": step, "created_at": raw.get("created_at"), "tools": []}
    if _valid_identifier(raw.get("model")):
        row["model"] = raw["model"]
    for key in TOKEN_KEYS:
        count = raw.get(key)
        if _is_int(count) and count >= 0:
            row[key] = count
    calls = raw.get("tool_calls")
    for index, call in enumerate(calls if isinstance(calls, list) else []):
        name = call.get("name") if isinstance(call, dict) else None
        if _valid_identifier(name):
            row["tools"].append((index, name))
    return row


def _scan(
    path: str, offset: int, inode: int | None, last: int, discarding: bool
) -> tuple[list[dict], int, int, bool]:
    """Read a bounded chunk, retaining the cursor after the last complete line.

    A line larger than the whole budget is discarded across scans. Its
    continuation is never parsed as a new line.
    """
    with open(path, "rb") as stream:
        info = os.fstat(stream.fileno())
        position = 0
        remaining = SCAN_MAX_BYTES
        if info.st_ino == inode and 0 < offset <= info.st_size:
            stream.seek(offset - 1)
            at_boundary = stream.read(1) == b"\n"
            remaining -= 1
            if at_boundary != discarding:
                position = offset
        discarding = discarding and position > 0
        stream.seek(position)
        budget = remaining
        rows: dict[int, dict] = {}
        for _ in range(SCAN_MAX_LINES):
            if not remaining or len(rows) == SCAN_MAX_STEPS:
                break
            line = stream.readline(remaining)
            if not line:
                break
            if not line.endswith(b"\n"):
                if discarding or len(line) == budget:
                    position += len(line)
                    discarding = True
                break
            remaining -= len(line)
            position += len(line)
            if discarding:
                discarding = False
                continue
            if b"PLANNER_RESPONSE" not in line:
                continue
            try:
                row = _row(json.loads(line), last)
            except ValueError:
                continue
            if row is not None:
                rows[row["step_index"]] = row
    return [rows[step] for step in sorted(rows)], position, info.st_ino, discarding


def _created_ns(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1_000_000) * 1000
    except (ValueError, OverflowError, OSError):
        return None


# ------------------------------------------------------------------ OTLP


def _text(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": value}}


def _number(key: str, value: int) -> dict:
    return {"key": key, "value": {"intValue": str(value)}}  # OTLP JSON int64


def _payload(conversation: str, records: list[dict]) -> str:
    return json.dumps(
        {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [
                            _text("service.name", "agy"),
                            _text("session.id", conversation),
                        ]
                    },
                    "scopeLogs": [
                        {"scope": {"name": "tokenage.agy"}, "logRecords": records}
                    ],
                }
            ]
        },
        separators=(",", ":"),
    )


def _step_records(
    conversation: str,
    row: dict,
    tools: list[dict],
    latency_ms: int | None,
    now_ns: int,
) -> list[dict]:
    message = f"{conversation}:{row['step_index']}"
    records = []
    for tool in tools:
        attributes = [
            _text("event.name", "agy.tool_decision"),
            _text("session.id", conversation),
            _text("message.id", message),
            _text("call_id", tool["call_id"]),
            _text("tool_name", tool["tool_name"]),
        ]
        if _is_int(tool.get("duration_ms")):
            attributes.append(_number("duration_ms", tool["duration_ms"]))
        records.append(
            {
                "timeUnixNano": str(tool["ts_ms"] * 1_000_000),
                "observedTimeUnixNano": str(now_ns),
                "attributes": attributes,
            }
        )
    attributes = [
        _text("event.name", "agy.invocation_completed"),
        _text("session.id", conversation),
        _text("message.id", message),
    ]
    if "model" in row:
        attributes.append(_text("model", row["model"]))
    for key in TOKEN_KEYS:
        if key in row or key != "cache_creation_tokens":
            attributes.append(_number(key, row.get(key, 0)))
    if latency_ms is not None:
        attributes.append(_number("duration_ms", latency_ms))
    attributes.append(_number("http.response.status_code", 200))
    records.append(
        {
            "timeUnixNano": str(_created_ns(row["created_at"]) or now_ns),
            "observedTimeUnixNano": str(now_ns),
            "attributes": attributes,
        }
    )
    return records


def _flush(
    state: dict,
    conversation: str,
    payload: dict,
    now_ms: int,
    final: bool,
    bound: str | None,
) -> None:
    rows: list[dict] = []
    scanned = None
    path = payload.get("transcriptPath")
    if isinstance(path, str):
        try:
            rows, *scanned = _scan(
                path,
                state["byte_offset"],
                state["transcript_inode"],
                state["last_step_index"],
                state["discarding_line"],
            )
        except OSError:
            pass
    # Signed out, or the login changed since these steps began: they belong to
    # nobody who is signed in now, so the cursor passes them unsent.
    skip = bound is None or state["resync"]
    if skip or not rows:
        if scanned:
            (
                state["byte_offset"],
                state["transcript_inode"],
                state["discarding_line"],
            ) = scanned
        if rows:
            state["last_step_index"] = rows[-1]["step_index"]
        if skip or final:
            state.update(pending_invocation=None, pending_tools=[])
        return  # PostInvocation can run before the step is written; Stop retries.

    # Only a tool call marks when the model finished. created_at is whole
    # seconds and now() differs on redelivery, so a tool-less step has none.
    latency_ms = None
    invocation = state["pending_invocation"]
    if (
        len(rows) == 1
        and invocation
        and _is_int(invocation.get("started_at_ms"))
        and _is_int(invocation.get("first_tool_ts_ms"))
    ):
        latency_ms = max(
            0, invocation["first_tool_ts_ms"] - invocation["started_at_ms"]
        )

    # Hooked tool calls go to the step that names them, in order. A call the
    # transcript names but no hook saw (denied before PreToolUse) gets a
    # synthetic id; a hooked call no step names stays with the last step.
    pending = [
        tool
        for tool in state["pending_tools"]
        if isinstance(tool.get("call_id"), str)
        and _valid_identifier(tool.get("tool_name"))
        and _is_int(tool.get("ts_ms"))
    ]
    pending_by_name: dict[str, deque[dict]] = defaultdict(deque)
    pending_by_id = {tool["call_id"]: tool for tool in pending}
    for tool in pending:
        pending_by_name[tool["tool_name"]].append(tool)
    now_ns = now_ms * 1_000_000
    steps: list[tuple[dict, list[dict], set[str]]] = []
    for row in rows:
        created_ns = _created_ns(row["created_at"])
        tools: list[dict] = []
        matched: set[str] = set()
        for index, name in row["tools"]:
            full = len(tools) >= AGY_MAX_BUFFERED_TOOLS
            if full and not pending_by_id:
                break
            matches = pending_by_name.get(name)
            match = matches.popleft() if matches else None
            if match is not None:
                call_id = match["call_id"]
                pending_by_id.pop(call_id, None)
                matched.add(call_id)
            if full:
                continue
            if match is not None:
                tools.append(match)
            else:
                tools.append(
                    {
                        "call_id": _tool_id(
                            conversation, f"{row['step_index']}:{index}"
                        ),
                        "tool_name": name,
                        "ts_ms": (created_ns or now_ns) // 1_000_000,
                        "duration_ms": None,
                    }
                )
        steps.append((row, tools, matched))
    last_tools = steps[-1][1]
    last_tools.extend(
        list(pending_by_id.values())[: AGY_MAX_BUFFERED_TOOLS - len(last_tools)]
    )

    # Keep each step in one payload with its completion after the decisions.
    envelope = len(_payload(conversation, []))
    batches: list[tuple[list[dict], int]] = []
    batch: list[dict] = []
    size = envelope
    for count, (row, tools, _) in enumerate(steps, 1):
        if not any(key in row for key in TOKEN_KEYS):
            continue
        records = _step_records(conversation, row, tools, latency_ms, now_ns)
        sizes = [
            len(json.dumps(record, separators=(",", ":"))) + 1 for record in records
        ]
        step_size = sum(sizes)
        # Long session IDs and names can exhaust the byte budget before the
        # tool count does. Keep the completion and as many decisions as fit.
        while len(records) > 1 and envelope + step_size > OTLP_MAX_BYTES:
            records.pop(-2)
            step_size -= sizes.pop(-2)
        if batch and (
            len(batch) + len(records) > OTLP_MAX_RECORDS
            or size + step_size > OTLP_MAX_BYTES
        ):
            batches.append((batch, count - 1))
            batch = []
            size = envelope
        batch.extend(records)
        size += step_size
    batches.append((batch, len(steps)))

    # The cursor only passes steps that are durably spooled; the rest are read
    # from the transcript again by a later hook.
    done = 0
    for batch_records, complete in batches:
        if batch_records:
            text = _payload(conversation, batch_records)
            if not _spool(otlp_dir(), text, OTLP_MAX_FILES, bound=bound):
                break
        done = complete
    if done:
        state["pending_invocation"] = None
        state["last_step_index"] = steps[done - 1][0]["step_index"]
    if done == len(steps):
        assert scanned is not None  # Every step came from this scan.
        state["byte_offset"], state["transcript_inode"], state["discarding_line"] = (
            scanned
        )
        state["pending_tools"] = []
    else:
        sent = {call_id for _, _, matched in steps[:done] for call_id in matched}
        state["pending_tools"] = [
            tool for tool in state["pending_tools"] if tool.get("call_id") not in sent
        ]


# ------------------------------------------------------------------ main


def _handle(event: str) -> None:
    raw = sys.stdin.read(STDIN_MAX_BYTES + 1)
    if len(raw) > STDIN_MAX_BYTES:
        return
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return
    conversation = payload.get("conversationId")
    if not isinstance(conversation, str) or not _SESSION_ID.fullmatch(conversation):
        return
    bound = binding()  # Once: every decision below is about this one login.
    if event == "session-start":
        _session_start(conversation, payload, bound)
        return
    now_ms = time.time_ns() // 1_000_000

    def update(state: dict, seen: bool) -> None:
        if seen and state["binding"] != bound:
            # Another login (or none) left this state: nothing it gathered may
            # be sent as this one.
            state.update(pending_invocation=None, pending_tools=[], resync=True)
        state["binding"] = bound
        if event == "pre-invocation":
            _pre_invocation(state, payload, now_ms)
        elif event == "pre-tool-use":
            _pre_tool_use(state, conversation, payload, now_ms)
        elif event == "post-tool-use":
            _post_tool_use(state, conversation, payload, now_ms)
        else:
            _flush(
                state, conversation, payload, now_ms, final=event == "stop", bound=bound
            )

    _with_state(conversation, update)


def main(argv: list[str]) -> int:
    event = argv[0] if argv else ""

    def expired(*_):
        raise TimeoutError()

    old = None
    try:
        if hasattr(signal, "SIGALRM"):
            try:
                old = signal.signal(signal.SIGALRM, expired)
                signal.setitimer(signal.ITIMER_REAL, TIMEOUT_SECONDS)
            except ValueError:  # Signals are unavailable outside the main thread.
                pass
        if event in EVENTS:
            _handle(event)
    except Exception:
        pass  # Fail open, and never log payloads, credentials or paths.
    finally:
        if old is not None:
            try:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, old)
            except Exception:
                pass
    if event != "pre-tool-use":
        try:
            sys.stdout.write("{}\n")
            sys.stdout.flush()
        except Exception:
            pass
    return 0
