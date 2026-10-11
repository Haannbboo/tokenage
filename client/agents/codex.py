"""Codex: the OTLP logs exporter in ``~/.codex/config.toml``."""

import json
import re
import sys
import tomllib
from copy import deepcopy
from pathlib import Path

from client.agents import (
    DONE,
    FAILED,
    SKIPPED,
)
from client.agents import (
    info as _info,
)
from client.agents import (
    write_private as _write_private,
)


def update_existing_otel_config(
    content: str, endpoint: str, token: str | None = None
) -> str:
    """Configure only the logs HTTP exporter, preserving other OTEL settings."""
    parsed = tomllib.loads(content)
    otel = parsed.get("otel", {})
    if not isinstance(otel, dict):
        raise ValueError("unsupported Codex HTTP exporter layout; left unchanged")
    exporter = otel.get("exporter", {})
    if exporter == "none":
        exporter = {}
    if not isinstance(exporter, dict):
        raise ValueError("unsupported Codex HTTP exporter layout; left unchanged")
    http = exporter.get("otlp-http", {})
    http = deepcopy(http) if isinstance(http, dict) else {}
    http.update(endpoint=endpoint, protocol="json")
    headers = http.get("headers")
    headers = dict(headers) if isinstance(headers, dict) else {}
    if token:
        headers["x-tokenage-token"] = token
    else:
        headers.pop("x-tokenage-token", None)
    if headers:
        http["headers"] = headers
    else:
        http.pop("headers", None)

    desired = deepcopy(parsed)
    desired_otel = desired.setdefault("otel", {"environment": "dev"})
    desired_exporter = deepcopy(exporter)
    desired_exporter["otlp-http"] = http
    desired_otel["exporter"] = desired_exporter
    new_content = _edit_otel_config(content, parsed, exporter, http)
    # Validate both syntax and preservation before the caller writes anything.
    try:
        if tomllib.loads(new_content) != desired:
            raise ValueError("configuration values changed unexpectedly")
    except ValueError as exc:
        raise ValueError(
            "unsupported Codex HTTP exporter layout; left unchanged"
        ) from exc
    return new_content


def _edit_otel_config(content: str, parsed: dict, exporter: dict, http: dict) -> str:
    if "otel" not in parsed:
        return content.rstrip() + (
            '\n\n[otel]\nenvironment = "dev"\nexporter = '
            + _inline_toml({"otlp-http": http})
            + "\n"
        )
    if re.search(r"(?m)^\[otel\.exporter\.otlp-http\]", content):
        return _write_http_keys(content, http)
    section = re.search(
        r"(?ms)^\[otel\][^\S\n]*(?:#[^\n]*)?(?:\n|\Z)(.*?)(?=^\[|\Z)",
        content,
    )
    if section and re.search(r"(?m)^\s*exporter\s*=", section.group(1)):
        return _write_http_keys(content, http)
    if not exporter and section:
        body = (
            section.group(1).rstrip()
            + "\nexporter = "
            + _inline_toml({"otlp-http": http})
            + "\n"
        )
        return content[: section.start(1)] + body + content[section.end(1) :]
    return (
        content.rstrip()
        + "\n\n[otel.exporter.otlp-http]\n"
        + "".join(f"{key} = {_inline_toml(value)}\n" for key, value in http.items())
    )


def _inline_toml(value) -> str:
    """Render the inline exporter values while preserving unrelated options."""
    if isinstance(value, dict):
        return (
            "{ "
            + ", ".join(
                f"{key if key in ('otlp-http', 'endpoint', 'protocol', 'headers') else json.dumps(key)} = {_inline_toml(item)}"
                for key, item in value.items()
            )
            + " }"
        )
    if isinstance(value, list):
        return "[" + ", ".join(_inline_toml(item) for item in value) + "]"
    if isinstance(value, (str, bool, int, float)):
        return json.dumps(value)
    return value.isoformat()


def _write_http_keys(content: str, http: dict) -> str:
    """Edit only the HTTP exporter in the two shapes emitted by this script."""
    # The parsed headers are rendered inline below. Remove their old table so
    # TOML does not declare the same headers twice.
    content = re.sub(
        r"(?ms)^\[otel\.exporter\.otlp-http\.headers(?:\.[^\]]+)?\]"
        r"[^\S\n]*(?:#[^\n]*)?(?:\n|\Z).*?(?=^\[|\Z)",
        "",
        content,
    )
    section = re.search(
        r"(?ms)(^\[otel\.exporter\.otlp-http\][^\S\n]*(?:#[^\n]*)?(?:\n|\Z))"
        r"(.*?)(?=^\[|\Z)",
        content,
    )
    if section:
        body = section.group(2)
        body = re.sub(r"(?m)^\s*(endpoint|protocol|headers)\s*=.*\n?", "", body)
        body = (
            body.rstrip()
            + "\n"
            + "".join(
                f"{key} = {_inline_toml(http[key])}\n"
                for key in ("endpoint", "protocol", "headers")
                if key in http
            )
        )
        return content[: section.start(2)] + body + content[section.end(2) :]
    section = re.search(
        r"(?ms)^\[otel\][^\S\n]*(?:#[^\n]*)?(?:\n|\Z)(.*?)(?=^\[|\Z)",
        content,
    )
    if section:
        body = section.group(1)
        exporter = tomllib.loads(content)["otel"].get("exporter")
        exporter = exporter if isinstance(exporter, dict) else {}
        exporter["otlp-http"] = http
        body = re.sub(
            r"(?m)^\s*exporter\s*=.*$",
            lambda _: "exporter = " + _inline_toml(exporter),
            body,
            count=1,
        )
        return content[: section.start(1)] + body + content[section.end(1) :]
    raise ValueError("unsupported Codex HTTP exporter layout; left unchanged")


