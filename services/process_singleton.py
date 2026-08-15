#!/usr/bin/env python3
"""Early process-singleton ownership for the bot launcher.

The lock is acquired before importing ``main`` so a second process cannot race
Telegram long-polling or initialize heavy runtime state. This module performs no
post-import mutation.
"""
from __future__ import annotations

import atexit
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_LOCK_PATH = Path(__file__).resolve().parent.parent / "bot.lock"
_EARLY_LOCK_ACQUIRED = False


def _enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def singleton_lock_path() -> Path:
    """Return one absolute lock path independent of launch working directory."""
    return _LOCK_PATH


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def release_early_singleton() -> None:
    global _EARLY_LOCK_ACQUIRED
    if not _EARLY_LOCK_ACQUIRED:
        return
    path = singleton_lock_path()
    try:
        if path.exists() and _read_lock_pid(path) == os.getpid():
            path.unlink()
    except OSError:
        pass
    _EARLY_LOCK_ACQUIRED = False


def acquire_early_singleton() -> bool:
    """Atomically reserve the process before Local API and heavy imports."""
    global _EARLY_LOCK_ACQUIRED
    if not _enabled("MP3BOT_EARLY_SINGLETON", True):
        return True

    path = singleton_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    pid_text = str(os.getpid())

    for _attempt in range(3):
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            old_pid = _read_lock_pid(path)
            if old_pid == os.getpid():
                _EARLY_LOCK_ACQUIRED = True
                return True
            if old_pid and _pid_is_running(old_pid):
                logger.error(
                    "❌ Уже запущен другой экземпляр бота (PID %d, lock=%s)",
                    old_pid,
                    path,
                )
                return False
            try:
                if _read_lock_pid(path) == old_pid:
                    path.unlink()
            except OSError:
                time.sleep(0.05)
            continue
        except OSError as exc:
            logger.warning("⚠️ Atomic singleton lock unavailable (%s): %s", path, exc)
            return True
        else:
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(pid_text)
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            _EARLY_LOCK_ACQUIRED = True
            atexit.register(release_early_singleton)
            return True
    return False


__all__ = [
    "acquire_early_singleton",
    "release_early_singleton",
    "singleton_lock_path",
]
