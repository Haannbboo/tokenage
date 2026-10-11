"""Agent configuration modules: endpoint handling, disable-only-when-matching,
refusing unsafe edits, plugin registration.

``_run`` calls the module functions in-process and mimics a finished process
(returncode, stdout, stderr) so each case reads as a before/after on one file.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from client.agents import claude, codex, kilo, opencode

ENDPOINT = "https://api.example.com:4005/v1/logs"
AGENTS = {"claude": claude, "codex": codex, "opencode": opencode, "kilo": kilo}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", raising=False)


def _run(agent: str, args: list[str], home: Path | None = None):
    """``[target, ENDPOINT[, TOKEN]]`` configures; ``[target, "--disable"[, ENDPOINT]]`` disables."""
    module, (target, *rest) = AGENTS[agent], args
    out, err = io.StringIO(), io.StringIO()
    with (
        mock.patch.dict(os.environ, {"HOME": str(home)} if home else {}),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        if rest[:1] == ["--disable"]:
            code = module.disable(target, rest[1] if len(rest) > 1 else None)
        else:
            code = module.configure(target, rest[0], rest[1] if len(rest) > 1 else None)
    return SimpleNamespace(
        returncode=code, stdout=out.getvalue(), stderr=err.getvalue()
    )


def _make_built_plugin_root(tmp_path: Path, name: str) -> Path:
    project_root = tmp_path / "repo"
    plugin_dir = project_root / "plugins" / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "node_modules").mkdir()
    (plugin_dir / "dist").mkdir()
    (plugin_dir / "dist" / "index.js").write_text("export default async () => ({})\n")
    return project_root


# ------------------------------------------------------------------- claude


def test_claude_endpoint_arg(tmp_path):
    settings = tmp_path / "settings.json"
    result = _run(
        "claude",
        [str(settings), ENDPOINT],
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    env = json.loads(settings.read_text())["env"]
    assert env["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] == ENDPOINT


def test_claude_ignores_ambient_otlp_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", "http://env.example:9/v1/logs"
    )
    settings = tmp_path / "settings.json"
    result = _run(
        "claude",
        [str(settings), ENDPOINT],
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    env = json.loads(settings.read_text())["env"]
    assert env["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] == ENDPOINT


# -------------------------------------------------------------------- codex


def test_codex_endpoint_arg(tmp_path):
    config = tmp_path / "config.toml"
    result = _run(
        "codex",
        [str(config), ENDPOINT],
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    content = config.read_text()
    assert f'endpoint = "{ENDPOINT}"' in content


def test_codex_token_arg_writes_otlp_header(tmp_path):
    config = tmp_path / "config.toml"
    result = _run(
        "codex",
        [str(config), ENDPOINT, "ingest-secret"],
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    content = config.read_text()
    assert 'headers = { "x-tokenage-token" = "ingest-secret" }' in content
    parsed = tomllib.loads(content)
    assert (
        parsed["otel"]["exporter"]["otlp-http"]["headers"]["x-tokenage-token"]
        == "ingest-secret"
    )


def test_codex_missing_config_dir_announces_the_skip(tmp_path):
    # A machine with `codex` on PATH but no ~/.codex yet: the script must say
    # it skipped, or `tokenage login` reports the agent as wired.
    config = tmp_path / "missing" / "config.toml"
    result = _run(
        "codex",
        [str(config), ENDPOINT],
        home=tmp_path,
    )
    assert result.returncode == 2, result.stderr
    assert "skipping" in result.stderr
    assert not config.exists()


@pytest.mark.parametrize("token", ["", "ingest-secret"])
def test_codex_configure_preserves_nested_headers(tmp_path, token):
    config = tmp_path / "config.toml"
    config.write_text("""model = "test"
