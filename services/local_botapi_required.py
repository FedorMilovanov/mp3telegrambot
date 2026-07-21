#!/usr/bin/env python3
"""One-path startup for the mandatory local Telegram Bot API.

The bot needs the local server for original-quality uploads up to 2000 MB. This
module deliberately has no cloud fallback and no media recompression path:

1. use an already healthy local server;
2. otherwise call the required cloud ``logOut`` once;
3. restart the managed local server once;
4. continue only after a real local ``getMe`` succeeds.

If the local server cannot reach Telegram (for example, the system TUN/VPN is
off), startup fails clearly instead of silently changing transport or quality.
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import httpx

from services import local_botapi_bootstrap as probe_runtime
from services import local_botapi_runtime as process_runtime

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class LocalBotApiRequiredError(RuntimeError):
    """The mandatory local Bot API could not be made ready."""


def _timeout_seconds() -> int:
    try:
        value = int(os.getenv("LOCAL_BOT_API_REQUIRED_TIMEOUT_SEC", "90").strip() or "90")
    except ValueError:
        value = 90
    return max(30, min(value, 180))


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

    ok, detail, _attempts = probe_runtime._wait_for_getme(
        getme_url,
        process,
        time.monotonic() + _timeout_seconds(),
    )
    if ok:
        _mark_local_ready(local_url)
        print(f"✅ Local Bot API готов ({detail}); лимит отправки — 2000 МБ, сжатие отключено.")
        return

    process_runtime._terminate_managed_server()
    tail = process_runtime._read_log_tail(str(log_path), max_chars=1200)
    tail_line = " | ".join(line.strip() for line in tail.splitlines()[-6:] if line.strip())
    reason = f"локальный /getMe не поднялся: {detail}"
    if tail_line:
        reason += f"; botapi-server.log: {tail_line[:700]}"
    raise LocalBotApiRequiredError(reason)
