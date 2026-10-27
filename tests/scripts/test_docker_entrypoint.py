"""Mounted configuration is immutable and failed migrations must prevent startup."""

import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "docker" / "entrypoint.sh"


@pytest.mark.parametrize("migration_status", [0, 1])
def test_entrypoint_migration_gate_and_mounted_config(tmp_path, migration_status):
    config = tmp_path / "config.yaml"
    original = "# operator config\nserver:\n  host: 127.0.0.1\n"
    config.write_text(original)
    config.chmod(0o444)
    python = tmp_path / "python"
    python.write_text(f"#!/bin/sh\nexit {migration_status}\n")
    python.chmod(0o755)
    started = tmp_path / "started"
    result = subprocess.run(
        ["sh", str(ENTRYPOINT), "touch", str(started)],
        env={
            **os.environ,
            "TOKENAGE_CONFIG": str(config),
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == migration_status
    assert started.exists() == (migration_status == 0)
    assert config.read_text() == original
