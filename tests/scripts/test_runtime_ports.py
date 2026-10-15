import pytest


def test_detect_port_issues_flags_preflight_conflict(runtime_ports_module):
    service_ports = [
        runtime_ports_module.ServicePort(
            service="API",
            program="tokenage-api",
            host="127.0.0.1",
            port=4001,
        )
    ]
    listeners_by_port = {
        4001: [runtime_ports_module.PortListener(pid=18431, command="QQ")]
    }

    issues = runtime_ports_module.detect_port_issues(
        service_ports=service_ports,
        supervisor_states={},
        listeners_by_port=listeners_by_port,
    )

    assert len(issues) == 1
    assert issues[0] == runtime_ports_module.PortIssue(
        service="API",
        program="tokenage-api",
        host="127.0.0.1",
        port=4001,
        kind="occupied_by_other_process",
        listener_pid=18431,
        listener_command="QQ",
        expected_pid=None,
    )


def test_detect_port_issues_flags_running_service_owned_by_other_process(
    runtime_ports_module,
):
    service_ports = [
        runtime_ports_module.ServicePort(
            service="API",
            program="tokenage-api",
            host="127.0.0.1",
            port=4001,
        )
    ]
    supervisor_states = {
        "tokenage-api": runtime_ports_module.SupervisorProgramState(
            status="RUNNING",
            pid=76037,
        )
    }
    listeners_by_port = {
        4001: [runtime_ports_module.PortListener(pid=18431, command="QQ")]
    }

    issues = runtime_ports_module.detect_port_issues(
        service_ports=service_ports,
        supervisor_states=supervisor_states,
        listeners_by_port=listeners_by_port,
    )

    assert len(issues) == 1
    assert issues[0] == runtime_ports_module.PortIssue(
        service="API",
        program="tokenage-api",
        host="127.0.0.1",
        port=4001,
        kind="occupied_by_unexpected_process",
        listener_pid=18431,
        listener_command="QQ",
        expected_pid=76037,
    )


def test_detect_port_issues_allows_running_service_on_expected_port(
    runtime_ports_module,
):
    service_ports = [
        runtime_ports_module.ServicePort(
            service="API",
            program="tokenage-api",
            host="127.0.0.1",
            port=4001,
        )
    ]
    supervisor_states = {
        "tokenage-api": runtime_ports_module.SupervisorProgramState(
            status="RUNNING",
            pid=76037,
        )
    }
    listeners_by_port = {
        4001: [runtime_ports_module.PortListener(pid=76037, command="Python")]
    }

    issues = runtime_ports_module.detect_port_issues(
        service_ports=service_ports,
        supervisor_states=supervisor_states,
        listeners_by_port=listeners_by_port,
    )

    assert issues == []


@pytest.mark.parametrize(
    "status,pid,known_pid,expected_kind",
    [
        ("RUNNING", 76037, None, None),
        ("STARTING", 76037, None, None),
        ("RUNNING", None, None, "occupied_by_other_process"),
        ("STOPPED", None, None, "occupied_by_other_process"),
        ("RUNNING", 76037, 18431, "occupied_by_unexpected_process"),
        ("RUNNING", 76037, 76037, None),
    ],
)
def test_detect_port_issues_with_unknown_ownership(
    runtime_ports_module, status, pid, known_pid, expected_kind
):
    m = runtime_ports_module
    listeners = [m.PortListener(-1, "(unknown)")]
    if known_pid is not None:
        listeners.append(m.PortListener(known_pid, "python"))

    issues = m.detect_port_issues(
        service_ports=[m.ServicePort("API", "tokenage-api", "127.0.0.1", 4001)],
        supervisor_states={"tokenage-api": m.SupervisorProgramState(status, pid)},
        listeners_by_port={4001: listeners},
    )

    if expected_kind is None:
        assert issues == []
    else:
        assert len(issues) == 1
        assert issues[0].kind == expected_kind
        assert issues[0].listener_pid == (known_pid if known_pid is not None else -1)
        assert m.get_blocking_port_issues(issues) == issues


def test_get_blocking_port_issues_ignores_not_listening(runtime_ports_module):
    issues = [
        runtime_ports_module.PortIssue(
            service="API",
            program="tokenage-api",
            host="127.0.0.1",
            port=4004,
            kind="not_listening",
            listener_pid=None,
            listener_command=None,
            expected_pid=342,
        )
    ]

    assert runtime_ports_module.get_blocking_port_issues(issues) == []


def test_get_configured_service_ports_lists_only_the_api(runtime_ports_module):
    # OTLP is mounted in the API; a stale server.otlp_port is ignored.
    service_ports = runtime_ports_module.get_configured_service_ports(
        {"server": {"host": "127.0.0.1", "api_port": 4001, "otlp_port": 4005}}
    )

    assert service_ports == [
        runtime_ports_module.ServicePort("API", "tokenage-api", "127.0.0.1", 4001)
    ]


# ---------------------------------------------------------------------------
# parse_supervisor_status
# ---------------------------------------------------------------------------


def test_parse_supervisor_status_empty_string(runtime_ports_module):
    assert runtime_ports_module.parse_supervisor_status("") == {}


def test_parse_supervisor_status_single_program_with_pid(runtime_ports_module):
    result = runtime_ports_module.parse_supervisor_status(
        "tokenage-api RUNNING pid 1234"
    )
    assert result == {
        "tokenage-api": runtime_ports_module.SupervisorProgramState(
            status="RUNNING",
            pid=1234,
        )
    }


