#!/usr/bin/env python3
"""Наблюдаемость: счётчики, /healthz, /metrics, JSON-логи и маскирование.

До этого PR единственным сигналом живости был ``/health`` с двумя состояниями и
текстовый ``bot.log``. Проверки ниже закрывают то, из-за чего такой набор
бесполезен при разборе инцидента: метрики должны меняться от реальных событий,
``/healthz`` — называть причину, а секреты не должны попадать в лог ни в каком
формате.
"""

from __future__ import annotations

import json
import logging
import time

import pytest

from core import runtime_status
from core.logging_setup import (
    JsonFormatter,
    SecretMaskingFormatter,
    bind_job,
    bind_update,
    configure_logging,
)
from services.health_report import (
    build_health,
    collect_problems,
    database_ok,
    readonly_uri,
    render_prometheus,
)


@pytest.fixture(autouse=True)
def _fresh_state():
    runtime_status.reset()
    bind_update(None)
    bind_job(None)
    yield
    runtime_status.reset()
    bind_update(None)
    bind_job(None)


# ── реестр состояния ────────────────────────────────────────────────────────


def test_counters_reflect_real_events() -> None:
    runtime_status.record_update()
    runtime_status.record_update()
    runtime_status.record_update_rejected()
    with runtime_status.track_job():
        during = runtime_status.snapshot()
        assert during["jobs_active"] == 1
    after = runtime_status.snapshot()

    assert after["updates_total"] == 2
    assert after["updates_rejected_total"] == 1
    assert after["jobs_active"] == 0
    assert after["jobs_total"] == 1


def test_job_counter_is_released_even_on_exception() -> None:
    """Счётчик не должен «залипать» после ошибки в тяжёлой задаче."""
    with pytest.raises(RuntimeError):
        with runtime_status.track_job():
            raise RuntimeError("boom")
    assert runtime_status.snapshot()["jobs_active"] == 0


def test_scheduler_slots_are_counted_as_jobs() -> None:
    """Gauge ``jobs_active`` не должен быть декоративным.

    Счётчик растёт там, где тяжёлая задача реально берёт слот планировщика:
    иначе метрика всегда показывала бы 0 и ничего не измеряла.
    """
    import asyncio

    from core.resource_scheduler import scheduler

    async def main() -> None:
        async with scheduler.gpu_render:
            assert runtime_status.snapshot()["jobs_active"] == 1
            assert scheduler.utilization()["gpu_slots_in_use"] == 1
        assert runtime_status.snapshot()["jobs_active"] == 0
        assert scheduler.utilization()["gpu_slots_in_use"] == 0

    asyncio.run(main())
    assert runtime_status.snapshot()["jobs_total"] == 1


def test_scheduler_limits_survive_garbage_env(monkeypatch) -> None:
    """Опечатка в env не должна ронять старт или разрешать 0 слотов."""
    from core import resource_scheduler

    monkeypatch.setenv("GPU_RENDER_CONCURRENCY", "много")
    assert resource_scheduler._limit("GPU_RENDER_CONCURRENCY", 2) == 2
    monkeypatch.setenv("GPU_RENDER_CONCURRENCY", "0")
    assert resource_scheduler._limit("GPU_RENDER_CONCURRENCY", 1) == 1


def test_utilization_before_first_use_is_zero() -> None:
    """Эндпоинт может спросить занятость до первой тяжёлой задачи."""
    from core import resource_scheduler

    fresh = resource_scheduler._Scheduler()
    assert fresh.utilization() == {
        "gpu_slots_in_use": 0,
        "gpu_slots_limit": 1,
        "whisper_slots_in_use": 0,
        "whisper_slots_limit": 1,
    }


def test_jobs_gauge_never_goes_negative() -> None:
    runtime_status.record_job_finished()
    runtime_status.record_job_finished()
    assert runtime_status.snapshot()["jobs_active"] == 0


def test_maintenance_outcome_is_recorded() -> None:
    runtime_status.record_maintenance(ok=False, error="нет места")
    snapshot = runtime_status.snapshot()
    assert snapshot["maintenance_failures_total"] == 1
    assert snapshot["last_maintenance_ok"] is False
    assert snapshot["last_maintenance_error"] == "нет места"

    runtime_status.record_maintenance(ok=True)
    snapshot = runtime_status.snapshot()
    assert snapshot["last_maintenance_ok"] is True
    assert snapshot["last_maintenance_error"] == ""
    assert snapshot["maintenance_runs_total"] == 2


