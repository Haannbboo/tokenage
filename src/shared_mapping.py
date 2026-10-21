"""Expiring mapping for ingest correlation state.

Read values are snapshots: callers explicitly assign after mutating them.
Only the consumer holding a user's partition lease mutates these keys.
"""

from collections.abc import Iterator, MutableMapping
from typing import Any

from .shared_state import get_shared_state


class SharedMapping(MutableMapping[str, Any]):
    def __init__(self, namespace: str):
        self.namespace = namespace

    def __getitem__(self, key: str) -> Any:
        value = get_shared_state().get(self.namespace, key)
        if value is None:
            raise KeyError(key)
        return value

    def __setitem__(self, key: str, value: Any) -> None:
        get_shared_state().set(self.namespace, key, value, 600)

    def __delitem__(self, key: str) -> None:
        get_shared_state().delete(self.namespace, key)

    def __iter__(self) -> Iterator[str]:
        return iter(get_shared_state().keys(self.namespace))

    def __len__(self) -> int:
        return len(get_shared_state().keys(self.namespace))
