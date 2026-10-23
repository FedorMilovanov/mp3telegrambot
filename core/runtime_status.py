#!/usr/bin/env python3
"""Состояние рантайма для health-эндпоинта и метрик.

Зачем отдельный модуль: до него единственным ответом на вопрос «бот жив?» был
``/health`` с двумя состояниями — ``OK`` и ``503 STALE`` (если ``mark_bot_alive``
не вызывался больше 5 минут). Этого хватало, чтобы узнать «что-то не так», и не
хватало, чтобы понять «что именно»: падает ли обслуживание, забита ли БД, кончилось
ли место, сколько тяжёлых задач идёт прямо сейчас.

Здесь только **счётчики и отметки времени**, без I/O: ни файлов, ни сети, ни БД.
Поэтому модуль безопасно читать из потока Flask и писать из event loop — все
операции под одним замком и занимают микросекунды.

Что не хранится: сами секреты, идентификаторы пользователей, тексты сообщений.
Метрика не должна становиться источником утечки.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

_LOCK = threading.Lock()


@dataclass
class _State:
    started_at: float = field(default_factory=time.time)
    updates_total: int = 0
    updates_rejected_total: int = 0
    last_update_at: float = 0.0
    jobs_active: int = 0
    jobs_total: int = 0
    maintenance_runs_total: int = 0
    maintenance_failures_total: int = 0
    last_maintenance_at: float = 0.0
    last_maintenance_ok: bool = True
    last_maintenance_error: str = ""
    alerts_total: int = 0
    last_alert_at: float = 0.0


_state = _State()


def reset() -> None:
    """Сбросить состояние — только для тестов."""
    global _state
    with _LOCK:
        _state = _State()


def record_update() -> None:
    """Апдейт принят в обработку."""
    with _LOCK:
        _state.updates_total += 1
        _state.last_update_at = time.time()


def record_update_rejected() -> None:
    """Апдейт отброшен до бизнес-логики (дедупликация, устаревший pending)."""
    with _LOCK:
        _state.updates_rejected_total += 1


def record_job_started() -> None:
    """Тяжёлая задача взяла слот планировщика (рендер, транскрипция)."""
    with _LOCK:
        _state.jobs_active += 1
        _state.jobs_total += 1


def record_job_finished() -> None:
    """Слот освобождён.

    ``max(0, ...)`` здесь не перестраховка: ``Semaphore.release()`` можно
    вызвать больше раз, чем ``acquire()`` (в asyncio это молча увеличивает
    счётчик). Отрицательный gauge в мониторинге выглядел бы как «минус одна
    задача» и врал бы сильнее, чем ноль.
    """
    with _LOCK:
        _state.jobs_active = max(0, _state.jobs_active - 1)


@contextmanager
def track_job() -> Iterator[None]:
    """Обёртка для тяжёлой задачи, которая не идёт через планировщик."""
    record_job_started()
    try:
        yield
    finally:
        record_job_finished()


def record_maintenance(*, ok: bool, error: str = "") -> None:
    """Итог одного прогона обслуживания (раз в час)."""
    with _LOCK:
        _state.maintenance_runs_total += 1
        _state.last_maintenance_at = time.time()
        _state.last_maintenance_ok = ok
        if ok:
            _state.last_maintenance_error = ""
        else:
            _state.maintenance_failures_total += 1
            # Текст ошибки обрезается: он попадает в /healthz и метрики.
            _state.last_maintenance_error = (error or "")[:200]


def record_alert() -> None:
    """Алерт владельцу отправлен: счётчик и отметка времени для антиспама."""
    with _LOCK:
        _state.alerts_total += 1
        _state.last_alert_at = time.time()


def alert_is_due(interval_hours: float, *, now: float | None = None) -> bool:
    """Пора ли повторять алерт владельцу.

    Логика вынесена из обработчика, чтобы её можно было проверить без
    Telegram: антиспам — единственное, что мешает постоянной ошибке (нет прав
    на каталог) превратиться в сообщение каждую минуту. Отрицательный или
    нулевой интервал означает «не глушить».
    """
    try:
        hours = float(interval_hours)
    except (TypeError, ValueError):
        hours = 0.0
    if hours <= 0:
        return True
    now = time.time() if now is None else now
    with _LOCK:
        last = _state.last_alert_at
    return (now - last) >= hours * 3600


def snapshot(now: float | None = None) -> dict[str, object]:
    """Согласованный снимок состояния для ``/healthz`` и ``/metrics``."""
    now = time.time() if now is None else now
    with _LOCK:
        last_update_age = (now - _state.last_update_at) if _state.last_update_at else None
        last_maintenance_age = (
            (now - _state.last_maintenance_at) if _state.last_maintenance_at else None
        )
        return {
            "uptime_seconds": max(0.0, now - _state.started_at),
            "updates_total": _state.updates_total,
            "updates_rejected_total": _state.updates_rejected_total,
            "last_update_age_seconds": last_update_age,
            "jobs_active": _state.jobs_active,
            "jobs_total": _state.jobs_total,
            "maintenance_runs_total": _state.maintenance_runs_total,
            "maintenance_failures_total": _state.maintenance_failures_total,
            "last_maintenance_age_seconds": last_maintenance_age,
            "last_maintenance_ok": _state.last_maintenance_ok,
            "last_maintenance_error": _state.last_maintenance_error,
            "alerts_total": _state.alerts_total,
            "last_alert_age_seconds": (now - _state.last_alert_at) if _state.last_alert_at else None,
        }
