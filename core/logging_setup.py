#!/usr/bin/env python3
"""Логирование: один формат для текста и JSON, маскирование секретов, update_id.

Что было не так до этого модуля: ``logging.basicConfig`` с текстовым форматом и
жёстко прописанным ``RotatingFileHandler("bot.log")`` в ``core/globals.py``.
Три следствия, каждое из которых мешает разбирать инцидент:

1. файл лога писался в текущий каталог — при запуске из другой папки логи
   оказывались в другом месте, и «в логе ничего нет» было неправдой;
2. строки разных апдейтов перемешаны и не связаны: по логу нельзя было собрать
   историю одного запроса;
3. значение токена, случайно попавшее в сообщение, сохранялось как есть.

Здесь:

* ``LOG_FORMAT=text|json`` и ``LOG_FILE`` — конфигурация без правки кода;
* ``ContextFilter`` добавляет ``update_id`` из ``contextvars`` в каждую
  строку, включая строки сервисов, которые ничего о Telegram не знают;
* ``SecretMaskingFormatter`` вырезает значения известных секретных переменных
  из готового текста — defense-in-depth поверх того, что в коде их и так не печатают.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

#: Переменные, значения которых нельзя показывать в логах никогда.
SECRET_ENV_NAMES = (
    "BOT_TOKEN",
    "TELEGRAM_API_HASH",
    "GEMINI_API_KEY",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
    "GEMINI_API_KEY_4",
    "YANDEX_OAUTH_TOKEN",
    "YANDEX_CLIENT_SECRET",
    "VK_TOKEN",
    "VK_ACCESS_TOKEN",
    "OPENAI_API_KEY",
)

#: Значения короче этого не маскируются: иначе «1» или «ok» испортят весь лог.
MIN_SECRET_LENGTH = 8

_update_id: contextvars.ContextVar[str] = contextvars.ContextVar("update_id", default="-")
_job: contextvars.ContextVar[str] = contextvars.ContextVar("job", default="-")


def bind_update(update_id: int | str | None) -> None:
    """Привязать текущий контекст (задачу, поток) к апдейту Telegram."""
    _update_id.set("-" if update_id is None else str(update_id))


def bind_job(job: str | None) -> None:
    """Привязать текущий контекст к именованной работе («whisper», «render»)."""
    _job.set(job or "-")


def current_update_id() -> str:
    return _update_id.get()


def current_job() -> str:
    return _job.get()


def _secret_values() -> tuple[str, ...]:
    """Текущие значения секретов из окружения (не кэшируется: .env читается позже)."""
    values: list[str] = []
    for name in SECRET_ENV_NAMES:
        value = (os.getenv(name) or "").strip()
        if len(value) >= MIN_SECRET_LENGTH:
            values.append(value)
    # Длинные значения первыми: иначе короткое «перепишет» часть длинного.
    return tuple(sorted(set(values), key=len, reverse=True))


def _mask(text: str) -> str:
    """Заменить все известные секреты на ``***``."""
    for secret in _secret_values():
        if secret in text:
            text = text.replace(secret, "***")
    return text


class SecretMaskingFormatter(logging.Formatter):
    """Форматтер, который вычищает значения секретов из готовой строки."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - API logging
        return _mask(super().format(record))


class JsonFormatter(SecretMaskingFormatter):
    """Одна строка JSON на запись: для journald, Loki и любого агрегатора.

    Наследуется от ``SecretMaskingFormatter``, но форматирует сам: у JSON своя
    структура, и общий ``format`` здесь не применим. Общая часть — ``_mask``.
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - API logging
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "update_id": getattr(record, "update_id", "-"),
            "job": getattr(record, "job", "-"),
            "thread": record.threadName,
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return _mask(json.dumps(payload, ensure_ascii=False))


class ContextFilter(logging.Filter):
    """Добавляет текущие ``update_id`` и ``job`` в запись (для текстового формата)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "update_id"):
            record.update_id = current_update_id()
        if not hasattr(record, "job"):
            record.job = current_job()
        return True


TEXT_FORMAT = "%(asctime)s %(levelname)-7s [%(update_id)s] %(name)s: %(message)s"


def configure_logging(
    *,
    level: str | int | None = None,
    fmt: str | None = None,
    log_file: str | Path | None = None,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """Настроить корневой логгер проекта.

    Значения по умолчанию совпадают с прежним поведением (текстовый формат,
    ``bot.log`` в текущем каталоге), поэтому вызов без аргументов — безопасная
    замена ``logging.basicConfig``. Всё остальное — через окружение:

    * ``LOG_LEVEL`` — уровень (по умолчанию ``INFO``);
    * ``LOG_FORMAT`` — ``text`` или ``json``;
    * ``LOG_FILE`` — путь к файлу; пустое значение отключает файловый обработчик.
    """
    level = level if level is not None else os.getenv("LOG_LEVEL", "INFO")
    # os.getenv отдаёт None, если переменная не задана вовсе; пустая строка —
    # законный способ попросить «по умолчанию». Отдельная переменная нужна,
    # чтобы дефолт из env-справочника остался видимым генератору документов.
    fmt_env = os.getenv("LOG_FORMAT", "text")
    fmt_name = (fmt or fmt_env or "text").strip().lower()
    if isinstance(level, str):
        level = getattr(logging, level.strip().upper(), logging.INFO)
    if log_file is None:
        log_file = os.getenv("LOG_FILE", "bot.log")

    formatter: SecretMaskingFormatter
    if fmt_name == "json":
        formatter = JsonFormatter()
    else:
        formatter = SecretMaskingFormatter(TEXT_FORMAT)

    root = logging.getLogger()
    # Повторная настройка (перезапуск в тестах, reload) не должна плодить
    # дублирующиеся обработчики: каждый вызов заменяет наши, чужие не трогаем.
    for handler in list(root.handlers):
        if getattr(handler, "_mp3bot_owned", False):
            root.removeHandler(handler)
            handler.close()

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.addFilter(ContextFilter())
    stream._mp3bot_owned = True  # type: ignore[attr-defined]
    root.addHandler(stream)

    if log_file:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                str(log_file), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
        except OSError:
            # Файл недоступен (только чтение, занят, нет прав) — это не повод
            # не запускаться; диагностика в stderr важнее.
            file_handler = None
        if file_handler is not None:
            file_handler.setFormatter(formatter)
            file_handler.addFilter(ContextFilter())
            file_handler._mp3bot_owned = True  # type: ignore[attr-defined]
            root.addHandler(file_handler)

    root.setLevel(level)
    # httpx/httpcore на INFO пишут строку на каждый запрос Gemini: шум, в котором
    # не видно собственных сообщений.
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING) if isinstance(level, int) else logging.WARNING)
