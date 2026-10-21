"""Expiring mapping for ingest correlation state.

Read values are snapshots: callers explicitly assign after mutating them.
Only the consumer holding a user's partition lease mutates these keys.
"""

from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

from .shared_state import get_shared_state

# One consumer thread processes a record at a time. Keep its state transitions
# private until the record's database writes succeed, so a retry sees the same
# correlation inputs (including tools from previously acknowledged events).
_pending: ContextVar[dict | None] = ContextVar("correlation_updates", default=None)
_DELETED = object()


@contextmanager
def defer_updates():
    updates = {}
    token = _pending.set(updates)
    try:
        yield
        store = get_shared_state()
        for (namespace, key), value in updates.items():
            if value is _DELETED:
                store.delete(namespace, key)
            else:
                store.set(namespace, key, value, 600)
    finally:
        _pending.reset(token)


class SharedMapping(MutableMapping[str, Any]):
    def __init__(self, namespace: str):
        self.namespace = namespace

    def __getitem__(self, key: str) -> Any:
        pending = _pending.get()
        state_key = (self.namespace, key)
        value = (
            pending[state_key]
            if pending is not None and state_key in pending
            else get_shared_state().get(self.namespace, key)
        )
        if value is None or value is _DELETED:
            raise KeyError(key)
        return deepcopy(value)

    def __setitem__(self, key: str, value: Any) -> None:
        pending = _pending.get()
        if pending is not None:
            pending[self.namespace, key] = deepcopy(value)
        else:
            get_shared_state().set(self.namespace, key, value, 600)

    def __delitem__(self, key: str) -> None:
        pending = _pending.get()
        if pending is not None:
            pending[self.namespace, key] = _DELETED
        else:
            get_shared_state().delete(self.namespace, key)

    def __iter__(self) -> Iterator[str]:
        keys = dict.fromkeys(get_shared_state().keys(self.namespace))
        for (namespace, key), value in (_pending.get() or {}).items():
            if namespace == self.namespace:
                if value is _DELETED:
                    keys.pop(key, None)
                else:
                    keys[key] = None
        return iter(keys)

    def __len__(self) -> int:
        return sum(1 for _ in self)