def _remove_http_exporter(content: str, exporter) -> str:
    section = re.search(
        r"(?ms)^\[otel\][^\S\n]*(?:#[^\n]*)?(?:\n|\Z)(.*?)(?=^\[|\Z)",
        content,
    )
    if section and re.search(r"(?m)^\s*exporter\s*=", section.group(1)):
        body = re.sub(
            r"(?m)^\s*exporter\s*=.*$",
            lambda _: "exporter = " + _inline_toml(exporter),
            section.group(1),
            count=1,
        )
        return content[: section.start(1)] + body + content[section.end(1) :]
    # Remove only the HTTP exporter and its own header subtables.
    cleaned = re.sub(
        r"(?ms)^\[otel\.exporter\.otlp-http(?:\.[^\]]+)?\]"
        r"[^\S\n]*(?:#[^\n]*)?(?:\n|\Z).*?(?=^\[|\Z)",
        "",
        content,
    )
    if exporter == "none":
        cleaned = re.sub(
            r"(?ms)^\[otel\.exporter\][^\S\n]*(?:#[^\n]*)?(?:\n|\Z).*?(?=^\[|\Z)",
            "",
            cleaned,
        )
        section = re.search(
            r"(?ms)^\[otel\][^\S\n]*(?:#[^\n]*)?(?:\n|\Z)(.*?)(?=^\[|\Z)",
            cleaned,
        )
        if not section:
            return cleaned.rstrip() + '\n\n[otel]\nexporter = "none"\n'
        body = section.group(1).rstrip() + '\nexporter = "none"\n'
        cleaned = cleaned[: section.start(1)] + body + cleaned[section.end(1) :]
    return cleaned


def _disable(config_path: Path, expected_endpoint: str | None) -> int:
    if not expected_endpoint:
        print("collector unknown; Codex configuration left unchanged", file=sys.stderr)
        return SKIPPED
    if not config_path.exists():
        _info(f"No Codex config at {config_path}")
        return SKIPPED
    content = config_path.read_text(encoding="utf-8")
    try:
        parsed = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        print("invalid Codex TOML configuration; left unchanged", file=sys.stderr)
        return FAILED
    otel = parsed.get("otel")
    exporter = otel.get("exporter") if isinstance(otel, dict) else None
    http = exporter.get("otlp-http") if isinstance(exporter, dict) else None
    if not isinstance(http, dict) or not http.get("endpoint"):
        _info(f"No tokenage telemetry in {config_path}")
        return SKIPPED
    if expected_endpoint and http["endpoint"] != expected_endpoint:
        print(f"{config_path} points at another collector; left alone", file=sys.stderr)
        return SKIPPED
    desired = deepcopy(parsed)
    remaining = desired["otel"]["exporter"]
    remaining.pop("otlp-http")
    if not remaining:
        desired["otel"]["exporter"] = "none"
    try:
        new_content = _remove_http_exporter(content, desired["otel"]["exporter"])
        # Refuse a text edit that changes any unrelated TOML value.
        if tomllib.loads(new_content) != desired:
            raise ValueError("unsupported Codex HTTP exporter layout; left unchanged")
    except (ValueError, TypeError, AttributeError) as exc:
        print(str(exc), file=sys.stderr)
        return FAILED
    _write_private(config_path, new_content)
    _info(f"Codex OTLP telemetry removed from {config_path}")
    return DONE


def configure(target: str | Path, logs_endpoint: str, token: str | None = None) -> int:
    config_path = Path(target).expanduser()
    if not config_path.parent.exists():
        print(
            f"WARNING: {config_path.parent} does not exist; skipping Codex configuration",
            file=sys.stderr,
        )
        return SKIPPED
    try:
        content = (
            config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        )
        try:
            new_content = update_existing_otel_config(content, logs_endpoint, token)
        except (ValueError, TypeError, AttributeError):
            print(
                "unsupported Codex HTTP exporter layout; left unchanged",
                file=sys.stderr,
            )
            return FAILED
        if new_content != content:
            _write_private(config_path, new_content)
            _info(f"Codex OTLP telemetry updated in {config_path}")
        else:
            _info(f"Codex OTLP telemetry already up-to-date in {config_path}")
    except (OSError, UnicodeError):
        print("cannot update Codex configuration; left unchanged", file=sys.stderr)
        return FAILED
    return DONE


def disable(target: str | Path, expected_endpoint: str | None) -> int:
    try:
        return _disable(Path(target).expanduser(), expected_endpoint)
    except (OSError, UnicodeError):
        print("cannot update Codex configuration; left unchanged", file=sys.stderr)
        return FAILED
