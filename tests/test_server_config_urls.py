from src.config.server_config import resolve_server_urls


def test_resolve_server_urls_uses_scheme_and_configured_ports():
    urls = resolve_server_urls(
        {
            "server": {
                "base_url": "https://tracker.example:8443/path",
                "api_port": 4004,
                "otlp_port": 4005,
            }
        }
    )

    assert urls == {
        "api_url": "https://tracker.example:4004",
        "otlp_url": "https://tracker.example:4005",
    }


def test_resolve_server_urls_falls_back_for_malformed_base_url():
    urls = resolve_server_urls(
        {
            "server": {
                "base_url": "https://",
                "host": "10.0.0.8",
                "api_port": 4104,
                "otlp_port": 4105,
            }
        }
    )

    assert urls == {
        "api_url": "http://10.0.0.8:4104",
        "otlp_url": "http://10.0.0.8:4105",
    }


def test_resolve_server_urls_maps_ipv6_wildcard_to_localhost():
    urls = resolve_server_urls(
        {
            "server": {
                "host": "::",
                "api_port": 4001,
                "otlp_port": 4002,
            }
        }
    )

    assert urls == {
        "api_url": "http://localhost:4001",
        "otlp_url": "http://localhost:4002",
    }


def test_resolve_server_urls_preserves_ipv6_loopback():
    urls = resolve_server_urls(
        {
            "server": {
                "host": "::1",
                "api_port": 4001,
                "otlp_port": 4002,
            }
        }
    )

    assert urls == {
        "api_url": "http://[::1]:4001",
        "otlp_url": "http://[::1]:4002",
    }
