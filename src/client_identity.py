"""Validate optional client identity metadata at server boundaries."""

import re


def client_version(value: object) -> str | None:
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value)
        else None
    )


def client_commit(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.lower()
    return value if re.fullmatch(r"[0-9a-f]{40}", value) else None
