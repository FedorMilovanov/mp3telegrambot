"""Fast, quiet bootstrap for the optional local Telegram Bot API server.

A TCP route probe is only a hint.  It must never decide that TDLib cannot work:
VPN split-routing, DNS policy and Telegram DC selection can make a generic probe
fail while the real server would connect.  The source of truth is a real local
``getMe`` response after restarting ``telegram-bot-api``.
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
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"


def _tcp_open(host: str, port: int, timeout_sec: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except OSError:
        return False


def _system_telegram_route_available(timeout_sec: float = 2.0) -> bool:
    """Return a non-authoritative route hint.

    Multiple Telegram endpoints/ports are checked because one blocked host is not
    evidence that every TDLib data-center route is unavailable.
    """
    deadline = time.monotonic() + max(0.5, timeout_sec)
    targets = (
        ("api.telegram.org", 443),
        ("149.154.167.50", 443),
        ("149.154.167.51", 443),
        ("91.108.56.130", 443),
        ("149.154.167.50", 80),
    )
    for host, port in targets:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            with socket.create_connection((host, port), timeout=min(0.7, remaining)):
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
        with log_path.open("a", encoding="utf-8", errors="replace") as marker:
            marker.write(
                f"\n===== smart bootstrap {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n"
            )
        log_handle = log_path.open("ab")
        kwargs["stdout"] = log_handle
        kwargs["stderr"] = subprocess.STDOUT
        if os.name == "nt":
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


def _read_log_tail(path_text: str, max_chars: int = 2200) -> str:
    try:
        path = Path(path_text)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[-max_chars:].strip()
    except OSError:
        return ""


def _diagnose_failure(detail: str, log_tail: str, route_hint: bool) -> str:
    combined = f"{detail}\n{log_tail}".lower()
    if any(x in combined for x in ("access is denied", "permission denied", "can't be opened")):
        return "telegram-bot-api не может записать data-dir"
    if any(x in combined for x in ("api-id", "api_id", "api-hash", "api_hash")) and any(
        x in combined for x in ("invalid", "wrong", "must provide")
    ):
        return "неверные TELEGRAM_API_ID/TELEGRAM_API_HASH"
    if any(x in combined for x in ("network is unreachable", "failed to connect", "timeout", "timed out")):
        return "TDLib не установил соединение с дата-центрами Telegram"
    if not route_hint:
        return "реальный /getMe не поднялся; предварительная проверка также не видит системный маршрут к Telegram"
    return f"локальный /getMe не поднялся ({detail})"


def _cloud_fallback_is_available() -> bool:
    return bool(
        os.getenv("TELEGRAM_PROXY_URL", "").strip()
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
    ) and _env_enabled("LOCAL_BOT_API_CLOUD_FALLBACK", True)


def _select_cloud_for_this_run(reason: str) -> None:
    os.environ["LOCAL_BOT_API_URL"] = ""
    os.environ["LOCAL_BOT_API_WAIT_LOCAL"] = "0"
    os.environ["MP3BOT_EFFECTIVE_BOT_API"] = "cloud"
    # The cloud adapter is enabled by default and will preserve delivery of an
    # oversized already-generated file through accurate ffmpeg transcoding.
    os.environ.setdefault("CLOUD_MEDIA_AUTO_COMPRESS", "1")
    print(
        f"☁️ Local Bot API недоступен: {reason}. "
        "Использую облачный API; большие видео будут автоматически сжаты до безопасного лимита."
    )


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
        os.environ["MP3BOT_EFFECTIVE_BOT_API"] = "local"
        return

    budget_sec = _clamped_int("LOCAL_BOT_API_SMART_TIMEOUT_SEC", 30, 10, 75)
    route_hint = _system_telegram_route_available(2.0)
    if not route_hint:
        print(
            "🌐 Предварительная проверка TUN-маршрута не прошла, но это только подсказка — "
            "реально перезапускаю Local Bot API и проверяю /getMe."
        )
    else:
        print("🌐 Local Bot API не отвечает — один реальный перезапуск и проверка /getMe…")

    started_at = time.monotonic()
    try:
        _terminate_stale_server()
    except Exception as exc:
        if _cloud_fallback_is_available():
            _select_cloud_for_this_run(f"не удалось остановить старый процесс: {exc}")
            return
        raise

    _wait_until_port_closes(host, port, time.monotonic() + 3.0)
    process, log_path = _start_local_server(host, port)
    if process is None:
        if _cloud_fallback_is_available():
            _select_cloud_for_this_run(log_path)
            return
        raise RuntimeError(log_path)

    ok, detail, attempts = _wait_for_getme(
        getme_url,
        process,
        time.monotonic() + budget_sec,
    )
    elapsed = time.monotonic() - started_at
    if ok:
        os.environ["LOCAL_BOT_API_WAIT_LOCAL"] = "1"
        os.environ["MP3BOT_EFFECTIVE_BOT_API"] = "local"
        print(f"✅ Local Bot API восстановлен за {elapsed:.1f}с ({detail}, проверок: {attempts}).")
        return

    log_tail = _read_log_tail(log_path)
    reason = _diagnose_failure(detail, log_tail, route_hint)
    try:
        _terminate_stale_server()
    except Exception:
        pass
    if log_tail:
        compact_tail = " | ".join(line.strip() for line in log_tail.splitlines()[-5:] if line.strip())
        if compact_tail:
            print(f"🧾 Local Bot API log: {compact_tail[:900]}")
    if _cloud_fallback_is_available():
        _select_cloud_for_this_run(f"{reason}; попытка заняла {elapsed:.1f}с")
        return
    raise RuntimeError(reason)