[otel]
environment = "production"
[otel.exporter.otlp-http]
endpoint = "https://old.example/v1/logs"
protocol = "json"
timeout_ms = 8000
[otel.exporter.otlp-http.headers] # user headers
x-custom = "keep"
x-tokenage-token = "old-token"
[otel.trace_exporter.otlp-http]
endpoint = "https://traces.example"
""")
    expected = tomllib.loads(config.read_text())
    http = expected["otel"]["exporter"]["otlp-http"]
    http["endpoint"] = ENDPOINT
    if token:
        http["headers"]["x-tokenage-token"] = token
    else:
        http["headers"].pop("x-tokenage-token")

    result = _run(
        "codex",
        [str(config), ENDPOINT, token],
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert tomllib.loads(config.read_text()) == expected


def test_codex_configure_without_final_newline_preserves_options(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("""[otel]
environment = "production"
[otel.exporter.otlp-http]
endpoint = "https://old.example/v1/logs"
timeout_ms = 8000""")
    expected = tomllib.loads(config.read_text())
    expected["otel"]["exporter"]["otlp-http"].update(endpoint=ENDPOINT, protocol="json")

    result = _run(
        "codex",
        [str(config), ENDPOINT, ""],
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert tomllib.loads(config.read_text()) == expected


def test_codex_configure_commented_table_after_disable(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("""[otel] # user settings
environment = "production"
exporter = "none"
""")

    result = _run(
        "codex",
        [str(config), ENDPOINT, ""],
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert tomllib.loads(config.read_text()) == {
        "otel": {
            "environment": "production",
            "exporter": {"otlp-http": {"endpoint": ENDPOINT, "protocol": "json"}},
        }
    }


@pytest.mark.parametrize("layout", ["inline", "nested"])
def test_codex_disable_and_reconfigure_commented_tables(tmp_path, layout):
    config = tmp_path / "config.toml"
    exporter = (
        f'exporter = {{ otlp-http = {{ endpoint = "{ENDPOINT}" }} }}\n'
        if layout == "inline"
        else f'''[otel.exporter] # exporters
[otel.exporter.otlp-http] # logs
endpoint = "{ENDPOINT}"
[otel.exporter.otlp-http.headers] # headers
x-custom = "keep"
'''
    )
    config.write_text(
        '[otel] # user settings\nenvironment = "production"\n'
        + exporter
        + '[otel.trace_exporter.otlp-http]\nendpoint = "https://traces.example"\n'
    )
    expected = tomllib.loads(config.read_text())
    expected["otel"]["exporter"] = "none"

    for _ in range(2):
        result = _run(
            "codex",
            [str(config), "--disable", ENDPOINT],
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert tomllib.loads(config.read_text()) == expected
        assert "[otel] # user settings" in config.read_text()

        result = _run(
            "codex",
            [str(config), ENDPOINT, ""],
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert (
            tomllib.loads(config.read_text())["otel"]["exporter"]["otlp-http"][
                "endpoint"
            ]
            == ENDPOINT
        )


@pytest.mark.parametrize(
    "content",
    [
        'otel.exporter = { otlp-http = { endpoint = "https://old.example/v1/logs" } }\n',
        '''[otel]
[otel.exporter.otlp-http]
endpoint = "https://old.example/v1/logs"
headers = { x-custom = """first line
second line""" }
''',
    ],
)
def test_codex_configure_refuses_unsafe_edit_without_changing_file(tmp_path, content):
    config = tmp_path / "config.toml"
    config.write_text(content)
    tomllib.loads(content)

    result = _run(
        "codex",
        [str(config), ENDPOINT, "ingest-secret"],
        tmp_path,
    )

    assert result.returncode == 1
    assert "left unchanged" in result.stderr
    assert "ingest-secret" not in result.stdout + result.stderr
    assert config.read_text() == content


@pytest.mark.parametrize("disable", [False, True])
def test_codex_invalid_config_reports_failure_without_traceback(tmp_path, disable):
    config = tmp_path / "config.toml"
    content = 'model = "unfinished\n'
    config.write_text(content)
    args = [str(config), "--disable", ENDPOINT] if disable else [str(config), ENDPOINT]

    result = _run("codex", args, tmp_path)

    assert result.returncode == 1
    assert "left unchanged" in result.stderr
    assert "Traceback" not in result.stderr
    assert config.read_text() == content


# --------------------------------------------------------- opencode / kilo


def test_opencode_endpoint_arg(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project_root = _make_built_plugin_root(tmp_path, "opencode")
    result = _run(
        "opencode",
        [str(project_root), ENDPOINT],
        home=home,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads((home / ".config" / "opencode" / "opencode.json").read_text())
    endpoints = [entry[1]["endpoint"] for entry in config["plugin"]]
    assert endpoints == [ENDPOINT]


def test_kilo_endpoint_arg(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project_root = _make_built_plugin_root(tmp_path, "kilo")
    result = _run(
        "kilo",
        [str(project_root), ENDPOINT],
        home=home,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads((home / ".config" / "kilo" / "opencode.json").read_text())
    endpoints = [entry[1]["endpoint"] for entry in config["plugin"]]
    assert endpoints == [ENDPOINT]


@pytest.mark.parametrize("name", ["opencode", "kilo"])
def test_plugin_disable_preserves_foreign_collector_and_user_plugins(tmp_path, name):
    project_root = _make_built_plugin_root(tmp_path, name)
    config = tmp_path / ".config" / name / "opencode.json"
    config.parent.mkdir(parents=True)
    foreign = [
        f"/other/install/plugins/{name}/dist/index.js",
        {"endpoint": "https://foreign.example/v1/logs", "token": "foreign-secret"},
    ]
    matching = [
        str(project_root / "plugins" / name / "dist" / "index.js"),
        {"endpoint": ENDPOINT},
    ]
    config.write_text(
        json.dumps({"plugin": [foreign, matching, "user-plugin"], "keep": 1})
    )

    result = _run(
        name,
        [str(project_root), "--disable", ENDPOINT],
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(config.read_text()) == {
        "plugin": [foreign, "user-plugin"],
        "keep": 1,
    }
    assert "foreign-secret" not in result.stdout + result.stderr
    before = config.read_bytes()
    result = _run(
        name,
        [str(project_root), "--disable", ENDPOINT],
        tmp_path,
    )
    assert result.returncode == 2, result.stderr
    assert config.read_bytes() == before


@pytest.mark.parametrize("name", ["opencode", "kilo"])
@pytest.mark.parametrize("matching", [False, True])
def test_plugin_disable_checks_runtime_default_for_bare_entry(tmp_path, name, matching):
    project_root = _make_built_plugin_root(tmp_path, name)
    config = tmp_path / ".config" / name / "opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {"plugin": [str(project_root / "plugins" / name / "dist" / "index.js")]}
        )
    )
    before = config.read_bytes()
    endpoint = "http://localhost:4005/v1/logs" if matching else ENDPOINT

    result = _run(
        name,
        [str(project_root), "--disable", endpoint],
        tmp_path,
    )

    assert result.returncode == (0 if matching else 2), result.stderr
    if matching:
        assert "plugin" not in json.loads(config.read_text())
    else:
        assert config.read_bytes() == before


def test_codex_disable_preserves_other_telemetry_and_uses_actual_endpoint(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("""model = "test"
[model_providers.custom]
endpoint = "https://provider.example/v1"
[otel]
environment = "production"
log_user_prompt = false
exporter = { otlp-http = { endpoint = "https://api.example.com:4005/v1/logs", protocol = "json", timeout_ms = 8000, headers = { "x-tokenage-token" = "secret", "x-custom" = "keep" } }, otlp-grpc = { endpoint = "https://other.example" } }
[otel.trace_exporter.otlp-http]
endpoint = "https://traces.example"
[otel_extra]
keep = true
""")
    before = tomllib.loads(config.read_text())
    result = _run("codex", [str(config), "--disable", ENDPOINT], tmp_path)
    assert result.returncode == 0, result.stderr
    after = tomllib.loads(config.read_text())
    before["otel"]["exporter"].pop("otlp-http")
    assert after == before
    assert 'endpoint = "https://provider.example/v1"' in config.read_text()


def test_codex_disable_nested_preserves_user_trace_options(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(f'''[otel]
environment = "production"
[otel.exporter.otlp-http]
endpoint = "{ENDPOINT}"
protocol = "json"
timeout_ms = 8000
headers = {{ "x-tokenage-token" = "secret", "x-custom" = "keep" }}
[otel.trace_exporter.otlp-http]
endpoint = "https://traces.example"
''')
    result = _run("codex", [str(config), "--disable", ENDPOINT], tmp_path)
    assert result.returncode == 0, result.stderr
    after = tomllib.loads(config.read_text())
    assert after["otel"]["exporter"] == "none"
    assert (
        after["otel"]["trace_exporter"]["otlp-http"]["endpoint"]
        == "https://traces.example"
    )


def test_codex_disable_skips_foreign_actual_endpoint(tmp_path):
    config = tmp_path / "config.toml"
    content = f'''[other]
endpoint = "{ENDPOINT}"
[otel]
exporter = {{ otlp-http = {{ endpoint = "https://foreign.example/v1/logs" }} }}
'''
    config.write_text(content)
    result = _run("codex", [str(config), "--disable", ENDPOINT], tmp_path)
    assert result.returncode == 2
    assert config.read_text() == content


@pytest.mark.parametrize(
    "current", [None, "https://user:secret@collector.example/v1/logs?token=secret"]
)
def test_claude_disable_requires_actual_matching_endpoint_without_echoing_secrets(
    tmp_path, current
):
    settings = tmp_path / "settings.json"
    env = {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://other.example",
    }
    if current:
        env["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] = current
    content = json.dumps({"env": env})
    settings.write_text(content)
    result = _run("claude", [str(settings), "--disable", ENDPOINT], tmp_path)
    assert result.returncode == 2
    assert "secret" not in result.stdout + result.stderr
    assert settings.read_text() == content


@pytest.mark.parametrize("agent", ["opencode", "kilo"])
@pytest.mark.parametrize("edit", ["valid", "invalid"])
def test_plugin_build_rereads_concurrent_config_edits(
    tmp_path, monkeypatch, agent, edit
):
    from client.agents import plugin

    root = _make_built_plugin_root(tmp_path, agent)
    (root / "plugins" / agent / "dist" / "index.js").unlink()
    config_path = tmp_path / ".config" / agent / "opencode.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"model": "old"}))
    concurrent = (
        json.dumps(
            {
                "model": "new",
                "plugin": ["user-plugin"],
                "provider": {"custom": {"keep": True}},
            }
        )
        if edit == "valid"
        else "{user-edit"
    )

    def build(args, plugin_dir):
        config_path.write_text(concurrent)
        (plugin_dir / "dist" / "index.js").write_text("built plugin")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(plugin, "run_npm", build)
    assert AGENTS[agent].configure(root, ENDPOINT) == (0 if edit == "valid" else 1)
    if edit == "valid":
        after = json.loads(config_path.read_text())
        assert after["model"] == "new"
        assert after["provider"] == {"custom": {"keep": True}}
        assert after["plugin"][0] == "user-plugin"
        assert len(after["plugin"]) == 2
    else:
        assert config_path.read_text() == concurrent


def test_compact_endpoint_input_and_no_claude_hook_registration(tmp_path):
    settings = tmp_path / "settings.json"
    result = _run("claude", [str(settings), ENDPOINT], tmp_path)
    assert result.returncode == 0, result.stderr
    after = json.loads(settings.read_text())
    assert after["env"]["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] == ENDPOINT
    assert "hooks" not in after


@pytest.mark.parametrize("agent", ["claude", "codex", "opencode", "kilo"])
def test_configure_helpers_do_not_echo_endpoint_credentials(tmp_path, agent):
    endpoint = "https://user:secret@collector.example/secret/v1/logs?token=secret"
    if agent in {"opencode", "kilo"}:
        target = _make_built_plugin_root(tmp_path, agent)
        script = agent
    else:
        target = tmp_path / ("config.toml" if agent == "codex" else "settings.json")
        script = agent
    result = _run(script, [str(target), endpoint], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "secret" not in result.stdout + result.stderr


@pytest.mark.parametrize("agent", ["claude", "codex", "opencode", "kilo"])
def test_disable_without_expected_collector_preserves_config(tmp_path, agent):
    if agent in {"opencode", "kilo"}:
        target = _make_built_plugin_root(tmp_path, agent)
        path = tmp_path / ".config" / agent / "opencode.json"
        content = json.dumps(
            {
                "plugin": [
                    [f"/old/plugins/{agent}/dist/index.js", {"endpoint": ENDPOINT}]
                ],
                "model": "keep",
            }
        )
        script = agent
    elif agent == "codex":
        target = path = tmp_path / "config.toml"
        content = f'[otel.exporter.otlp-http]\nendpoint = "{ENDPOINT}"\n'
        script = "codex"
    else:
        target = path = tmp_path / "settings.json"
        content = json.dumps(
            {"env": {"OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": ENDPOINT}, "model": "keep"}
        )
        script = "claude"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    result = _run(script, [str(target), "--disable"], tmp_path)
    assert result.returncode == 2, result.stderr
    assert "collector unknown" in result.stderr
    assert path.read_text() == content


@pytest.mark.parametrize("agent", ["claude", "opencode", "kilo"])
@pytest.mark.parametrize(
    "content",
    ['{"secret":', "[]", "null", '{"plugin": "user-plugin", "env": "user-env"}'],
)
@pytest.mark.parametrize("disable", [False, True])
def test_json_config_errors_preserve_original_file(tmp_path, agent, content, disable):
    if agent == "claude":
        target = path = tmp_path / "settings.json"
        script = "claude"
    else:
        target = _make_built_plugin_root(tmp_path, agent)
        path = tmp_path / ".config" / agent / "opencode.json"
        script = agent
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    args = [str(target), "--disable", ENDPOINT] if disable else [str(target), ENDPOINT]
    result = _run(script, args, tmp_path)
    assert result.returncode == 1, result.stderr
    assert "left unchanged" in result.stderr
    assert "Traceback" not in result.stderr
    assert "secret" not in result.stderr
    assert path.read_text() == content


@pytest.mark.parametrize("prefix", ["", "[otel.exporter]\n"])
def test_codex_disable_supports_implicit_otel_parent(tmp_path, prefix):
    path = tmp_path / "config.toml"
    path.write_text(
        f'{prefix}[otel.exporter.otlp-http]\nendpoint = "{ENDPOINT}"\n[profiles.work]\nmodel = "keep"\n'
    )
    result = _run("codex", [str(path), "--disable", ENDPOINT], tmp_path)
    assert result.returncode == 0, result.stderr
    assert tomllib.loads(path.read_text()) == {
        "otel": {"exporter": "none"},
        "profiles": {"work": {"model": "keep"}},
    }
    result = _run("codex", [str(path), ENDPOINT], tmp_path)
    assert result.returncode == 0, result.stderr
    assert (
        tomllib.loads(path.read_text())["otel"]["exporter"]["otlp-http"]["endpoint"]
        == ENDPOINT
    )