def test_alert_throttle_is_decided_by_the_registry() -> None:
    """Антиспам проверяется без Telegram: иначе он тестируется только руками."""
    now = 1_000_000.0
    assert runtime_status.alert_is_due(6, now=now) is True  # ещё не было алертов
    runtime_status.record_alert()
    assert runtime_status.alert_is_due(6, now=time.time()) is False
    assert runtime_status.alert_is_due(6, now=time.time() + 6 * 3600 + 1) is True


def test_garbage_interval_does_not_silence_alerts() -> None:
    """Опечатка в env не должна превращаться в «алерты выключены»."""
    assert runtime_status.alert_is_due("шесть часов") is True


def test_zero_interval_disables_throttling() -> None:
    runtime_status.record_alert()
    assert runtime_status.alert_is_due(0) is True
    assert runtime_status.alert_is_due(-1) is True


def test_alert_gauge_and_age_are_reported() -> None:
    runtime_status.record_alert()
    snapshot = runtime_status.snapshot()
    assert snapshot["alerts_total"] == 1
    assert isinstance(snapshot["last_alert_age_seconds"], float)


def test_uptime_counts_from_import_not_from_first_request() -> None:
    """`uptime_seconds` не должен быть нулём на первой проверке."""
    started = runtime_status.snapshot()["uptime_seconds"]
    later = runtime_status.snapshot(now=time.time() + 120)["uptime_seconds"]
    assert later - started >= 119


def test_error_text_is_truncated() -> None:
    """Метрика не должна раздуваться из-за длинного traceback."""
    runtime_status.record_maintenance(ok=False, error="x" * 5000)
    assert len(str(runtime_status.snapshot()["last_maintenance_error"])) == 200


# ── проверка БД ─────────────────────────────────────────────────────────────


def test_health_report_compatibility_exports_core_owner() -> None:
    from core import health_report as core_owner
    from services import health_report as compatibility

    assert compatibility.build_health is core_owner.build_health
    assert compatibility.render_prometheus is core_owner.render_prometheus


def test_database_probe_reads_an_existing_db(tmp_path) -> None:
    """Имя с пробелом и «#» обязано открываться: путь попадает в URI."""
    import sqlite3

    db = tmp_path / "a b#c.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t(x)")
    assert database_ok(db) is True


def test_database_probe_does_not_create_missing_db(tmp_path) -> None:
    """health-проба не должна оставлять пустой файл вместо базы."""
    missing = tmp_path / "nope.db"
    assert database_ok(missing) is False
    assert not missing.exists()


def test_database_probe_reports_corruption(tmp_path) -> None:
    """``SELECT 1`` проходил и на мусоре: файл не читался вовсе.

    Проба обязана отличать испорченный файл от рабочего, иначе `/healthz`
    отвечает 200 при базе, с которой бот не может работать.
    """
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a database" * 10)
    assert database_ok(broken) is False


def test_database_probe_accepts_fresh_empty_file(tmp_path) -> None:
    """Ноль байт — это свежая установка, а не ошибка."""
    fresh = tmp_path / "fresh.db"
    fresh.write_bytes(b"")
    assert database_ok(fresh) is True


def test_readonly_uri_is_absolute_on_windows() -> None:
    """``file:C:\\...`` SQLite на Windows понимает как относительный путь."""
    assert readonly_uri("C:\\bot\\cache.db") == "file:/C:/bot/cache.db?mode=ro"
    assert readonly_uri("/tmp/x.db").startswith("file:/tmp/x.db")
    assert readonly_uri("bot_cache.db").startswith("file:/")


def test_disk_free_gb_returns_minus_one_when_path_is_unusable(monkeypatch) -> None:
    """Недоступный путь — «неизвестно», а не «0 ГБ» (иначе ложный disk_low)."""
    import shutil

    from services import health_report

    def boom(_path):
        raise OSError("no such device")

    monkeypatch.setattr(shutil, "disk_usage", boom)
    assert health_report.disk_free_gb("/nowhere") == -1.0


def test_gemini_key_count_counts_only_non_empty(monkeypatch) -> None:
    from services import health_report

    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"):
        monkeypatch.delenv(name, raising=False)
    assert health_report.gemini_keys_configured() == 0
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-first")
    monkeypatch.setenv("GEMINI_API_KEY_3", "   ")
    assert health_report.gemini_keys_configured() == 1


