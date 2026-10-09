from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

OTEL_EXPORTER_OTLP_LOGS_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"


@dataclass(frozen=True)
class ServicePort:
    """Configured bind address for one tokenage service."""

    service: str
    program: str
    host: str
    port: int


@dataclass(frozen=True)
class SupervisorProgramState:
    """Latest supervisor-reported status for a managed program."""

    status: str
    pid: int | None


@dataclass(frozen=True)
class PortListener:
    """Single process currently listening on a TCP port."""

    pid: int
    command: str


@dataclass(frozen=True)
class PortIssue:
    """Mismatch between configured service ports and actual OS listeners."""

    service: str
    program: str
    host: str
    port: int
    kind: str
    listener_pid: int | None
    listener_command: str | None
    expected_pid: int | None


def get_configured_service_ports(config: dict[str, Any]) -> list[ServicePort]:
    """Expand effective service ports, including OTLP endpoint env override."""
    server = config.get("server", {})
    host = str(server.get("host", "127.0.0.1"))
    api_port = int(server.get("api_port", 4001))

    return [
        ServicePort("API", "tokenage-api", host, api_port),
        resolve_otlp_service_port(config),
    ]


def resolve_otlp_service_port(config: dict[str, Any]) -> ServicePort:
    """Return OTLP listener target, preferring a parseable OTEL logs endpoint."""
    server = config.get("server", {})
    endpoint = os.environ.get(OTEL_EXPORTER_OTLP_LOGS_ENDPOINT_ENV)
    if endpoint:
        parsed_endpoint = _parse_otlp_endpoint(endpoint)
        if parsed_endpoint is not None:
            host, port = parsed_endpoint
            return ServicePort("OTLP", "tokenage-otlp", host, port)

    host = str(server.get("host", "127.0.0.1"))
    api_port = int(server.get("api_port", 4001))
    otlp_port = int(server.get("otlp_port", api_port + 1))
    return ServicePort("OTLP", "tokenage-otlp", host, otlp_port)


def _parse_otlp_endpoint(endpoint: str) -> tuple[str, int] | None:
    try:
        parsed = urlparse(endpoint)
        port = parsed.port
    except ValueError:
        return None

    if parsed.hostname is None or port is None:
        return None
    return parsed.hostname, port


def parse_supervisor_status(text: str) -> dict[str, SupervisorProgramState]:
    """Parse ``supervisorctl status`` output into a program->state map."""
    states: dict[str, SupervisorProgramState] = {}

    for line in text.splitlines():
        if not line.strip():
            continue

        parts = line.split(None, 2)
        if len(parts) < 2:
            continue

        match = re.search(r"\bpid\s+(\d+)\b", line)
        states[parts[0]] = SupervisorProgramState(
            status=parts[1],
            pid=int(match.group(1)) if match else None,
        )

    return states


def detect_port_issues(
    *,
    service_ports: list[ServicePort],
    supervisor_states: dict[str, SupervisorProgramState],
    listeners_by_port: dict[int, list[PortListener]],
) -> list[PortIssue]:
    """Detect configured ports that are missing or owned by the wrong process."""
    issues: list[PortIssue] = []

    for service_port in service_ports:
        listeners = listeners_by_port.get(service_port.port, [])
        program_state = supervisor_states.get(service_port.program)
        expected_pid = None
        if program_state and program_state.status in {"RUNNING", "STARTING"}:
            expected_pid = program_state.pid

        if expected_pid is not None:
            if not listeners:
                issues.append(
                    PortIssue(
                        service=service_port.service,
                        program=service_port.program,
                        host=service_port.host,
                        port=service_port.port,
                        kind="not_listening",
                        listener_pid=None,
                        listener_command=None,
                        expected_pid=expected_pid,
                    )
                )
            elif all(listener.pid != expected_pid for listener in listeners):
                listener = listeners[0]
                issues.append(
                    PortIssue(
                        service=service_port.service,
                        program=service_port.program,
                        host=service_port.host,
                        port=service_port.port,
                        kind="occupied_by_unexpected_process",
                        listener_pid=listener.pid,
                        listener_command=listener.command,
                        expected_pid=expected_pid,
                    )
                )
        elif listeners:
            listener = listeners[0]
            issues.append(
                PortIssue(
                    service=service_port.service,
                    program=service_port.program,
                    host=service_port.host,
                    port=service_port.port,
                    kind="occupied_by_other_process",
                    listener_pid=listener.pid,
                    listener_command=listener.command,
                    expected_pid=None,
                )
            )

    return issues


def format_port_issue(issue: PortIssue) -> str:
    """Render a short human-readable explanation for a detected port issue."""
    if issue.kind == "not_listening":
        return (
            f"  {issue.service}: expected {issue.program} pid {issue.expected_pid} to "
            f"listen on {issue.host}:{issue.port}, but nothing is listening there."
        )

    owner = f"{issue.listener_command} (pid {issue.listener_pid})"
    if issue.kind == "occupied_by_unexpected_process":
        return (
            f"  {issue.service}: {issue.host}:{issue.port} is owned by {owner}, not "
            f"{issue.program} pid {issue.expected_pid}."
        )

    return (
        f"  {issue.service}: {issue.host}:{issue.port} is already owned by {owner}, "
        f"so {issue.program} cannot bind to it."
    )


def get_blocking_port_issues(issues: list[PortIssue]) -> list[PortIssue]:
    """Return only issues that should block a start or restart operation."""
    return [issue for issue in issues if issue.kind != "not_listening"]
