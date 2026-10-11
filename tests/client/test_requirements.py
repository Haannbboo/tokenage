"""The client installs on machines that have no server, so it stays light."""

import re
from pathlib import Path

import tomllib


def test_client_dependencies_stay_small() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "client" / "pyproject.toml"
    dependencies = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    names = {re.split(r"[<>=\[ ]", dep, maxsplit=1)[0].lower() for dep in dependencies}
    assert names == {"httpx", "pydantic", "pyyaml", "starlette", "uvicorn"}
