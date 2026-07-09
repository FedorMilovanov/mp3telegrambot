#!/usr/bin/env python3
"""AUDIT R21: MAX_FILE_SIZE_MB был заморожен на момент импорта модуля —
только по наличию LOCAL_BOT_API_URL в .env, а не по факту, поднялся ли
локальный Bot API сервер в рантайме. main.py может откатиться на облачный
Bot API (50 МБ), если /getMe локального сервера не ответил (SOCKS/MTProto
proxy не поддерживается официальным telegram-bot-api.exe без TUN/VPN) —
но старый код продолжал пропускать файлы до 2000 МБ через предварительную
проверку размера, и Telegram затем реально отклонял их отправку
("Request Entity Too Large").

Живой лог: LiveDub успешно сгенерировал видео, предварительная проверка
"file_size > MAX_FILE_SIZE_MB" его пропустила, но send_video упал с
Request Entity Too Large, потому что бот в этой сессии фактически работал
через облачный API (50 МБ), а не через локальный (2000 МБ).
"""
import re
from pathlib import Path

import core.database as db

_BARE_CONSTANT_RE = re.compile(r"\bMAX_FILE_SIZE_MB\b")


def test_get_max_file_size_mb_returns_current_value():
    assert db.get_max_file_size_mb() == db.MAX_FILE_SIZE_MB


def test_setter_switches_between_cloud_and_local_limits():
    was_explicit = db._MAX_FILE_SIZE_MB_EXPLICIT
    db._MAX_FILE_SIZE_MB_EXPLICIT = False
    try:
        db.set_effective_max_file_size_mb(True)
        assert db.get_max_file_size_mb() == 2000
        db.set_effective_max_file_size_mb(False)
        assert db.get_max_file_size_mb() == 50
    finally:
        db._MAX_FILE_SIZE_MB_EXPLICIT = was_explicit


def test_setter_is_noop_when_operator_set_env_explicitly():
    """Ручной выбор оператора (.env MAX_FILE_SIZE_MB=...) должен всегда
    побеждать над автоопределением локальный/облачный."""
    was_explicit = db._MAX_FILE_SIZE_MB_EXPLICIT
    was_value = db.MAX_FILE_SIZE_MB
    db._MAX_FILE_SIZE_MB_EXPLICIT = True
    db.MAX_FILE_SIZE_MB = 123
    try:
        db.set_effective_max_file_size_mb(True)
        assert db.get_max_file_size_mb() == 123
        db.set_effective_max_file_size_mb(False)
        assert db.get_max_file_size_mb() == 123
    finally:
        db._MAX_FILE_SIZE_MB_EXPLICIT = was_explicit
        db.MAX_FILE_SIZE_MB = was_value


def test_main_calls_setter_after_fallback_decision_is_final():
    """set_effective_max_file_size_mb() должен вызываться ПОСЛЕ того, как
    _using_local_bot_api получил своё окончательное значение (включая
    возможный откат на облако при неотвечающем /getMe), а не до него."""
    src = Path("main.py").read_text(encoding="utf-8")
    idx = src.find("set_effective_max_file_size_mb(_using_local_bot_api)")
    assert idx != -1, "main.py должен вызывать set_effective_max_file_size_mb"
    fallback_idx = src.find("_using_local_bot_api = False")
    assert fallback_idx != -1
    assert fallback_idx < idx, (
        "вызов сеттера должен идти после присвоения _using_local_bot_api "
        "в ветке cloud-fallback, иначе лимит останется 2000 МБ по ошибке"
    )


def test_no_production_consumer_imports_frozen_constant_directly():
    """`from core.database import MAX_FILE_SIZE_MB` замораживает значение
    на момент импорта — все потребители обязаны читать через
    get_max_file_size_mb()."""
    consumers_using_limit = [
        "pipelines/main_pipeline.py",
        "pipelines/montage.py",
        "pipelines/clips.py",
        "services/segment_render.py",
    ]
    for path in consumers_using_limit:
        src = Path(path).read_text(encoding="utf-8")
        assert not _BARE_CONSTANT_RE.search(src), (
            f"{path}: найден голый идентификатор MAX_FILE_SIZE_MB — "
            "он замораживается на момент импорта, нужен get_max_file_size_mb()"
        )
        assert "get_max_file_size_mb" in src, f"{path} должен использовать get_max_file_size_mb()"

    # handlers/commands.py вообще не использует лимит — раньше тянул
    # MAX_FILE_SIZE_MB мёртвым импортом; теперь просто не импортирует его.
    commands_src = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert not _BARE_CONSTANT_RE.search(commands_src), (
        "handlers/commands.py: мёртвый импорт MAX_FILE_SIZE_MB должен быть удалён"
    )


def test_database_module_exposes_getter_and_setter():
    assert callable(db.get_max_file_size_mb)
    assert callable(db.set_effective_max_file_size_mb)