def test_parse_supervisor_status_program_without_pid(runtime_ports_module):
    result = runtime_ports_module.parse_supervisor_status("tokenage-api STOPPED")
    assert result == {
        "tokenage-api": runtime_ports_module.SupervisorProgramState(
            status="STOPPED",
            pid=None,
        )
    }


def test_parse_supervisor_status_multiple_programs(runtime_ports_module):
    text = (
        "tokenage-api RUNNING pid 100\n"
        "tokenage-web RUNNING pid 200\n"
        "tokenage-otlp STOPPED\n"
    )
    result = runtime_ports_module.parse_supervisor_status(text)
    assert result == {
        "tokenage-api": runtime_ports_module.SupervisorProgramState(
            status="RUNNING", pid=100
        ),
        "tokenage-web": runtime_ports_module.SupervisorProgramState(
            status="RUNNING", pid=200
        ),
        "tokenage-otlp": runtime_ports_module.SupervisorProgramState(
            status="STOPPED", pid=None
        ),
    }


# ---------------------------------------------------------------------------
# format_port_issue
# ---------------------------------------------------------------------------


def test_format_port_issue_not_listening(runtime_ports_module):
    issue = runtime_ports_module.PortIssue(
        service="API",
        program="tokenage-api",
        host="127.0.0.1",
        port=4001,
        kind="not_listening",
        listener_pid=None,
        listener_command=None,
        expected_pid=76037,
    )
    text = runtime_ports_module.format_port_issue(issue)
    assert "expected" in text
    assert "tokenage-api" in text
    assert "pid 76037" in text
    assert "nothing is listening" in text


def test_format_port_issue_occupied_by_unexpected_process(runtime_ports_module):
    issue = runtime_ports_module.PortIssue(
        service="API",
        program="tokenage-api",
        host="127.0.0.1",
        port=4001,
        kind="occupied_by_unexpected_process",
        listener_pid=18431,
        listener_command="QQ",
        expected_pid=76037,
    )
    text = runtime_ports_module.format_port_issue(issue)
    assert "owned by" in text
    assert "QQ (pid 18431)" in text
    assert "not tokenage-api pid 76037" in text


def test_format_port_issue_occupied_by_other_process(runtime_ports_module):
    issue = runtime_ports_module.PortIssue(
        service="API",
        program="tokenage-api",
        host="127.0.0.1",
        port=4001,
        kind="occupied_by_other_process",
        listener_pid=18431,
        listener_command="QQ",
        expected_pid=None,
    )
    text = runtime_ports_module.format_port_issue(issue)
    assert "already owned by" in text
    assert "QQ (pid 18431)" in text
    assert "tokenage-api cannot bind" in text


# ---------------------------------------------------------------------------
# detect_port_issues edge cases
# ---------------------------------------------------------------------------


def test_detect_port_issues_stopped_program_with_listeners(runtime_ports_module):
    service_ports = [
        runtime_ports_module.ServicePort(
            service="API",
            program="tokenage-api",
            host="127.0.0.1",
            port=4001,
        )
    ]
    supervisor_states = {
        "tokenage-api": runtime_ports_module.SupervisorProgramState(
            status="STOPPED",
            pid=None,
        )
    }
    listeners_by_port = {
        4001: [runtime_ports_module.PortListener(pid=5555, command="nginx")]
    }

    issues = runtime_ports_module.detect_port_issues(
        service_ports=service_ports,
        supervisor_states=supervisor_states,
        listeners_by_port=listeners_by_port,
    )

    assert len(issues) == 1
    assert issues[0] == runtime_ports_module.PortIssue(
        service="API",
        program="tokenage-api",
        host="127.0.0.1",
        port=4001,
        kind="occupied_by_other_process",
        listener_pid=5555,
        listener_command="nginx",
        expected_pid=None,
    )


def test_detect_port_issues_empty_service_ports(runtime_ports_module):
    issues = runtime_ports_module.detect_port_issues(
        service_ports=[],
        supervisor_states={
            "tokenage-api": runtime_ports_module.SupervisorProgramState(
                status="RUNNING", pid=100
            )
        },
        listeners_by_port={
            4001: [runtime_ports_module.PortListener(pid=999, command="X")]
        },
    )
    assert issues == []


def test_detect_port_issues_multiple_listeners_uses_first(runtime_ports_module):
    service_ports = [
        runtime_ports_module.ServicePort(
            service="API",
            program="tokenage-api",
            host="127.0.0.1",
            port=4001,
        )
    ]
    supervisor_states = {
        "tokenage-api": runtime_ports_module.SupervisorProgramState(
            status="RUNNING",
            pid=76037,
        )
    }
    listeners_by_port = {
        4001: [
            runtime_ports_module.PortListener(pid=1111, command="first"),
            runtime_ports_module.PortListener(pid=2222, command="second"),
        ]
    }

    issues = runtime_ports_module.detect_port_issues(
        service_ports=service_ports,
        supervisor_states=supervisor_states,
        listeners_by_port=listeners_by_port,
    )

    assert len(issues) == 1
    assert issues[0].listener_pid == 1111
    assert issues[0].listener_command == "first"
    assert issues[0].kind == "occupied_by_unexpected_process"
