#!/usr/bin/env python3
"""One-path startup for the mandatory local Telegram Bot API.

The bot needs the local server for original-quality uploads up to 2000 MB. This
module deliberately has no cloud fallback and no media recompression path:

1. use an already healthy local server;
2. if a local server is already warming up, wait for it instead of restarting;
3. otherwise call the required cloud ``logOut`` once and start one server;
4. continue only after a real local ``getMe`` succeeds.

A live server is never killed merely because TDLib needed longer to establish a
Telegram data-centre session. If startup times out, the process is left alive so
that enabling/fixing the system TUN can let the same session finish.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from services import local_botapi_bootstrap as probe_runtime
from services import local_botapi_runtime as process_runtime

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class LocalBotApiRequiredError(RuntimeError):
    """The mandatory local Bot API could not be made ready."""


def _timeout_seconds() -> int:
    try:
        value = int(os.getenv("LOCAL_BOT_API_REQUIRED_TIMEOUT_SEC", "300").strip() or "300")
    except ValueError:
        value = 300
    return max(60, min(value, 600))


def _normalise_proxy(url: str) -> str:
    value = str(url or "").strip()
    if value.lower().startswith("socks5h://"):
        return "socks5://" + value.split("://", 1)[1]
    return value


def _cloud_proxy() -> str:
    return _normalise_proxy(
        os.getenv("TELEGRAM_PROXY_URL", "").strip()
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
    )


def _cloud_logout(token: str, proxy_url: str) -> None:
    """Deregister the bot from api.telegram.org before local authorization."""
    kwargs: dict[str, object] = {
        "timeout": httpx.Timeout(30.0, connect=20.0),
        "trust_env": not bool(proxy_url),
    }
    if proxy_url:
        kwargs["proxy"] = proxy_url
    url = f"https://api.telegram.org/bot{token}/logOut"
    try:
        with httpx.Client(**kwargs) as client:  # type: ignore[arg-type]
            response = client.post(url)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise LocalBotApiRequiredError(
            "не удалось выполнить обязательный cloud logOut через TELEGRAM_PROXY_URL"
        ) from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        description = str(payload.get("description") if isinstance(payload, dict) else payload)
        raise LocalBotApiRequiredError(f"cloud logOut отклонён Telegram: {description[:240]}")


def _disable_cloud_transport() -> None:
    os.environ["LOCAL_BOT_API_CLOUD_FALLBACK"] = "0"
    os.environ["CLOUD_MEDIA_AUTO_COMPRESS"] = "0"
    os.environ.pop("MP3BOT_EFFECTIVE_BOT_API", None)


def _mark_local_ready(local_url: str) -> None:
    os.environ["LOCAL_BOT_API_URL"] = local_url
    os.environ["LOCAL_BOT_API_WAIT_LOCAL"] = "1"
    os.environ["MP3BOT_EFFECTIVE_BOT_API"] = "local"
    # Keep TELEGRAM_PROXY_URL in .env for the next possible cloud logOut, but
    # remove it from this process: main.py must have only the localhost route.
    os.environ["TELEGRAM_PROXY_URL"] = ""


def _log_path() -> Path:
    try:
        data_dir = process_runtime._writable_data_dir()
        return data_dir.parent / "botapi-server.log"
    except Exception:
        local = os.getenv("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "TelegramBotAPI" / "botapi-server.log"


def _failure_reason(detail: str, log_path: str | Path) -> str:
    tail = process_runtime._read_log_tail(str(log_path), max_chars=1600)
    tail_line = " | ".join(line.strip() for line in tail.splitlines()[-8:] if line.strip())
    reason = f"локальный /getMe не поднялся: {detail}"
    if tail_line:
        reason += f"; botapi-server.log: {tail_line[:900]}"
    reason += "; сервер оставлен запущенным — включи/исправь системный TUN и запусти бот повторно"
    return reason


def _wait_for_ready(getme_url: str, process, timeout_sec: int) -> tuple[bool, str]:
    ok, detail, _attempts = probe_runtime._wait_for_getme(
        getme_url,
        process,
        time.monotonic() + timeout_sec,
    )
    return ok, detail


def require_local_bot_api() -> None:
    """Make the local server healthy or abort application startup."""
    _disable_cloud_transport()
    local_url = os.getenv("LOCAL_BOT_API_URL", "").strip() or "http://127.0.0.1:8081"
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise LocalBotApiRequiredError("BOT_TOKEN не задан")

    parsed = urlparse(local_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8081
    if host not in _LOCAL_HOSTS:
        raise LocalBotApiRequiredError(
            f"LOCAL_BOT_API_URL должен указывать на localhost, получено: {local_url}"
        )

    getme_url = f"{local_url.rstrip('/')}/bot{token}/getMe"
    ok, detail = probe_runtime._probe_getme(getme_url, 2.0)
    if ok:
        _mark_local_ready(local_url)
        print(f"✅ Local Bot API готов ({detail}); лимит отправки — 2000 МБ.")
        return

    timeout_sec = _timeout_seconds()

    # A listening server may be in the middle of TDLib authorization. Restarting
    # it here destroys the temporary auth key and makes every run begin from zero.
    if probe_runtime._tcp_open(host, port, timeout_sec=0.5):
        print(f"⏳ Local Bot API уже запущен и устанавливает сессию; жду /getMe до {timeout_sec}с без перезапуска…")
        ok, detail = _wait_for_ready(getme_url, None, timeout_sec)
        if ok:
            _mark_local_ready(local_url)
            print(f"✅ Local Bot API готов ({detail}); лимит отправки — 2000 МБ, сжатие отключено.")
            return
        raise LocalBotApiRequiredError(_failure_reason(detail, _log_path()))

    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        raise LocalBotApiRequiredError("TELEGRAM_API_ID/TELEGRAM_API_HASH не заданы")

    print("🔌 Поднимаю Local Bot API для отправки без сжатия…")
    _cloud_logout(token, _cloud_proxy())

    process_runtime._ACTIVE_PROXY_URL = os.getenv("LOCAL_BOT_API_PROXY_URL", "").strip()
    process_runtime._terminate_managed_server()
    probe_runtime._wait_until_port_closes(host, port, time.monotonic() + 3.0)

    process, log_path = process_runtime._start_server(host, port)
    if process is None:
        raise LocalBotApiRequiredError(str(log_path))

    print(f"⏳ telegram-bot-api.exe запущен; жду настоящий local /getMe до {timeout_sec}с…")
    ok, detail = _wait_for_ready(getme_url, process, timeout_sec)
    if ok:
        _mark_local_ready(local_url)
        print(f"✅ Local Bot API готов ({detail}); лимит отправки — 2000 МБ, сжатие отключено.")
        return

    # Do not kill a live TDLib session on a mere timeout. The next bot run will
    # detect the open port and continue waiting without another logOut/restart.
    if process.poll() is not None:
        raise LocalBotApiRequiredError(
            f"telegram-bot-api.exe завершился с кодом {process.returncode}; "
            + _failure_reason(detail, log_path)
        )
    raise LocalBotApiRequiredError(_failure_reason(detail, log_path))
