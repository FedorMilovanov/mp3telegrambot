#!/usr/bin/env python3
"""Explain why the local Telegram Bot API fell back to cloud mode.

The official server is an MTProto/TDLib client.  A Python SOCKS proxy can keep
cloud Bot API requests working while the separate telegram-bot-api.exe process
still has no system route to Telegram data centres.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse


def _proxy_label(value: str) -> str:
    try:
        parsed = urlparse(value)
        if parsed.scheme and parsed.hostname:
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://{parsed.hostname}{port}"
    except Exception:
        pass
    return "application proxy"


def _read_tail() -> tuple[str, Path | None]:
    try:
        from services.local_botapi_runtime import _read_log_tail, _writable_data_dir

        path = _writable_data_dir().parent / "botapi-server.log"
        return _read_log_tail(str(path), max_chars=5000), path
    except Exception:
        return "", None


def _classify(tail: str, route_ok: bool, proxy: str) -> str:
    low = tail.lower()
    if any(token in low for token in ("api-id", "api_id", "api-hash", "api_hash")) and any(
        token in low for token in ("invalid", "wrong", "must provide")
    ):
        return "неверны TELEGRAM_API_ID или TELEGRAM_API_HASH"
    if any(token in low for token in ("access is denied", "permission denied", "can't be opened")):
        return "процесс не может записывать рабочую папку Local Bot API"
    if any(token in low for token in ("409", "conflict", "logged out", "another bot api")):
        return (
            "бот остался зарегистрирован на другом Bot API сервере; перед переходом "
            "на local требуется облачный logOut"
        )
    if not route_ok and proxy:
        return (
            f"облачные запросы проходят через {_proxy_label(proxy)}, но системного TUN-маршрута "
            "для отдельного процесса telegram-bot-api.exe нет"
        )
    if any(token in low for token in ("timeout", "timed out", "failed to connect", "network is unreachable")):
        return "TDLib не может установить прямое MTProto-соединение с дата-центрами Telegram"
    if not route_ok:
        return "Windows не видит прямой системный маршрут к Telegram; приложение работает только через proxy"
    return "локальный процесс запустился, но /getMe не завершил авторизацию; смотри botapi-server.log"


def explain_local_bot_api_result(requested_local_url: str) -> None:
    """Print one actionable explanation after the bootstrap selected cloud mode."""
    if not requested_local_url or os.getenv("MP3BOT_EFFECTIVE_BOT_API", "").strip().lower() != "cloud":
        return

    proxy = (
        os.getenv("TELEGRAM_PROXY_URL", "").strip()
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
    )
    try:
        from services.local_botapi_bootstrap import _system_telegram_route_available

        route_ok = _system_telegram_route_available(2.5)
    except Exception:
        route_ok = False

    tail, log_path = _read_tail()
    reason = _classify(tail, route_ok, proxy)
    print(f"🔎 Точная причина Local Bot API: {reason}.")
    if proxy:
        print(
            "   Важно: официальный параметр telegram-bot-api --proxy обслуживает только "
            "исходящие webhook HTTP-запросы и не проксирует TDLib/MTProto."
        )
    print(
        "   Почему раньше работало: тогда был активен системный TUN/VPN-маршрут либо "
        "уже запущенный Local Bot API сохранял рабочую сессию; SOCKS-порт сам по себе этого не заменяет."
    )
    if log_path:
        print(f"   Диагностика сервера: {log_path}")
        try:
            report = log_path.parent / "botapi-last-diagnostic.txt"
            safe_tail = re.sub(r"/bot\d+:[A-Za-z0-9_-]+", "/bot***", tail)
            report.write_text(
                "Local Bot API diagnostic\n"
                f"requested_url={requested_local_url}\n"
                f"effective=cloud\nroute_hint={route_ok}\n"
                f"cloud_proxy={_proxy_label(proxy) if proxy else 'none'}\n"
                f"reason={reason}\n\nlog_tail:\n{safe_tail[-3500:]}\n",
                encoding="utf-8",
            )
        except OSError:
            pass
