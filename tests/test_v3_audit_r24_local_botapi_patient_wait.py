#!/usr/bin/env python3
"""AUDIT R24 (live bug: с TUN+прокси бот всё равно уходил на облако 50 МБ,
хотя локальный Bot API сервер работал).

Root cause: `_fast_cloud_fallback = _fallback_enabled and _cloud_fallback_proxy_url`.
Так как у пользователя задан TELEGRAM_PROXY_URL (для облачного fallback),
флаг всегда True, и бот делал РОВНО ОДНУ проверку /getMe (в t=0, сразу после
открытия TCP-порта) и уходил на облако. telegram-bot-api.exe на холодном
старте 20-60с поднимает соединение с дата-центрами Telegram, поэтому проверка
в t=0 всегда не успевает. При включённом TUN локальный сервер РАБОТАЕТ —
надо лишь дать время.

Fix: LOCAL_BOT_API_WAIT_LOCAL=1 отключает быстрый fallback и ждёт локальный
/getMe весь интервал LOCAL_BOT_API_GETME_TIMEOUT_SEC (по умолчанию 60с).
Поведение по умолчанию (без флага) НЕ меняется.
"""
from pathlib import Path

SRC = Path("main.py").read_text(encoding="utf-8")


def test_patient_wait_flag_is_honored():
    assert 'os.getenv("LOCAL_BOT_API_WAIT_LOCAL"' in SRC
    # быстрый fallback обязан отключаться, когда включён patient-режим
    assert "and not _patient_local)" in SRC


def test_getme_window_is_configurable_and_clamped():
    assert 'os.getenv("LOCAL_BOT_API_GETME_TIMEOUT_SEC"' in SRC
    # интервал ограничен разумными рамками (15..300с)
    assert "max(15, min(_getme_window_sec, 300))" in SRC
    # цикл ожидания строится из окна, а не из зашитого range(12)
    assert "_getme_attempts = max(1, _getme_window_sec // 5)" in SRC
    assert "for _gm_attempt in range(_getme_attempts):" in SRC


def test_default_behavior_unchanged_when_flag_absent():
    """Без LOCAL_BOT_API_WAIT_LOCAL патч-режим выключен: _fast_cloud_fallback
    остаётся прежним (proxy задан -> быстрый откат), окно 60с == прежним 12x5с."""
    # значение по умолчанию окна = 60 -> 12 попыток == прежнее range(12)
    assert '"LOCAL_BOT_API_GETME_TIMEOUT_SEC", "60"' in SRC
    # patient_local вычисляется из env, по умолчанию пусто -> False
    assert '_patient_local = os.getenv("LOCAL_BOT_API_WAIT_LOCAL", "")' in SRC


def test_fast_fallback_message_no_longer_claims_no_tun():
    """Старое сообщение утверждало 'no-TUN fast path' даже когда у юзера TUN
    включён — теперь оно нейтральное и подсказывает LOCAL_BOT_API_WAIT_LOCAL."""
    assert "no-TUN fast path" not in SRC
    idx = SRC.find("Быстрый fallback на облачный Bot API")
    assert idx != -1
    assert "LOCAL_BOT_API_WAIT_LOCAL=1" in SRC[idx:idx + 400]


def test_env_knobs_documented():
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "LOCAL_BOT_API_WAIT_LOCAL=1" in env
    assert "LOCAL_BOT_API_GETME_TIMEOUT_SEC" in env
