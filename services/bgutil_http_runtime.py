#!/usr/bin/env python3
"""Own one persistent exact-source bgutil HTTP PO-token provider.

The upstream script provider starts Node and imports the full SessionManager for
each yt-dlp process, including a hard 15-second ``--version`` probe. On slower
Windows paths that probe can time out before any PO token is generated. The
upstream HTTP provider is designed to keep the same SessionManager resident and
has much higher yt-dlp provider preference.

This module does not download or patch bgutil. Provisioning remains owned by
``tools/ensure_bgutil_provider.py`` and the exact commit marker remains the
supply-chain identity. We only start ``server/build/main.js`` and prove its
``/ping`` version before production continues. A server that was not spawned by
this Python process is never trusted merely because it reports the same version.
"""
from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4416
DEFAULT_BASE_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
_STARTUP_TIMEOUT_SEC = 60.0
_PING_TIMEOUT_SEC = 2.0
_STOP_GRACE_SEC = 3.0

_OWNED_PROCESS: subprocess.Popen[Any] | None = None
_CLEANUP_REGISTERED = False


class BgutilHttpRuntimeError(RuntimeError):
    """Raised when the persistent HTTP provider cannot be proven ready."""


def _ping(base_url: str = DEFAULT_BASE_URL, *, timeout: float = _PING_TIMEOUT_SEC) -> dict[str, object] | None:
    """Return bgutil /ping JSON without honoring ambient proxy settings."""
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(Request(f"{base_url.rstrip('/')}/ping"), timeout=timeout) as response:
            if int(getattr(response, "status", 0) or 0) != 200:
                return None
            payload = json.loads(response.read().decode("utf-8", errors="strict"))
    except (OSError, HTTPError, URLError, UnicodeError, json.JSONDecodeError, TimeoutError):
        return None
    return payload if isinstance(payload, dict) else None


def _require_ping_version(payload: dict[str, object], *, expected_version: str) -> None:
    version = str(payload.get("version") or "").strip()
    if version != expected_version:
        raise BgutilHttpRuntimeError(
            "Локальный bgutil HTTP provider отвечает с неверной версией: "
            f"expected={expected_version} actual={version or 'unknown'}"
        )


def _spawn_kwargs() -> dict[str, object]:
    if os.name == "nt":
        create_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {"creationflags": create_group | no_window}
    return {"start_new_session": True}


def _force_stop_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_STOP_GRACE_SEC,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass
        return

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except OSError:
            pass


def stop_owned_bgutil_http_runtime() -> None:
    """Stop only the provider process created by this Python process."""
    global _OWNED_PROCESS
    process = _OWNED_PROCESS
    _OWNED_PROCESS = None
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=_STOP_GRACE_SEC)
        return
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        pass
    _force_stop_tree(process)
    try:
        process.wait(timeout=_STOP_GRACE_SEC)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _register_cleanup_once() -> None:
    global _CLEANUP_REGISTERED
    if not _CLEANUP_REGISTERED:
        atexit.register(stop_owned_bgutil_http_runtime)
        _CLEANUP_REGISTERED = True


def ensure_bgutil_http_runtime(
    *,
    node_executable: str,
    server_home: Path,
    expected_version: str,
    base_url: str = DEFAULT_BASE_URL,
    startup_timeout: float = _STARTUP_TIMEOUT_SEC,
) -> str:
    """Start or reuse only this process's exact-source HTTP provider."""
    global _OWNED_PROCESS

    existing = _ping(base_url)
    if existing is not None:
        _require_ping_version(existing, expected_version=expected_version)
        if _OWNED_PROCESS is not None and _OWNED_PROCESS.poll() is None:
            return base_url
        raise BgutilHttpRuntimeError(
            "Порт bgutil HTTP provider уже занят неуправляемым процессом. "
            "Совпадения version недостаточно для доказательства pinned source identity; "
            "останови старый процесс/бот и запусти Start Bot.bat снова."
        )

    server_home = Path(server_home).resolve()
    main_script = server_home / "build" / "main.js"
    if not main_script.is_file():
        raise BgutilHttpRuntimeError(
            "bgutil exact-source build не содержит server/build/main.js; "
            "перезапусти Start Bot.bat для полной пересборки runtime"
        )

    if _OWNED_PROCESS is not None and _OWNED_PROCESS.poll() is None:
        raise BgutilHttpRuntimeError(
            "Ранее запущенный управляемый bgutil HTTP provider перестал отвечать на /ping"
        )

    try:
        process = subprocess.Popen(
            [str(node_executable), str(main_script), "--port", str(DEFAULT_PORT)],
            cwd=str(server_home),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            **_spawn_kwargs(),
        )
    except OSError as exc:
        raise BgutilHttpRuntimeError(
            "Не удалось запустить pinned bgutil HTTP provider через Node.js"
        ) from exc

    _OWNED_PROCESS = process
    _register_cleanup_once()
    deadline = time.monotonic() + max(1.0, float(startup_timeout))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            code = process.returncode
            _OWNED_PROCESS = None
            raise BgutilHttpRuntimeError(
                f"bgutil HTTP provider завершился во время startup (rc={code})"
            )
        payload = _ping(base_url)
        if payload is not None:
            try:
                _require_ping_version(payload, expected_version=expected_version)
            except Exception:
                stop_owned_bgutil_http_runtime()
                raise
            return base_url
        time.sleep(0.25)

    stop_owned_bgutil_http_runtime()
    raise BgutilHttpRuntimeError(
        f"bgutil HTTP provider не стал готов за {startup_timeout:g}с: {base_url}/ping"
    )


__all__ = [
    "BgutilHttpRuntimeError",
    "DEFAULT_BASE_URL",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "ensure_bgutil_http_runtime",
    "stop_owned_bgutil_http_runtime",
]