# ── /healthz ────────────────────────────────────────────────────────────────


def _snapshot(**overrides):
    base = runtime_status.snapshot()
    base.update(overrides)
    return base


def _health(**overrides):
    kwargs = {
        "snapshot": _snapshot(last_update_age_seconds=1.0, last_maintenance_age_seconds=600.0),
        "db_ok": True,
        "free_disk_gb": 50.0,
        "version": "4.0.0",
        "keys_configured": 2,
        "jobs": {"gpu_slots_in_use": 1, "gpu_slots_limit": 1},
    }
    kwargs.update(overrides)
    return build_health(**kwargs)


def test_healthy_bot_returns_200_and_names_the_version() -> None:
    payload, status = _health()
    assert status == 200
    assert payload["ok"] is True
    assert payload["problems"] == []
    assert payload["version"] == "4.0.0"
    assert payload["jobs"]["gpu_slots_in_use"] == 1


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"snapshot": _snapshot(last_update_age_seconds=900.0)}, "updates_stale"),
        ({"snapshot": _snapshot(last_maintenance_age_seconds=9000.0)}, "maintenance_stale"),
        ({"snapshot": _snapshot(last_maintenance_ok=False)}, "maintenance_failed"),
        ({"db_ok": False}, "database_unavailable"),
        ({"free_disk_gb": 0.5}, "disk_low"),
        ({"keys_configured": 0}, "gemini_keys_missing"),
    ],
)
def test_each_failure_mode_is_named(overrides: dict, problem: str) -> None:
    """503 без причины бесполезен: клиент должен видеть, что именно сломалось."""
    payload, status = _health(**overrides)
    assert status == 503
    assert problem in payload["problems"]
    assert payload["ok"] is False


def test_health_never_leaks_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSECRETSECRETSECRETSECRETSECRETSECRET123")
    payload, _ = _health()
    assert "AIzaSECRET" not in json.dumps(payload)
    assert payload["gemini_keys_configured"] == 2


def test_first_minutes_without_updates_are_not_an_alert() -> None:
    """Сразу после старта апдейтов ещё не было — это не «бот завис»."""
    payload, status = _health(snapshot=_snapshot(last_update_age_seconds=None))
    assert status == 200
    assert payload["updates"]["last_age_seconds"] is None


# ── /metrics ────────────────────────────────────────────────────────────────


def test_metrics_are_prometheus_text() -> None:
    text = render_prometheus(
        snapshot=_snapshot(updates_total=7, jobs_active=2),
        db_ok=True,
        free_disk_gb=10.0,
        jobs={"gpu_slots_in_use": 1, "whisper_slots_limit": 1},
    )
    assert "mp3bot_updates_total 7" in text
    assert "mp3bot_jobs_active 2" in text
    assert "mp3bot_gpu_slots_in_use 1" in text
    assert "mp3bot_up 1" in text
    assert "# TYPE mp3bot_updates_total counter" in text
    assert text.endswith("\n")
    for line in text.splitlines():
        if not line.startswith("#"):
            name, _, value = line.partition(" ")
            assert name.replace("_", "").replace("mp3bot", "").isalnum() or name
            float(value)  # значение обязано быть числом


def test_metrics_agree_with_healthz() -> None:
    """``mp3bot_up`` обязан совпадать с кодом ответа /healthz.

    Иначе мониторинг и health-проба сообщают разное об одном и том же боте — и
    верят тому, который удобнее.
    """
    snapshot = _snapshot(last_update_age_seconds=900.0)
    text = render_prometheus(snapshot=snapshot, db_ok=True, free_disk_gb=50.0, keys_configured=1)
    payload, status = build_health(
        snapshot=snapshot,
        db_ok=True,
        free_disk_gb=50.0,
        version="4.0.0",
        keys_configured=1,
    )
    assert status == 503
    assert "mp3bot_up 0" in text
    assert payload["problems"] == ["updates_stale"]


def test_metrics_without_key_count_still_work() -> None:
    """``/metrics`` не знает про ключи Gemini и не должен из-за этого врать."""
    text = render_prometheus(snapshot=_snapshot(last_update_age_seconds=1.0), db_ok=True, free_disk_gb=50.0)
    assert "mp3bot_up 1" in text
    assert collect_problems(snapshot=_snapshot(last_update_age_seconds=1.0), db_ok=True, free_disk_gb=50.0) == []


