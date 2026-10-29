#!/usr/bin/env python3
"""Сборка ответов ``/healthz`` и ``/metrics`` без побочных эффектов.

Модуль намеренно чистый: все внешние факты (состояние БД, место на диске,
снимок счётчиков) передаются аргументами. Поэтому его легко тестировать, и он
не может «зависнуть» на диске в момент, когда бот и без того нездоров.

Что отдаётся наружу: только числа, версия и признак успеха. Ни одного секрета,
пути или имени пользователя — health-эндпоинт слушают внешние мониторы, а не
доверенные лица.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

#: Successful getUpdates responses are required even when no users write.
STALE_POLLING_SECONDS = 300.0
#: Обслуживание не проходило дольше двух часов (оно ежечасное) — сигнал тревоги.
STALE_MAINTENANCE_SECONDS = 7200.0
#: Ниже этого порога свободного места бот не сможет рендерить видео.
MIN_FREE_DISK_GB = 2.0


def _is_windows_drive(text: str) -> bool:
    """``C:\\`` / ``C:/`` — проверка по символам, без регулярного выражения.

    Регулярки в проекте считаются отдельной метрикой сложности
    (`tools/check_code_health.py`), и заводить шаблон ради двух сравнений
    символов незачем.
    """
    return len(text) > 2 and text[0].isalpha() and text[1] == ":" and text[2] in "\\/"


def readonly_uri(path: str | Path) -> str:
    """URI для открытия БД только на чтение, корректный и на Windows.

    Наивное ``file:C:\\bot\\cache.db?mode=ro`` SQLite на Windows трактует как
    относительный путь и открывает не ту базу. По документации путь в URI
    абсолютный и с прямыми слэшами; для диска нужен ведущий ``/``:
    ``file:/C:/bot/cache.db?mode=ro``.
    """
    text = str(path).replace("\\", "/")
    if _is_windows_drive(str(path)):
        text = "/" + text
    elif not text.startswith("/"):
        text = "/" + str(Path(path).resolve()).replace("\\", "/").lstrip("/")
    # Пробел и «#» в URI значимы (фрагмент/разделитель), поэтому кодируются;
    # слэши и двоеточие диска остаются как есть.
    return f"file:{quote(text, safe='/:')}?mode=ro"


def database_ok(db_path: str | Path) -> bool:
    """Дешёвая проверка БД: открыть, прочитать версию схемы, закрыть.

    Именно ``PRAGMA schema_version``, а не ``SELECT 1``: ``SELECT 1`` отвечает
    из константы и **не читает файл**, поэтому «здоровая» проба возвращала True
    даже на мусоре вместо базы (проверено тестом). `schema_version` читает
    заголовок, стоит микросекунды, таблиц не перебирает и в WAL-режиме не
    блокируется за писателями.

    ``mode=ro`` гарантирует, что health-проба не создаст пустую базу вместо
    отсутствующей. Пустой существующий файл SQLite считает пустой базой — это
    корректное состояние свежей установки, а не ошибка.
    """
    path = Path(db_path)
    if not path.exists():
        return False
    try:
        with sqlite3.connect(readonly_uri(path), uri=True, timeout=2.0) as conn:
            conn.execute("PRAGMA schema_version").fetchone()
        return True
    except sqlite3.Error:
        return False


def disk_free_gb(path: str | Path) -> float:
    try:
        return round(shutil.disk_usage(str(path)).free / (1024**3), 1)
    except OSError:
        return -1.0


def gemini_keys_configured() -> int:
    """Сколько ключей Gemini задано — число, не значения."""
    names = ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4")
    return sum(1 for name in names if (os.getenv(name) or "").strip())


def collect_problems(
    *,
    snapshot: dict[str, Any],
    db_ok: bool,
    free_disk_gb: float,
    keys_configured: int | None = None,
    gemini_keys_required: bool = True,
) -> list[str]:
    """Список причин, по которым бот считается нездоровым.

    Одна функция на ``/healthz`` и ``/metrics``: иначе счётчик ``mp3bot_up`` и
    код ответа ``/healthz`` рано или поздно разойдутся, и мониторинг начнёт
    показывать «всё хорошо» при падающем боте.

    ``keys_configured=None`` означает «не проверять ключи» — так вызывает
    ``/metrics``, куда список ключей не передаётся.
    """
    problems: list[str] = []

    polling_start_age = snapshot.get("polling_start_age_seconds")
    last_polling_age = snapshot.get("last_polling_age_seconds")
    polling_age = last_polling_age if last_polling_age is not None else polling_start_age
    if polling_age is not None and float(polling_age) > STALE_POLLING_SECONDS:
        problems.append("polling_stale")

    last_maintenance_age = snapshot.get("last_maintenance_age_seconds")
    if last_maintenance_age is not None and float(last_maintenance_age) > STALE_MAINTENANCE_SECONDS:
        problems.append("maintenance_stale")
    if not snapshot.get("last_maintenance_ok", True):
        problems.append("maintenance_failed")

    if not db_ok:
        problems.append("database_unavailable")
    if 0 <= free_disk_gb < MIN_FREE_DISK_GB:
        problems.append("disk_low")
    if gemini_keys_required and keys_configured == 0:
        problems.append("gemini_keys_missing")
    return problems


def build_health(
    *,
    snapshot: dict[str, Any],
    db_ok: bool,
    free_disk_gb: float,
    version: str,
    keys_configured: int,
    jobs: dict[str, int] | None = None,
    gemini_keys_required: bool = True,
) -> tuple[dict[str, Any], int]:
    """(payload, HTTP-код).

    ``503`` отдаётся, если polling не отвечает, БД недоступна или не осталось
    места. Отсутствие ключей Gemini — тоже ``503``: без них анализ не
    работает, и «здоровым» бот в таком состоянии не является.
    """
    problems = collect_problems(
        snapshot=snapshot,
        db_ok=db_ok,
        free_disk_gb=free_disk_gb,
        keys_configured=keys_configured,
        gemini_keys_required=gemini_keys_required,
    )
    last_update_age = snapshot.get("last_update_age_seconds")
    polling_start_age = snapshot.get("polling_start_age_seconds")
    last_polling_age = snapshot.get("last_polling_age_seconds")
    last_maintenance_age = snapshot.get("last_maintenance_age_seconds")

    payload: dict[str, Any] = {
        "ok": not problems,
        "problems": problems,
        "version": version,
        "uptime_seconds": round(float(snapshot.get("uptime_seconds", 0.0)), 1),
        "updates": {
            "total": snapshot.get("updates_total", 0),
            "rejected_total": snapshot.get("updates_rejected_total", 0),
            "last_age_seconds": (None if last_update_age is None else round(float(last_update_age), 1)),
        },
        "polling": {
            "start_age_seconds": (None if polling_start_age is None else round(float(polling_start_age), 1)),
            "last_age_seconds": (None if last_polling_age is None else round(float(last_polling_age), 1)),
        },
        "maintenance": {
            "runs_total": snapshot.get("maintenance_runs_total", 0),
            "failures_total": snapshot.get("maintenance_failures_total", 0),
            "ok": snapshot.get("last_maintenance_ok", True),
            "last_age_seconds": (None if last_maintenance_age is None else round(float(last_maintenance_age), 1)),
            "last_error": snapshot.get("last_maintenance_error", "") or None,
        },
        "database": {"ok": db_ok},
        "disk_free_gb": free_disk_gb,
        "jobs": jobs or {},
        "gemini_keys_configured": keys_configured,
        "alerts_total": snapshot.get("alerts_total", 0),
    }
    return payload, (200 if not problems else 503)


def _metric(name: str, value: float | int, help_text: str, kind: str = "gauge") -> list[str]:
    return [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} {kind}",
        f"{name} {value}",
    ]


def render_prometheus(
    *,
    snapshot: dict[str, Any],
    db_ok: bool,
    free_disk_gb: float,
    jobs: dict[str, int] | None = None,
    keys_configured: int | None = None,
) -> str:
    """Текст в формате Prometheus text exposition (version 0.0.4).

    Формат плоский и без зависимостей: его читает любой сборщик, а нам не нужен
    отдельный сервер метрик на машине с ботом.
    """
    jobs = jobs or {}
    problems = collect_problems(
        snapshot=snapshot,
        db_ok=db_ok,
        free_disk_gb=free_disk_gb,
        keys_configured=keys_configured,
        gemini_keys_required=keys_configured is not None,
    )
    lines: list[str] = []
    lines += _metric("mp3bot_up", 0 if problems else 1, "1 if /healthz would answer 200, else 0")
    lines += _metric("mp3bot_database_ok", 1 if db_ok else 0, "1 if the database header is readable")
    lines += _metric(
        "mp3bot_uptime_seconds",
        round(float(snapshot.get("uptime_seconds", 0.0)), 1),
        "Process uptime in seconds",
    )
    lines += _metric(
        "mp3bot_updates_total",
        snapshot.get("updates_total", 0),
        "Telegram updates accepted for processing",
        "counter",
    )
    lines += _metric(
        "mp3bot_updates_rejected_total",
        snapshot.get("updates_rejected_total", 0),
        "Updates dropped before business logic",
        "counter",
    )
    last_update_age = snapshot.get("last_update_age_seconds")
    lines += _metric(
        "mp3bot_last_update_age_seconds",
        -1 if last_update_age is None else round(float(last_update_age), 1),
        "Seconds since the last processed update, -1 if none yet; informational only",
    )
    last_polling_age = snapshot.get("last_polling_age_seconds")
    polling_start_age = snapshot.get("polling_start_age_seconds")
    polling_age = last_polling_age if last_polling_age is not None else polling_start_age
    lines += _metric(
        "mp3bot_last_polling_age_seconds",
        -1 if polling_age is None else round(float(polling_age), 1),
        "Seconds since successful getUpdates, or polling start before its first response",
    )
    lines += _metric(
        "mp3bot_jobs_active",
        snapshot.get("jobs_active", 0),
        "Heavy jobs running right now",
    )
    lines += _metric(
        "mp3bot_maintenance_failures_total",
        snapshot.get("maintenance_failures_total", 0),
        "Hourly maintenance runs that ended with an exception",
        "counter",
    )
    last_maintenance_age = snapshot.get("last_maintenance_age_seconds")
    lines += _metric(
        "mp3bot_last_maintenance_age_seconds",
        -1 if last_maintenance_age is None else round(float(last_maintenance_age), 1),
        "Seconds since the last maintenance run, -1 if none yet",
    )
    lines += _metric(
        "mp3bot_disk_free_bytes",
        -1 if free_disk_gb < 0 else int(free_disk_gb * (1024**3)),
        "Free bytes on the data volume, -1 if unknown",
    )
    lines += _metric(
        "mp3bot_maintenance_last_ok",
        1 if snapshot.get("last_maintenance_ok", True) else 0,
        "1 if the last maintenance run succeeded",
    )
    last_alert_age = snapshot.get("last_alert_age_seconds")
    lines += _metric(
        "mp3bot_last_alert_age_seconds",
        -1 if last_alert_age is None else round(float(last_alert_age), 1),
        "Seconds since the last alert to the owner, -1 if none yet",
    )
    for name, value in sorted(jobs.items()):
        lines += _metric(f"mp3bot_{name}", value, f"Scheduler slots: {name}")
    return "\n".join(lines) + "\n"
