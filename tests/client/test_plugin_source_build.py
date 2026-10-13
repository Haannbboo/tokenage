from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from client.agents import claude, kilo, opencode

ROOT = Path(__file__).resolve().parents[2]
AGENTS = {"opencode": opencode, "kilo": kilo}


def _entry(dist_dir, agent):
    """OpenCode registers the built directory (so v1 and v2 both load it); Kilo
    registers ``index.js``."""
    return str(dist_dir if agent == "opencode" else dist_dir / "index.js")


@pytest.mark.parametrize("agent", ["opencode", "kilo"])
def test_hosted_plugin_builds_from_source_with_npm(agent, tmp_path, monkeypatch):
    plugin_dir = tmp_path / "snapshot" / "plugins" / agent
    plugin_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "plugins" / agent / "package.json", plugin_dir / "package.json")
    shutil.copy2(
        ROOT / "plugins" / agent / "package-lock.json",
        plugin_dir / "package-lock.json",
    )
    assert not (plugin_dir / "dist").exists()

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    npm = fake_bin / "npm"
    npm.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "with open(os.environ['TOKENAGE_NPM_LOG'], 'a') as log:\n"
        "    log.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:] == ['ci']:\n"
        "    (pathlib.Path.cwd() / 'node_modules').mkdir()\n"
        "elif sys.argv[1:] == ['run', 'build']:\n"
        "    dist = pathlib.Path.cwd() / 'dist'\n"
        "    dist.mkdir()\n"
        "    (dist / 'index.js').write_text('export default {}\\n')\n"
        "else:\n"
        "    raise SystemExit(2)\n"
    )
    npm.chmod(0o755)
    npm_log = tmp_path / "npm.log"

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.setenv("TOKENAGE_NPM_LOG", str(npm_log))
    assert (
        AGENTS[agent].configure(tmp_path / "snapshot", "https://example.test/v1/logs")
        == 0
    )
    assert npm_log.read_text().splitlines() == ["ci", "run build"]
    assert (plugin_dir / "dist" / "index.js").is_file()
    config_path = home / ".config" / agent / "opencode.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["plugin"] == [
        [
            _entry(plugin_dir / "dist", agent),
            {"endpoint": "https://example.test/v1/logs"},
        ]
    ]


@pytest.mark.parametrize("agent", ["opencode", "kilo"])
def test_hosted_login_removes_old_server_plugin_and_token(agent, tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    plugin_dir = snapshot / "plugins" / agent
    plugin_dir.mkdir(parents=True)
    dist_dir = plugin_dir / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.js").write_text("export default {}\n")
    home = tmp_path / "home"
    config_path = home / ".config" / agent / "opencode.json"
    config_path.parent.mkdir(parents=True)
    old_path = f"/old/versions/aaaa/plugins/{agent}/dist/index.js"
    config_path.write_text(
        json.dumps(
            {
                "plugin": [
                    [
                        old_path,
                        {"endpoint": "https://old.test/v1/logs", "token": "old-secret"},
                    ],
                    "other-plugin",
                ]
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    assert (
        AGENTS[agent].configure(snapshot, "https://new.test/v1/logs", "new-secret") == 0
    )
    config = json.loads(config_path.read_text())
    assert config["plugin"] == [
        "other-plugin",
        [
            _entry(plugin_dir / "dist", agent),
            {"endpoint": "https://new.test/v1/logs", "token": "new-secret"},
        ],
    ]
    assert "old-secret" not in config_path.read_text()


def test_hosted_claude_setup_does_not_register_versioned_hook(tmp_path):
    settings = tmp_path / "home" / ".claude" / "settings.json"
    for _ in range(2):
        assert claude.configure(settings, "https://new.test/v1/logs", "new-secret") == 0
    data = json.loads(settings.read_text())
    assert data["env"]["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] == (
        "https://new.test/v1/logs"
    )
    assert data["env"]["OTEL_EXPORTER_OTLP_HEADERS"] == "x-tokenage-token=new-secret"
    assert "hooks" not in data
