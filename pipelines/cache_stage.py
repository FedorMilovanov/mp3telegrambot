#!/usr/bin/env python3
"""Решение о cache-hit: актуальность записи и чистота сохранённого анализа.

База отвечает за TTL и версии генерации через ``is_cache_valid``. Эта стадия
добавляет вторую часть решения: даже актуальная запись не должна попасть в
публикацию с meta-мусором в теме, авторе, названии, событии или вопросах.

Повреждённая структура JSON (например, список вместо ``ai_data``) равнозначна
грязному кэшу: пользователь идёт по обычному fresh-пути вместо падения на
``.get``/итерации. Содержимое валидной записи не нормализуется и не мутируется.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

CacheRecord = dict[str, Any]
CacheReader = Callable[[str], Awaitable[CacheRecord | None]]
CacheValidator = Callable[[CacheRecord | None], tuple[bool, str]]
DirtyDetector = Callable[[str], bool]

#: Порядок важен: это исторический порядок проверки полей в оркестраторе.
DIRTY_AI_FIELDS = ("main_topic", "real_author", "real_title", "real_event")


@dataclass(frozen=True)
class CacheDecision:
    """Результат проверки без скрытого чтения БД или изменения записи."""

    cached: CacheRecord | None
    ai_data: dict[str, Any] | None
    valid: bool
    reason: str

    @property
    def hit(self) -> bool:
        """Cache-hit возможен только с записью и непустым dict-анализом."""
        return self.valid and self.cached is not None and bool(self.ai_data)


@dataclass(frozen=True)
class CacheDeps:
    """Внешние границы; тесты подставляют фейки без SQLite и часов."""

    read: CacheReader
    validate: CacheValidator
    is_dirty: DirtyDetector


def production_deps() -> CacheDeps:
    """Боевые зависимости импортируются лениво, без DB-side-effects на import."""
    from core.database import adb_get, is_cache_valid
    from core.text_utils import _has_dirty_meta

    return CacheDeps(read=adb_get, validate=is_cache_valid, is_dirty=_has_dirty_meta)


def _dirty_decision(
    cached: CacheRecord,
    ai_data: dict[str, Any] | None,
) -> CacheDecision:
    return CacheDecision(
        cached=cached,
        ai_data=ai_data,
        valid=False,
        reason="dirty_meta",
    )


def evaluate_cache(
    cached: CacheRecord | None,
    *,
    validate: CacheValidator,
    is_dirty: DirtyDetector,
) -> CacheDecision:
    """Проверить запись в прежнем порядке: сначала версии, затем содержимое.

    ``validate`` не вызывается повторно и dirty-проверки не выполняются для
    устаревшей записи. Это сохраняет как причину промаха, так и отсутствие
    лишней работы на fresh-пути.
    """
    valid, reason = validate(cached)
    raw_ai = cached.get("ai_data") if cached else None
    ai_data = raw_ai if isinstance(raw_ai, dict) else None

    if not valid or not cached:
        return CacheDecision(cached=cached, ai_data=ai_data, valid=valid, reason=reason)

    # JSON из SQLite обычно имеет правильную форму, но ручная/старая запись не
    # должна ронять весь пайплайн. Такая запись пересоздаётся через fresh-путь.
    if not isinstance(raw_ai, dict):
        return _dirty_decision(cached, None)

    questions = cached.get("questions") or []
    if not isinstance(questions, list):
        return _dirty_decision(cached, ai_data)

    for field in DIRTY_AI_FIELDS:
        value = raw_ai.get(field, "")
        # Исторически None/0/"" означали пустое поле. truthy нестроковое
        # значение раньше падало в splitlines(); теперь это безопасный miss.
        if value and not isinstance(value, str):
            return _dirty_decision(cached, ai_data)
        if is_dirty(value or ""):
            return _dirty_decision(cached, ai_data)

    for question in questions:
        if is_dirty(str(question)):
            return _dirty_decision(cached, ai_data)

    return CacheDecision(cached=cached, ai_data=ai_data, valid=True, reason=reason)


async def load_cache(media_id: str, deps: CacheDeps | None = None) -> CacheDecision:
    """Прочитать и оценить запись; ошибки самой БД по-прежнему пробрасываются."""
    deps = deps or production_deps()
    decision = evaluate_cache(
        await deps.read(media_id),
        validate=deps.validate,
        is_dirty=deps.is_dirty,
    )
    if not decision.valid and decision.reason == "dirty_meta":
        logger.info("Кэш skip: %s reason=dirty_meta", media_id)
    return decision


__all__ = [
    "DIRTY_AI_FIELDS",
    "CacheDecision",
    "CacheDeps",
    "evaluate_cache",
    "load_cache",
    "production_deps",
]
