"""Windows-safe runtime hardening for :mod:`local_botapi_bootstrap`.

The original bootstrap owns the route/getMe policy. This adapter narrows the
process-management surface and makes documented configuration real:

* ``LOCAL_BOT_API_AUTOSTART=0`` is respected before any process is killed;
* only the managed PID or telegram-bot-api listener on the configured port is
  terminated — never every telegram-bot-api.exe on the machine;
* a supported HTTP(S) ``LOCAL_BOT_API_PROXY_URL`` is passed to the binary;
* the child PID is persisted atomically;
* BOT_TOKEN/API hash/proxy passwords are redacted from diagnostic log tails.
"""

from __future__ import annotations

import csv
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from services import local_botapi_bootstrap as legacy

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_ACTIVE_PROXY_URL = ""


def _enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def _local_url() -> tuple[str, int]:
    parsed = urlparse(os.getenv("LOCAL_BOT_API_URL", "").strip())
    return parsed.hostname or "127.0.0.1", parsed.port or 80


def _fallback_data_dir() -> Path:
    local = os.getenv("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "TelegramBotAPI" / "data"
    return Path.home() / ".telegram-bot-api" / "data"


def _writable_data_dir() -> Path:
    configured = os.getenv("LOCAL_BOT_API_DATA_DIR", "").strip()
    candidates = [Path(configured)] if configured else []
    fallback = _fallback_data_dir()
    if fallback not in candidates:
        candidates.append(fallback)

    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            marker = path / ".mp3bot-write-test"
            marker.write_text("ok", encoding="utf-8")
            marker.unlink(missing_ok=True)
            os.environ["LOCAL_BOT_API_DATA_DIR"] = str(path)
            return path
        except OSError:
            continue
    raise RuntimeError("нет доступной для записи папки Local Bot API")


def _pid_path(data_dir: Path | None = None) -> Path:
    directory = data_dir or _writable_data_dir()
    return directory.parent / "botapi-server.pid"


def _read_pid(path: Path) -> int:
    try:
        pid = int(path.read_text(encoding="ascii").strip())
        return pid if pid > 0 else 0
    except (OSError, ValueError):
        return 0


def _write_pid(path: Path, pid: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(str(pid), encoding="ascii")
        os.replace(temp, path)
    except OSError:
        pass


def _remove_pid(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _windows_listener_pids(port: int) -> set[int]:
    try:
        proc = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
    except Exception:
        return set()
    if proc.returncode != 0:
        return set()

    result: set[int] = set()
    for raw in (proc.stdout or "").splitlines():
        line = raw.strip()
        if "LISTEN" not in line.upper():
            continue
        parts = re.split(r"\s+", line)
        if len(parts) < 4:
            continue
        try:
            local_port = int(parts[1].rsplit(":", 1)[1])
            pid = int(parts[-1])
        except (IndexError, ValueError):
            continue
        if local_port == port and pid > 0:
            result.add(pid)
    return result


def _windows_name(pid: int) -> str:
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
        )
        rows = (proc.stdout or "").strip().splitlines()
        if proc.returncode != 0 or not rows or rows[0].lower().startswith("info:"):
            return ""
        row = next(csv.reader([rows[0]]))
        return (row[0] if row else "").strip().lower()
    except Exception:
        return ""


def _posix_name(pid: int) -> str:
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        return (proc.stdout or "").strip().lower()
    except Exception:
        return ""


def _is_botapi(pid: int) -> bool:
    if pid <= 0:
        return False
    name = _windows_name(pid) if os.name == "nt" else _posix_name(pid)
    return "telegram-bot-api" in name


def _kill_pid(pid: int) -> bool:
    try:
        if os.name == "nt":
            cmd = ["taskkill", "/F", "/T", "/PID", str(pid)]
        else:
            cmd = ["kill", "-TERM", str(pid)]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _terminate_managed_server() -> list[int]:
    """Kill only our PID or a verified Bot API listener on our port."""
    _host, port = _local_url()
    try:
        data_dir = _writable_data_dir()
    except Exception:
        data_dir = None
    pid_file = _pid_path(data_dir) if data_dir else _fallback_data_dir().parent / "botapi-server.pid"
    managed = _read_pid(pid_file)
    candidates = {managed} if managed else set()
    if os.name == "nt":
        candidates.update(_windows_listener_pids(port))

    killed: list[int] = []
    for pid in sorted(candidates):
        if _is_botapi(pid) and _kill_pid(pid):
            killed.append(pid)
    if managed and (managed in killed or not _is_botapi(managed)):
        _remove_pid(pid_file)

    if not killed and _enabled("LOCAL_BOT_API_ALLOW_GLOBAL_KILL", False):
        try:
            cmd = (
                ["taskkill", "/F", "/T", "/IM", "telegram-bot-api.exe"]
                if os.name == "nt"
                else ["pkill", "-f", "telegram-bot-api"]
            )
            proc = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
            if proc.returncode == 0:
                killed.append(-1)
        except Exception:
            pass
    return killed


def _proxy_args() -> list[str]:
    proxy = _ACTIVE_PROXY_URL.strip()
    if not proxy:
        return []
    parsed = urlparse(proxy)
    if (parsed.scheme or "").lower() in {"http", "https"} and parsed.hostname:
        return [f"--proxy={proxy}"]
    return []


def _start_server(host: str, port: int):
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    exe = os.getenv(
        "LOCAL_BOT_API_EXE",
        r"C:\Program Files\TelegramBotAPI\telegram-bot-api.exe",
    ).strip()
    if not api_id or not api_hash:
        return None, "TELEGRAM_API_ID/TELEGRAM_API_HASH are missing"
    if not Path(exe).is_file():
        return None, f"telegram-bot-api executable not found: {exe}"

    try:
        data_dir = _writable_data_dir()
    except Exception as exc:
        return None, str(exc)
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
        *_proxy_args(),
    ]
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "env": dict(os.environ),
    }
    handle = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", errors="replace") as marker:
            marker.write(
                f"\n===== runtime bootstrap {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n"
            )
        handle = log_path.open("ab")
        kwargs["stdout"] = handle
        kwargs["stderr"] = subprocess.STDOUT
        if os.name == "nt":
            kwargs["creationflags"] = 0x8 | 0x200 | 0x08000000
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]
        _write_pid(_pid_path(data_dir), process.pid)
        return process, str(log_path)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        if handle is not None:
            handle.close()