def test_absent_values_are_reported_as_minus_one() -> None:
    text = render_prometheus(
        snapshot=_snapshot(last_update_age_seconds=None, last_maintenance_age_seconds=None),
        db_ok=False,
        free_disk_gb=-1.0,
    )
    assert "mp3bot_last_update_age_seconds -1" in text
    assert "mp3bot_last_maintenance_age_seconds -1" in text
    assert "mp3bot_up 0" in text
    assert "mp3bot_database_ok 0" in text
    assert "mp3bot_disk_free_bytes -1" in text


# ── логи ────────────────────────────────────────────────────────────────────


def _record(message: str, **extra) -> logging.LogRecord:
    record = logging.LogRecord("svc", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_secret_values_are_masked_in_text_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "123456789:AAHqwertyuiopASDFGHJKLzxcvbnm12345"
    monkeypatch.setenv("BOT_TOKEN", secret)
    formatted = SecretMaskingFormatter("%(message)s").format(
        _record(f"отправляю через https://api.telegram.org/bot{secret}/sendMessage")
    )
    assert secret not in formatted
    assert "***" in formatted


def test_short_env_values_are_not_treated_as_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Иначе значение вида «1» вырежет половину сообщений."""
    monkeypatch.setenv("BOT_TOKEN", "1")
    formatted = SecretMaskingFormatter("%(message)s").format(_record("шаг 1 из 3 готов"))
    assert "шаг 1 из 3 готов" in formatted


def test_json_logs_carry_update_and_job(monkeypatch: pytest.MonkeyPatch) -> None:
    bind_update(424242)
    bind_job("whisper")
    payload = json.loads(JsonFormatter().format(_record("транскрипция начата", update_id="424242", job="whisper")))
    assert payload["msg"] == "транскрипция начата"
    assert payload["update_id"] == "424242"
    assert payload["job"] == "whisper"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "svc"


def test_json_logs_mask_secrets_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSECRETSECRETSECRETSECRETSECRETSECRET123")
    payload = json.loads(JsonFormatter().format(_record("ключ AIzaSECRETSECRETSECRETSECRETSECRETSECRET123")))
    assert "AIzaSECRET" not in json.dumps(payload)


def test_json_logs_include_traceback() -> None:
    try:
        raise ValueError("сервис сломался")
    except ValueError:
        import sys

        record = _record("ошибка")
        record.exc_info = sys.exc_info()
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: сервис сломался" in payload["exc"]


def test_unwritable_log_file_does_not_stop_the_bot(tmp_path, monkeypatch, caplog) -> None:
    """Нет прав на bot.log — это не повод не запускаться."""
    from core import logging_setup

    def boom(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(logging_setup, "RotatingFileHandler", boom)
    logging_setup.configure_logging(level="INFO", fmt="text", log_file=tmp_path / "bot.log")
    owned = [h for h in logging.getLogger().handlers if getattr(h, "_mp3bot_owned", False)]
    assert len(owned) == 1  # только stderr
    assert not (tmp_path / "bot.log").exists()


def test_update_id_reaches_records_of_any_logger() -> None:
    """Строки сервисов, которые про Telegram ничего не знают, тоже связаны."""
    from core.logging_setup import ContextFilter, current_update_id

    bind_update(99)
    assert current_update_id() == "99"
    record = _record("внутри сервиса")
    assert ContextFilter().filter(record) is True
    assert record.update_id == "99"


def test_configure_logging_is_idempotent(tmp_path) -> None:
    """Повторный вызов (reload, тесты) не должен дублировать обработчики."""
    log_file = tmp_path / "bot.log"
    configure_logging(level="WARNING", fmt="text", log_file=log_file)
    first = [h for h in logging.getLogger().handlers if getattr(h, "_mp3bot_owned", False)]
    configure_logging(level="WARNING", fmt="text", log_file=log_file)
    second = [h for h in logging.getLogger().handlers if getattr(h, "_mp3bot_owned", False)]
    assert len(first) == len(second) == 2
    logging.getLogger().info("не должно попасть: уровень WARNING")


def test_json_format_is_selected_by_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    configure_logging(log_file=tmp_path / "bot.log")
    root = logging.getLogger()
    owned = [h for h in root.handlers if getattr(h, "_mp3bot_owned", False)]
    assert any(isinstance(h.formatter, JsonFormatter) for h in owned)
