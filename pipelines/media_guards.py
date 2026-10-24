#!/usr/bin/env python3
"""Проверки, из-за которых видео не обрабатывается, — отдельно от пайплайна.

Зачем отдельный модуль: в `pipelines/main_pipeline.py` эти проверки жили внутри
`process_single_video` — функции на 2600 строк, где любая из них проверялась
только «на живом боте». Пользовательский отказ («live-трансляция», «слишком
длинное видео», «мало места») — это контракт, а не деталь реализации: текст
видит человек, а `reason` попадает в лог. Здесь он зафиксирован и покрыт тестами.

Порядок важен и сохранён в вызывающем коде: live → длительность → диск.
Дешёвые проверки идут раньше скачивания, поэтому пользователь узнаёт причину
отказа за секунды, а не после трёхминутной загрузки.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Статусы yt-dlp, при которых поток бесконечный: скачивание не завершится.
LIVE_STATUSES = frozenset({"is_live", "is_upcoming", "post_live"})

#: Ниже этого порога не начинаем скачивание: места не хватит и на видео, и на mp3.
DEFAULT_MIN_FREE_GB = 2.0

#: Ограничение длительности по умолчанию — 3 часа (Gemini обрежет конспект).
DEFAULT_MAX_DURATION_SEC = 10_800


@dataclass(frozen=True)
class Skip:
    """Причина отказа обработать видео.

    ``reason`` — стабильный машинный идентификатор (для логов и метрик),
    ``message`` — текст для пользователя, ``details`` — то, что полезно в логе,
    но не нужно в сообщении (например, «7200s > 3600s»).
    """

    reason: str
    message: str
    details: str = ""


def env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    """Прочитать целое из окружения, не падая на опечатке.

    Раньше `int(os.getenv("MAX_VIDEO_DURATION_SEC", "10800"))` на значении вида
    `3 часа` бросал ValueError прямо в пайплайне: пользователь получал ошибку
    обработки вместо понятного отказа, а причина была только в логе.
    """
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r — не целое число, использую %s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning("%s=%s меньше минимума %s, использую %s", name, value, minimum, default)
        return default
    return value


def info_timeout_seconds(raw: str | None = None) -> int:
    """Таймаут получения метаданных: по умолчанию 180 с, в разумных границах.

    Раньше выражение `max(60, min(int(...), 900))` повторялось в пайплайне и не
    было покрыто тестом.
    """
    if raw is None:
        raw = os.getenv("YTDLP_INFO_TIMEOUT", "180")
    try:
        value = int(str(raw).strip() or 180)
    except ValueError:
        return 180
    return max(60, min(value, 900))


def is_live(info: dict[str, Any] | None) -> bool:
    """Трансляция ещё идёт (или вот-вот начнётся) — скачивать нельзя."""
    if not isinstance(info, dict):
        return False
    if info.get("is_live"):
        return True
    return str(info.get("live_status") or "") in LIVE_STATUSES


def live_skip(info: dict[str, Any] | None) -> Skip | None:
    if not is_live(info):
        return None
    return Skip(
        reason="live-stream",
        message=(
            "⚠️ Live-трансляции не поддерживаются. "
            "Дождитесь окончания и загрузите запись."
        ),
        details=str((info or {}).get("live_status") or "is_live"),
    )


def duration_skip(duration_sec: float | None, max_sec: int) -> Skip | None:
    """Видео длиннее лимита.

    Неизвестная длительность (`None`, `0`) не блокирует обработку: yt-dlp не
    всегда отдаёт `duration`, и отказ на пустом месте хуже, чем попытка.
    """
    try:
        duration = int(duration_sec or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0 or duration <= max_sec:
        return None
    return Skip(
        reason="too-long",
        message=(
            f"⚠️ Видео {duration // 60} мин — превышает лимит "
            f"{max_sec // 60} мин. Скиньте видео покороче."
        ),
        details=f"{duration}s > {max_sec}s",
    )


def disk_skip(free_gb: float | None, min_gb: float = DEFAULT_MIN_FREE_GB) -> Skip | None:
    """Мало свободного места.

    ``None`` означает «не смогли узнать» (нет каталога, нет прав) — в этом
    случае не блокируем: проверка не должна превращаться в запрет.
    """
    if free_gb is None:
        return None
    if free_gb >= min_gb:
        return None
    return Skip(
        reason="disk-low",
        message=(
            f"⚠️ Мало места на диске ({free_gb:.1f} ГБ свободно). "
            "Освободите место и повторите."
        ),
        details=f"{free_gb:.1f} GB < {min_gb:.1f} GB",
    )


def free_gb(path: str | Path | None) -> float | None:
    """Свободно гигабайт на диске с этим путём; ``None`` если узнать нельзя."""
    if not path:
        return None
    try:
        return shutil.disk_usage(str(path)).free / (1024**3)
    except OSError:
        return None


def lowest_free_gb(*paths: str | Path | None) -> float | None:
    """Минимум по нескольким каталогам.

    ENG-режимы пишут видео в `%TEMP%` (ld_work), а mp3 — в `DOWNLOAD_DIR`; они
    могут быть на разных дисках, и «места хватает» надо считать по худшему.
    """
    values = [value for value in (free_gb(path) for path in paths) if value is not None]
    return min(values) if values else None


def temp_dir() -> str:
    return tempfile.gettempdir()


def first_entry(info: Any) -> dict[str, Any] | None:
    """Нормализовать ответ yt-dlp «плейлист-подобного вида».

    yt-dlp иногда возвращает контейнер `{"entries": [...]}` даже с
    `--no-playlist` (например, для ссылки на канал). Берём первую реальную
    запись; пустой контейнер оставляем как есть, чтобы вызывающий код сказал
    «не удалось получить метаданные», а не «видео не найдено».
    """
    if not isinstance(info, dict):
        return None
    if info.get("entries") and not info.get("id"):
        entries = [entry for entry in (info.get("entries") or []) if entry]
        return entries[0] if entries else info
    return info


def parse_info_stdout(stdout: str | None) -> dict[str, Any] | None:
    """Достать JSON метаданных из вывода `yt-dlp --dump-json`.

    yt-dlp печатает предупреждения в stderr, но некоторые обёртки/плагины
    пишут их и в stdout — поэтому ищем первую строку, начинающуюся с `{`,
    и пробуем разобрать её; битые строки пропускаем, а не падаем.
    """
    for line in str(stdout or "").strip().splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None
