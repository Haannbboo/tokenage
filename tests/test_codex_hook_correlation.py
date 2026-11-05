"""Codex uses the same ThreadId for SessionStart and conversation.id.

Fixture captured from Codex 0.166.0 against a local mock provider and OTLP
listener in a temporary home, without credentials. Only the shared session ID
and hook event fields were retained; the cwd is a synthetic replacement.
"""

import json
from pathlib import Path


def test_codex_hook_session_id_matches_ingested_conversation(load_module):
    otlp = load_module("src.otlp")
    fixture = json.loads(
        Path(__file__)
        .with_name("fixtures")
        .joinpath("codex-session-start.json")
        .read_text()
    )
    for service in ("codex_cli_rs", "codex_exec", "codex-app-server"):
        assert (
            otlp._usage_session_id(
                service_name=service,
                attrs=fixture["log"]["attributes"],
                resource_session_id="different-resource-id",
            )
            == fixture["hook"]["session_id"]
        )
