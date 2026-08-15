#!/usr/bin/env python3
"""Small bounded dict-compatible LRU for long-lived process caches."""
from __future__ import annotations

from collections import OrderedDict
from typing import Any


class BoundedLRUDict(OrderedDict):
    """Dict-compatible LRU that evicts the oldest entry above ``max_entries``."""

    def __init__(self, *args: Any, max_entries: int = 256, **kwargs: Any) -> None:
        self.max_entries = max(8, int(max_entries))
        super().__init__()
        self.update(*args, **kwargs)
        self._trim()

    def __getitem__(self, key: Any) -> Any:
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def get(self, key: Any, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        self._trim()

    def _trim(self) -> None:
        while len(self) > self.max_entries:
            self.popitem(last=False)


__all__ = ["BoundedLRUDict"]