def _redact(text: str) -> str:
    result = str(text or "")
    secrets = [
        os.getenv("BOT_TOKEN", "").strip(),
        os.getenv("TELEGRAM_API_HASH", "").strip(),
        os.getenv("LOCAL_BOT_API_PROXY_PASSWORD", "").strip(),
    ]
    if _ACTIVE_PROXY_URL:
        password = urlparse(_ACTIVE_PROXY_URL).password
        if password:
            secrets.append(password)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "***")
    return re.sub(r"/bot\d+:[A-Za-z0-9_-]+", "/bot***", result)


def _read_log_tail(path_text: str, max_chars: int = 2200) -> str:
    try:
        path = Path(path_text)
        if not path.is_file():
            return ""
        return _redact(
            path.read_text(encoding="utf-8", errors="replace")[-max_chars:].strip()
        )
    except OSError:
        return ""


def _cloud_available() -> bool:
    return bool(
        os.getenv("TELEGRAM_PROXY_URL", "").strip()
        or os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
    ) and _enabled("LOCAL_BOT_API_CLOUD_FALLBACK", True)


def prepare_local_bot_api() -> None:
    """Run the existing policy with safe process and config primitives."""
    global _ACTIVE_PROXY_URL
    local_url = os.getenv("LOCAL_BOT_API_URL", "").strip()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not local_url or not token:
        return

    if not _enabled("LOCAL_BOT_API_AUTOSTART", True):
        getme = f"{local_url.rstrip('/')}/bot{token}/getMe"
        ok, _detail = legacy._probe_getme(getme, 1.2)
        if ok:
            os.environ["MP3BOT_EFFECTIVE_BOT_API"] = "local"
            return
        reason = "LOCAL_BOT_API_AUTOSTART=0 и сервер не ответил на /getMe"
        if _cloud_available():
            legacy._select_cloud_for_this_run(reason)
            return
        raise RuntimeError(reason)

    _ACTIVE_PROXY_URL = os.getenv("LOCAL_BOT_API_PROXY_URL", "").strip()
    original_proxy = _ACTIVE_PROXY_URL
    original_terminate = legacy._terminate_stale_server
    original_start = legacy._start_local_server
    original_read_tail = legacy._read_log_tail
    try:
        legacy._terminate_stale_server = _terminate_managed_server
        legacy._start_local_server = _start_server
        legacy._read_log_tail = _read_log_tail
        legacy.prepare_local_bot_api()
    finally:
        legacy._terminate_stale_server = original_terminate
        legacy._start_local_server = original_start
        legacy._read_log_tail = original_read_tail
        if original_proxy:
            os.environ["LOCAL_BOT_API_PROXY_URL"] = original_proxy
