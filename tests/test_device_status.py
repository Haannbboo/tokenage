"""Device status reporting: POST/GET /devices/status storage and scoping."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

INSTALLATION_KEY = "k" * 43
ENDPOINT = "http://localhost:4005/v1/logs"

CLAUDE = {
    "configured": True,
    "endpoint_matches": True,
    "configured_endpoint": ENDPOINT,
    "expected_endpoint": ENDPOINT,
    "status": "ready",
}
CODEX = {
    "configured": False,
    "endpoint_matches": False,
    "configured_endpoint": None,
    "expected_endpoint": ENDPOINT,
    "status": "missing_config",
}


def _report(**overrides) -> dict:
    report = {
        "device_name": "laptop",
        "client_version": "0.1.200",
        "client_commit": "a" * 40,
        "collected_at": 1770000000,
        "agents": {"claude": dict(CLAUDE), "codex": dict(CODEX)},
        "detected": {"claude": {"found": True}},
    }
    report.update(overrides)
    return report


def _enable_auth(monkeypatch) -> None:
    import src.config.app

    monkeypatch.setitem(
        src.config.app.CONFIG, "auth", {"provider": "google", "allowlist": []}
    )


def _device_client(api_module, fresh_db, email="a@example.com", key="d" * 43):
    """A client carrying a device bearer token; returns (client, user, device)."""
    from src.auth.tokens import mint_device_tokens, mint_token

    _, user = mint_token(email, kind="web", db_path=fresh_db.db_path)
    cli_token, _, device = mint_device_tokens(
        user.id, key, "laptop", db_path=fresh_db.db_path
    )
    client = TestClient(api_module.app)
    client.headers["Authorization"] = f"Bearer {cli_token}"
    return client, user, device


def test_post_persists_and_get_returns_the_report(api_module, fresh_db):
    client, _, device = _device_client(api_module, fresh_db)
    assert client.get("/devices/status").json()["devices"] == []
    assert client.post("/devices/status", json=_report()).status_code == 204

    devices = client.get("/devices/status").json()["devices"]
    assert len(devices) == 1
    entry = devices[0]
    assert entry["device_id"] == device.id
    assert entry["device_name"] == "laptop"
    assert entry["client_version"] == "0.1.200"
    assert entry["client_commit"] == "a" * 40
    assert isinstance(entry["reported_at"], int) and entry["reported_at"] > 0
    assert entry["status"]["agents"]["claude"]["expected_endpoint"] == ENDPOINT
    assert entry["status"]["agents"]["claude"]["status"] == "ready"


def test_post_has_no_installation_key_path(api_module, fresh_db):
    """Tokenless callers are rejected and a body installation_key is ignored."""
    anonymous = TestClient(
        api_module.app, client=("203.0.113.9", 5), base_url="http://tracker.example"
    )
    assert (
        anonymous.post(
            "/devices/status", json=_report(installation_key=INSTALLATION_KEY)
        ).status_code
        == 401
    )

    client, _, device = _device_client(api_module, fresh_db)
    assert (
        client.post(
            "/devices/status", json=_report(installation_key=INSTALLATION_KEY)
        ).status_code
        == 204
    )
    (entry,) = client.get("/devices/status").json()["devices"]
    assert entry["device_id"] == device.id
    assert "installation_key" not in entry["status"]


def test_post_rejects_tokens_without_a_device(api_module, fresh_db, monkeypatch):
    from src.auth.tokens import mint_token

    token, _ = mint_token("a@example.com", kind="cli", db_path=fresh_db.db_path)

    client = TestClient(api_module.app)
    client.headers["Authorization"] = f"Bearer {token}"
    assert client.post("/devices/status", json=_report()).status_code == 400


def test_loopback_owner_without_a_device_token_cannot_post(api_module, fresh_db):
    # The local owner resolves with no token, so there is no device to report as.
    assert (
        TestClient(api_module.app).post("/devices/status", json=_report()).status_code
        == 400
    )


def test_get_is_user_scoped(api_module, fresh_db, monkeypatch):
    from src.auth.tokens import mint_device_tokens, mint_token

    _enable_auth(monkeypatch)
    _, user_a = mint_token("a@example.com", kind="web", db_path=fresh_db.db_path)
    _, user_b = mint_token("b@example.com", kind="web", db_path=fresh_db.db_path)
    cli_a, _, device_a = mint_device_tokens(
        user_a.id, "a" * 43, "laptop-a", db_path=fresh_db.db_path
    )
    cli_b, _, device_b = mint_device_tokens(
        user_b.id, "b" * 43, "laptop-b", db_path=fresh_db.db_path
    )

    client_a = TestClient(api_module.app)
    client_a.headers["Authorization"] = f"Bearer {cli_a}"
    client_b = TestClient(api_module.app)
    client_b.headers["Authorization"] = f"Bearer {cli_b}"

    assert (
        client_a.post(
            "/devices/status", json=_report(device_name="laptop-a")
        ).status_code
        == 204
    )
    assert (
        client_b.post(
            "/devices/status", json=_report(device_name="laptop-b")
        ).status_code
        == 204
    )

    devices_a = client_a.get("/devices/status").json()["devices"]
    assert [(device["device_id"], device["device_name"]) for device in devices_a] == [
        (device_a.id, "laptop-a")
    ]
    devices_b = client_b.get("/devices/status").json()["devices"]
    assert [(device["device_id"], device["device_name"]) for device in devices_b] == [
        (device_b.id, "laptop-b")
    ]


def test_get_returns_401_without_a_token_when_google(api_module, fresh_db, monkeypatch):
    _enable_auth(monkeypatch)
    client = TestClient(api_module.app)
    assert client.get("/devices/status").status_code == 401
    assert client.post("/devices/status", json=_report()).status_code == 401


def test_post_accepts_agents_the_server_does_not_know(api_module, fresh_db):
    # Clients release independently; a new agent must not be rejected.
    client, _, _ = _device_client(api_module, fresh_db)
    report = _report()
    report["agents"]["gemini"] = dict(CLAUDE, status="some_new_status")
    report["detected"]["gemini"] = {"found": True}
    assert client.post("/devices/status", json=report).status_code == 204


def test_post_rejects_overlong_strings_and_oversized_maps(api_module, fresh_db):
    client, _, _ = _device_client(api_module, fresh_db)

    overlong = _report()
    overlong["agents"]["claude"]["configured_endpoint"] = "x" * 513
    assert client.post("/devices/status", json=overlong).status_code == 422

    long_name = _report()
    long_name["agents"]["x" * 33] = dict(CLAUDE)
    assert client.post("/devices/status", json=long_name).status_code == 422

    too_many = _report()
    too_many["detected"] = {f"agent{i}": {"found": True} for i in range(33)}
    assert client.post("/devices/status", json=too_many).status_code == 422


def test_get_returns_null_status_for_corrupt_stored_json(api_module, fresh_db):
    from src.auth.tokens import set_device_status

    client, _, device = _device_client(api_module, fresh_db)
    set_device_status(device.id, "not-json", db_path=fresh_db.db_path)

    devices = client.get("/devices/status").json()["devices"]
    assert len(devices) == 1
    assert devices[0]["status"] is None


def test_devices_routes_are_classified_for_the_auth_gate(api_module):
    assert "/devices" in api_module._SPA_API_PREFIXES
    api_module._assert_api_routes_classified()


@pytest.mark.parametrize(
    "metadata,expected_version,expected_commit",
    [
        ({"client_version": "0.1.201", "client_commit": "B" * 40}, "0.1.201", "b" * 40),
        ({"client_version": "bad", "client_commit": "bad"}, "0.1.200", "a" * 40),
        ({"client_version": "0.1.201", "client_commit": "bad"}, "0.1.201", "a" * 40),
        ({"client_version": "bad", "client_commit": "b" * 40}, "0.1.200", "b" * 40),
        ({}, "0.1.200", "a" * 40),
        ({"client_version": "١.٢.٣"}, "0.1.200", "a" * 40),
    ],
)
def test_status_refreshes_device_identity(
    api_module, fresh_db, metadata, expected_version, expected_commit
):
    from src.auth.tokens import list_user_devices

    client, user, _ = _device_client(api_module, fresh_db)
    assert client.post("/devices/status", json=_report()).status_code == 204
    report = _report()
    del report["client_version"], report["client_commit"]
    report.update(metadata)
    assert client.post("/devices/status", json=report).status_code == 204
    (device,) = list_user_devices(user.id, db_path=fresh_db.db_path)
    assert device.client_version == expected_version
    assert device.client_commit == expected_commit
    (entry,) = client.get("/auth/devices").json()["devices"]
    assert entry["client_version"] == expected_version
    assert entry["client_commit"] == expected_commit
    status_entry = client.get("/devices/status").json()["devices"][0]
    assert status_entry["client_version"] == expected_version
    assert status_entry["client_commit"] == expected_commit
    status = status_entry["status"]
    for key in ("client_version", "client_commit"):
        if metadata.get(key) == "bad":
            assert status[key] is None
