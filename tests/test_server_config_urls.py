import pytest

from src.config.server_config import load_server_config, resolve_server_urls


def test_resolve_server_urls_mounts_otlp_under_the_api_and_ignores_otlp_port():
    urls = resolve_server_urls(
        {
            "server": {
                "base_url": "https://tracker.example:8443/path",
                "api_port": 4004,
                "otlp_port": 4005,  # stale key from before the merge: ignored
            }
        }
    )

    assert urls == {
        "api_url": "https://tracker.example:8443",
        "otlp_url": "https://tracker.example:8443/otlp",
    }


@pytest.mark.parametrize(
    ("base_url", "api_port", "expected"),
    [
        ("https://example.test:443", 4004, "https://example.test"),
        ("http://example.test:80", 4004, "http://example.test"),
        ("https://example.test", 443, "https://example.test"),
        ("http://example.test", 80, "http://example.test"),
        ("https://host.ts.net", 4004, "https://host.ts.net"),
        ("https://example.test:", 4004, "https://example.test"),
        ("my-nas", 4004, "http://my-nas"),
        ("my-nas:8443", 4004, "http://my-nas:8443"),
        ("https://[::1]:8443", 4004, "https://[::1]:8443"),
        ("https://[::1]", 4004, "https://[::1]"),
    ],
)
def test_resolve_server_urls_public_port(base_url, api_port, expected):
    urls = resolve_server_urls({"server": {"base_url": base_url, "api_port": api_port}})
    assert urls == {"api_url": expected, "otlp_url": f"{expected}/otlp"}


@pytest.mark.parametrize("port", ["bad", "65536"])
def test_resolve_server_urls_rejects_invalid_base_url_port(port):
    with pytest.raises(ValueError, match="server.base_url"):
        resolve_server_urls({"server": {"base_url": f"https://example.test:{port}"}})


def test_load_server_config_rejects_invalid_base_url_port(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text('server:\n  base_url: "https://example.test:bad"\n')
    monkeypatch.setattr("src.config.models.get_config_path", lambda: config)
    with pytest.raises(ValueError, match="server.base_url"):
        load_server_config()


def test_resolve_server_urls_fallback_retains_listen_port_even_if_default():
    assert (
        resolve_server_urls({"server": {"api_port": 80}})["api_url"]
        == "http://localhost:80"
    )


def test_resolve_server_urls_falls_back_for_malformed_base_url():
    urls = resolve_server_urls(
        {"server": {"base_url": "https://", "host": "10.0.0.8", "api_port": 4104}}
    )

    assert urls == {
        "api_url": "http://10.0.0.8:4104",
        "otlp_url": "http://10.0.0.8:4104/otlp",
    }


def test_resolve_server_urls_maps_ipv6_wildcard_to_localhost():
    urls = resolve_server_urls({"server": {"host": "::", "api_port": 4001}})

    assert urls == {
        "api_url": "http://localhost:4001",
        "otlp_url": "http://localhost:4001/otlp",
    }


def test_resolve_server_urls_preserves_ipv6_loopback():
    urls = resolve_server_urls({"server": {"host": "::1", "api_port": 4001}})

    assert urls == {
        "api_url": "http://[::1]:4001",
        "otlp_url": "http://[::1]:4001/otlp",
    }


def test_resolve_server_urls_rejects_malformed_ipv6():
    with pytest.raises(ValueError, match="server.base_url"):
        resolve_server_urls({"server": {"base_url": "https://[::1"}})


def test_load_server_config_accepts_valid_base_url(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text('server:\n  base_url: "https://example.test:8443"\n')
    monkeypatch.setattr("src.config.models.get_config_path", lambda: config)
    assert load_server_config().base_url == "https://example.test:8443"
