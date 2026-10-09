_MODEL_NAME_ALIASES = {
    "space-bunny-free": "stealth/space-bunny-alpha",
    "ling-3.1-flash-free": "inclusionai/ling-3.1-flash",
}


def normalize_model_name(model_name: str) -> str:
    """Normalize casing/whitespace and resolve confirmed model aliases.

    Unknown names keep their provider prefixes, versions, and variant suffixes.
    """
    name = model_name.strip().lower()
    return _MODEL_NAME_ALIASES.get(name, name)


def normalize_provider_name(provider_name: str) -> str:
    """Fold provider casing so names share one identity."""
    return provider_name.strip().lower()


def normalize_tool_name(tool_name: str) -> str:
    """Fold tool name casing so e.g. `Bash`/`bash` aggregate as one tool."""
    return tool_name.lower()


def replace_contents(target: dict, source: dict) -> None:
    """Update target in place to match source without a clear-then-fill window."""
    target.update(source)
    for stale in set(target) - set(source):
        del target[stale]


def secs_to_micros(secs: int | float) -> int:
    """Convert epoch seconds to integer microseconds."""
    return round(secs * 1_000_000)


def micros_to_secs(micros: int) -> float:
    """Convert integer microseconds to epoch seconds."""
    return micros / 1_000_000
