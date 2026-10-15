from src.config.server_config import resolve_server_urls


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
        "api_url": "https://tracker.example:4004",
        "otlp_url": "https://tracker.example:4004/otlp",
    }


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
