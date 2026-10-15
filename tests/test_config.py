import runpy
import threading
from pathlib import Path

import pytest


def test_load_config_expands_database_path(config_module, tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path.write_text(
        """
server:
  host: 127.0.0.1
db:
  path: ~/.tokenage/usage.db
""",
        encoding="utf-8",
    )

    config = config_module.load_config(str(config_path))

    assert config["db"]["path"] == str(Path(tmp_path, ".tokenage/usage.db"))
    assert config["db"]["url"] == f"sqlite:///{Path(tmp_path, '.tokenage/usage.db')}"


def test_load_config_sets_default_models_mapping(config_module, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
server:
  host: 127.0.0.1
""",
        encoding="utf-8",
    )

    config = config_module.load_config(str(config_path))

    assert config["models"] == {}


def test_load_config_rejects_nonpositive_record_cap(config_module, tmp_path):
    """A 0/negative record cap would silently drop every export; default wins."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
server:
  host: 127.0.0.1
otlp:
  max_records_per_request: 0
""",
        encoding="utf-8",
    )

    config = config_module.load_config(str(config_path))

    assert config["otlp"]["max_records_per_request"] == 1_000


def test_load_config_keeps_database_url(config_module, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
server:
  host: 127.0.0.1
db:
  url: postgresql+psycopg://user:pass@db.example.edu:5432/tokenage
""",
        encoding="utf-8",
    )

    config = config_module.load_config(str(config_path))

    assert (
        config["db"]["url"]
        == "postgresql+psycopg://user:pass@db.example.edu:5432/tokenage"
    )


def test_get_config_path_prefers_env_override(config_module, tmp_path, monkeypatch):
    config_path = tmp_path / "custom-config.yaml"
    monkeypatch.setenv("TOKENAGE_CONFIG", str(config_path))

    assert config_module.get_config_path() == str(config_path)


def test_get_tracker_home_prefers_env_override(config_module, tmp_path, monkeypatch):
    tracker_home = tmp_path / "tracker-home"
    monkeypatch.setenv("TOKENAGE_HOME", str(tracker_home))

    assert config_module.get_tracker_home() == str(tracker_home)


def test_resolve_all_costs_parses_model_costs(config_module, pricing_maps_module):
    resolved = pricing_maps_module.resolve_all_costs(
        {
            "models": {
                "alpha-1": {"cost": {"input": 1.5, "output": 2.5, "cacheRead": 0.15}},
                "beta-1": {},
            },
        }
    )

    assert resolved["alpha-1"].cost == pricing_maps_module.ModelCost(
        input=1.5,
        output=2.5,
        cache_read=0.15,
    )
    assert "beta-1" not in resolved


def test_resolve_all_costs_preserves_cache_write_metadata(
    config_module, pricing_maps_module
):
    resolved = pricing_maps_module.resolve_all_costs(
        {
            "models": {
                "alpha-1": {
                    "cost": {
                        "input": 1.5,
                        "output": 2.5,
                        "cacheRead": 0.15,
                        "cacheWrite": 1.875,
                    }
                }
            },
        }
    )

    assert resolved["alpha-1"].cost == pricing_maps_module.ModelCost(
        input=1.5,
        output=2.5,
        cache_read=0.15,
        cache_write=1.875,
    )


def test_resolve_all_costs_normalizes_model_keys_to_lowercase(
    config_module, pricing_maps_module
):
    resolved = pricing_maps_module.resolve_all_costs(
        {
            "models": {
                "MiniMax-M2.7": {
                    "cost": {"input": 1.5, "output": 2.5, "cacheRead": 0.15}
                }
            },
        }
    )

    assert "MiniMax-M2.7" not in resolved
    assert resolved["minimax-m2.7"].cost == pricing_maps_module.ModelCost(
        input=1.5,
        output=2.5,
        cache_read=0.15,
    )


def test_refresh_runtime_config_updates_globals_in_place(
    config_module, tmp_path, monkeypatch, pricing_maps_module
):
    config_path = tmp_path / "config.yaml"
    monkeypatch.setenv("HOME", str(tmp_path))
    config_path.write_text(
        """
pricing:
  auto_fetch: false
server:
  host: 127.0.0.1
models:
  alpha-1:
    cost:
      input: 1.0
      output: 2.0
      cacheRead: 0.1
""",
        encoding="utf-8",
    )

    config_id = id(config_module.CONFIG)

    refreshed = config_module.refresh_runtime_config(str(config_path))

    assert id(config_module.CONFIG) == config_id
    assert refreshed is config_module.CONFIG
    assert config_module.CONFIG["server"]["host"] == "127.0.0.1"
    assert pricing_maps_module.MODEL_COSTS["alpha-1"] == pricing_maps_module.ModelCost(
        input=1.0,
        output=2.0,
        cache_read=0.1,
    )


def test_merge_missing_config_defaults_backfills_missing_fields(config_module):
    user_config = {
        "models": {
            "gpt-5": {
                "cost": {
                    "input": 9.0,
                }
            }
        },
        "server": {
            "api_port": 4100,
            "otlp_port": 4101,  # stale: OTLP is mounted in the API now
        },
    }
    default_config = {
        "models": {
            "gpt-5": {
                "cost": {
                    "input": 1.25,
                    "output": 10.0,
                    "cacheRead": 0.125,
                }
            },
            "gpt-5-mini": {
                "cost": {
                    "input": 0.25,
                    "output": 2.0,
                    "cacheRead": 0.025,
                }
            },
        },
        "server": {
            "host": "127.0.0.1",
            "api_port": 4001,
        },
        "db": {
            "path": "~/.tokenage/usage.db",
        },
    }

    merged_config = config_module.merge_missing_config_defaults(
        user_config, default_config
    )

    assert merged_config["models"]["gpt-5"]["cost"]["input"] == 9.0
    assert merged_config["models"]["gpt-5"]["cost"]["output"] == 10.0
    assert merged_config["models"]["gpt-5"]["cost"]["cacheRead"] == 0.125
    assert merged_config["models"]["gpt-5-mini"]["cost"]["output"] == 2.0
    assert merged_config["server"]["api_port"] == 4100
    assert merged_config["server"]["host"] == "127.0.0.1"
    assert merged_config["server"]["otlp_port"] == 4101  # kept, never used
    assert merged_config["db"]["path"] == "~/.tokenage/usage.db"


def test_load_config_ignores_stale_otlp_port(config_module, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "server:\n  api_port: 4001\n  otlp_port: 4002\n", encoding="utf-8"
    )

    config = config_module.load_config(str(config_path))

    assert config["server"]["api_port"] == 4001


def test_gunicorn_configs_resolve_log_paths_to_repo_root(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    config_dir = tmp_path / ".tokenage"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        """
server:
  host: 127.0.0.1
  api_port: 4001
db:
  path: usage.db
models: {}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    namespace = runpy.run_path(str(repo_root / "src" / "config" / "api.conf.py"))
    assert namespace["ROOT"] == str(repo_root)


def test_replace_contents_updates_keys(config_module):
    target = {"a": 1, "b": 2, "c": 3}
    source = {"a": 10, "b": 20, "d": 40}
    config_module._replace_contents(target, source)
    assert target == {"a": 10, "b": 20, "d": 40}
    assert "c" not in target


def test_replace_contents_empty_source(config_module):
    target = {"a": 1, "b": 2}
    source = {}
    config_module._replace_contents(target, source)
    assert target == {}


def test_replace_contents_no_stale_keys(config_module):
    target = {"a": 1}
    source = {"a": 2}
    config_module._replace_contents(target, source)
    assert target == {"a": 2}


def test_replace_contents_concurrent_readers_never_see_empty(config_module):
    target = {f"key{i}": i for i in range(100)}
    errors = []

    def reader():
        for _ in range(1000):
            if not target:
                errors.append("saw empty dict")
                return

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()

    new_source = {f"key{i}": i * 10 for i in range(50, 150)}
    config_module._replace_contents(target, new_source)

    for thread in threads:
        thread.join()

    assert not errors


def test_load_config_auth_defaults(config_module, tmp_path, monkeypatch):
    monkeypatch.delenv("TOKENAGE_AUTH__GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("TOKENAGE_AUTH__GOOGLE_CLIENT_SECRET", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server: {}\n", encoding="utf-8")

    config = config_module.load_config(str(config_path))

    assert config["auth"]["provider"] == "local"
    assert config["auth"]["allowlist"] == []
    assert config["auth"]["google_client_id"] == ""
    assert config["auth"]["google_client_secret"] == ""


@pytest.mark.parametrize(
    "auth_yaml,provider",
    [
        ("auth: {}\n", "local"),
        ("auth:\n  provider: google\n", "google"),
    ],
)
def test_load_config_auth_provider_mapping(
    config_module, tmp_path, auth_yaml, provider
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(auth_yaml, encoding="utf-8")

    assert config_module.load_config(str(config_path))["auth"]["provider"] == provider


def test_load_config_rejects_unknown_auth_provider(config_module, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("auth:\n  provider: github\n", encoding="utf-8")

    with pytest.raises(ValueError, match="auth.provider"):
        config_module.load_config(str(config_path))


def test_load_config_google_creds_prefer_env(config_module, tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "auth:\n  provider: google\n  google_client_id: from-yaml\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TOKENAGE_AUTH__GOOGLE_CLIENT_ID", "from-env")
    monkeypatch.setenv("TOKENAGE_AUTH__GOOGLE_CLIENT_SECRET", "from-env-secret")

    config = config_module.load_config(str(config_path))

    assert config["auth"]["provider"] == "google"
    assert config["auth"]["google_client_id"] == "from-env"
    assert config["auth"]["google_client_secret"] == "from-env-secret"


def test_load_config_reads_google_creds_from_yaml(config_module, tmp_path, monkeypatch):
    monkeypatch.delenv("TOKENAGE_AUTH__GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("TOKENAGE_AUTH__GOOGLE_CLIENT_SECRET", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "auth:\n  google_client_id: from-yaml\n  google_client_secret: from-yaml\n",
        encoding="utf-8",
    )

    config = config_module.load_config(str(config_path))

    assert config["auth"]["google_client_id"] == "from-yaml"
    assert config["auth"]["google_client_secret"] == "from-yaml"
