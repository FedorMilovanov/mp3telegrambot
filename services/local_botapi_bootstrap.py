"""Fast, quiet bootstrap for the optional local Telegram Bot API server.

This module runs before importing :mod:`main`.  It prevents a stale
``telegram-bot-api.exe`` process from keeping port 8081 open while its TDLib
connection is dead — the exact state in which the old startup loop spent about
three minutes printing twelve identical ``getMe`` timeout messages.

The policy is deliberately conservative:

* a healthy local server is left untouched;
* an unhealthy local process is restarted at most once;
* all retries share one real elapsed-time deadline;
* there are no per-attempt INFO messages;
* if local recovery fails and a cloud proxy is configured, LOCAL_BOT_API_URL is
  cleared for this process only, so the current run starts immediately in cloud
  mode.  The value in ``.env`` is not changed, therefore the next bot restart
  tries local mode again.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _env_enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def _clamped_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip() or str(default))
    except ValueError:
        value = default
    return max(low, min(value, high))


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    # Environment HTTP(S)_PROXY values must never intercept localhost.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _probe_getme(url: str, timeout_sec: float) -> tuple[bool, str]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "Connection": "close"},
        method="GET",
    )
    try:
        with _no_proxy_opener().open(request, timeout=max(0.2, timeout_sec)) as response:
            raw = response.read(64 * 1024).decode("utf-8", "replace")
        payload = json.loads(raw)
        if payload.get("ok") is True:
            username = str((payload.get("result") or {}).get("username") or "?")
            return True, f"@{username}"
        return False, str(payload)[:240]
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")[:240]
        except Exception:
            body = ""
        return False, f"HTTP {exc.code}: {body}"
    except Exception as exc:  # timeout, invalid JSON, refused connection, etc.
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"


def _tcp_open(host: str, port: int, timeout_sec: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except OSError:
        return False


def _system_telegram_route_available(timeout_sec: float = 1.25) -> bool:
    """Cheap hint that a system/TUN route is available.

    This is not treated as proof that TDLib will connect.  It only prevents an
    obviously route-less machine from spending the complete recovery budget.
    """
    deadline = time.monotonic() + max(0.3, timeout_sec)
    for host in ("api.telegram.org", "149.154.167.50"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            with socket.create_connection((host, 443), timeout=min(0.8, remaining)):
                return True
        except OSError:
            continue
    return False


def _terminate_stale_server() -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/IM", "telegram-bot-api.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=8,
        )
    else:
        subprocess.run(
            ["pkill", "-f", "telegram-bot-api"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=8,
        )


def _wait_until_port_closes(host: str, port: int, deadline: float) -> None:
    while time.monotonic() < deadline:
        if not _tcp_open(host, port, timeout_sec=0.15):
            return
        time.sleep(0.1)


def _writable_data_dir() -> Path:
    configured = os.getenv("LOCAL_BOT_API_DATA_DIR", "").strip()
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    fallback = (
        Path(local_app_data) / "TelegramBotAPI" / "data"
        if local_app_data
        else Path.home() / ".telegram-bot-api" / "data"
    )
    candidate = Path(configured) if configured else fallback

    def ensure_writable(path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            marker = path / ".mp3bot-write-test"
            marker.write_text("ok", encoding="utf-8")
            marker.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    if ensure_writable(candidate):
        return candidate
    fallback.mkdir(parents=True, exist_ok=True)
    os.environ["LOCAL_BOT_API_DATA_DIR"] = str(fallback)
    return fallback


def _start_local_server(host: str, port: int) -> tuple[subprocess.Popen[bytes] | None, str]:
    if host not in _LOCAL_HOSTS:
        return None, "remote LOCAL_BOT_API_URL is not managed automatically"

    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    exe = os.getenv(
        "LOCAL_BOT_API_EXE",
        r"C:\Program Files\TelegramBotAPI\telegram-bot-api.exe",
    ).strip()
    if not api_id or not api_hash:
        return None, "TELEGRAM_API_ID/TELEGRAM_API_HASH are missing"
    if not Path(exe).exists():
        return None, f"telegram-bot-api executable not found: {exe}"

    data_dir = _writable_data_dir()
    log_path = data_dir.parent / "botapi-server.log"
    command = [
        exe,
        f"--api-id={api_id}",
        f"--api-hash={api_hash}",
        "--local",
        f"--http-port={port}",
        f"--dir={data_dir}",
        f"--log={log_path}",
        "--verbosity=2",
    ]
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "env": dict(os.environ),
    }
    log_handle = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("ab")
        kwargs["stdout"] = log_handle
        kwargs["stderr"] = subprocess.STDOUT
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            kwargs["creationflags"] = 0x8 | 0x200 | 0x08000000
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]
        return process, str(log_path)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        if log_handle is not None:
            log_handle.close()


def _wait_for_getme(
    url: str,
    process: subprocess.Popen[bytes] | None,
    deadline: float,
    probe: Callable[[str, float], tuple[bool, str]] = _probe_getme,
) -> tuple[bool, str, int]:
    """Poll quietly with short probes and one shared monotonic deadline."""
    attempts = 0
    last_error = "not checked"
    delay = 0.15
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, last_error, attempts
        if process is not None and process.poll() is not None:
            return False, f"telegram-bot-api exited with code {process.returncode}", attempts

        attempts += 1
        ok, detail = probe(url, min(1.5, remaining))
        if ok:
            return True, detail, attempts
        last_error = detail

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, last_error, attempts
        time.sleep(min(delay, remaining))
        delay = min(2.0, delay * 1.6)


def _cloud_fallback_is_available() -> bool:
    return bool(
        os.getenv("TELEGRAM_PROXY_URL", "").strip()
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
    ) and _env_enabled("LOCAL_BOT_API_CLOUD_FALLBACK", True)


def _select_cloud_for_this_run(reason: str) -> None:
    # core.globals is imported only after this function returns, so clearing the
    # variable here cleanly selects cloud mode without changing the user's .env.
    os.environ["LOCAL_BOT_API_URL"] = ""
    os.environ["LOCAL_BOT_API_WAIT_LOCAL"] = "0"
    print(f"☁️ Local Bot API недоступен: {reason}. Этот запуск сразу использует облачный API.")


def prepare_local_bot_api() -> None:
    """Recover local Bot API once, quietly, before heavy application imports."""
    local_url = os.getenv("LOCAL_BOT_API_URL", "").strip()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not local_url or not token or not _env_enabled("LOCAL_BOT_API_SMART_BOOTSTRAP", True):
        return

    parsed = urlparse(local_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    if host not in _LOCAL_HOSTS:
        return

    # These settings never proxy TDLib in the official binary.  Remove them
    # from this process to avoid misleading startup warnings; TELEGRAM_PROXY_URL
    # remains available for the cloud fallback.
    for name in (
        "LOCAL_BOT_API_PROXY_URL",
        "LOCAL_BOT_API_TDLIB_PROXY_TYPE",
        "LOCAL_BOT_API_PROXY_SERVER",
        "LOCAL_BOT_API_PROXY_PORT",
        "LOCAL_BOT_API_PROXY_LOGIN",
        "LOCAL_BOT_API_PROXY_PASSWORD",
        "LOCAL_BOT_API_PROXY_SECRET",
    ):
        os.environ[name] = ""

    getme_url = f"{local_url.rstrip('/')}/bot{token}/getMe"
    ok, detail = _probe_getme(getme_url, 1.2)
    if ok:
        # main.py will perform its normal probe, which now returns immediately.
        return

    budget_sec = _clamped_int("LOCAL_BOT_API_SMART_TIMEOUT_SEC", 25, 8, 60)
    route_ok = _system_telegram_route_available(1.5)
    if not route_ok and _cloud_fallback_is_available():
        try:
            _terminate_stale_server()
        except Exception:
            pass
        _select_cloud_for_this_run("нет системного TUN/VPN-маршрута к Telegram")
        return

    print("🌐 Local Bot API не отвечает — один быстрый перезапуск без повторяющихся сообщений…")
    started_at = time.monotonic()
    try:
        _terminate_stale_server()
    except Exception as exc:
        if _cloud_fallback_is_available():
            _select_cloud_for_this_run(f"не удалось остановить старый процесс: {exc}")
            return

    _wait_until_port_closes(host, port, time.monotonic() + 3.0)
    process, start_detail = _start_local_server(host, port)
    if process is None:
        if _cloud_fallback_is_available():
            _select_cloud_for_this_run(start_detail)
        return

    ok, detail, attempts = _wait_for_getme(
        getme_url,
        process,
        time.monotonic() + budget_sec,
    )
    elapsed = time.monotonic() - started_at
    if ok:
        os.environ["LOCAL_BOT_API_WAIT_LOCAL"] = "1"
        print(f"✅ Local Bot API восстановлен за {elapsed:.1f}с ({detail}, проверок: {attempts}).")
        return

    if _cloud_fallback_is_available():
        _select_cloud_for_this_run(
            f"не восстановился за {elapsed:.1f}с ({detail}); лог: {start_detail}"
        )
