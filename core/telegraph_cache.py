#!/usr/bin/env python3
"""Shared thread-safe Telegraph HTML cache and invalidation contract."""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Generic, Hashable, TypeVar

logger = logging.getLogger(__name__)


_KT = TypeVar("_KT", bound=Hashable)
_VT = TypeVar("_VT")


class _LRUCache(Generic[_KT, _VT]):
    """Потокобезопасный LRU-кеш на базе OrderedDict (композиция)."""

    def __init__(self, maxsize: int = 64) -> None:
        self._data: OrderedDict[_KT, _VT] = OrderedDict()
        self._max = maxsize
        self._lock = threading.Lock()

    def get_cached(self, key: _KT) -> _VT | None:
        """Возвращает значение по ключу или None, сдвигая в конец."""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        return None

    def put(self, key: _KT, value: _VT) -> None:
        """Добавляет элемент, вытесняя старейший при переполнении."""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear_safe(self) -> None:
        """Thread-safe полная очистка."""
        with self._lock:
            self._data.clear()

    def delete(self, key: _KT) -> bool:
        """Thread-safe удаление одного ключа. Возвращает True если ключ был в кеше."""
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
        return False


_TELEGRAPH_CACHE_SIZE: int = int(os.environ.get("PDF_TELEGRAPH_CACHE_SIZE", "64"))
_TELEGRAPH_CACHE: _LRUCache[str, str] = _LRUCache(_TELEGRAPH_CACHE_SIZE)


def clear_telegraph_cache() -> None:
    """Thread-safe очистка кеша Telegraph-страниц."""
    _TELEGRAPH_CACHE.clear_safe()


def invalidate_telegraph_url(url: str) -> None:
    """#105: инвалидирует одну запись кеша Telegraph по URL (после успешного editPage)."""
    removed = _TELEGRAPH_CACHE.delete(url)
    if removed:
        logger.debug("Telegraph cache invalidated for URL: %s", url)


__all__ = ["clear_telegraph_cache", "invalidate_telegraph_url"]
