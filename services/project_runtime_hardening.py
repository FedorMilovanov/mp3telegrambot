"""Narrow runtime hardening for the long-running Telegram bot process.

This module owns cross-cutting invariants that must hold even when no later
LiveDub installer runs: process singleton, bounded caches, atomic MP3
conversion, optional-stage isolation and stale audio cleanup.
"""
from __future__ import annotations

import atexit
import asyncio
import inspect
import json
import logging
import os
import shutil
import subprocess as _subprocess
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_INSTALLED = False
_EARLY_LOCK_ACQUIRED = False
_LOCK_PATH = Path(__file__).resolve().parent.parent / "bot.lock"
_CONVERSION_POSTCONDITION = "atomic-mp3-conversion-postcondition-v1"


def _enabled(name: str = "PROJECT_RUNTIME_HARDENING", default: bool = True) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def singleton_lock_path() -> Path:
    """Return one absolute lock path, independent of launch working directory."""
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
    if not _enabled() or not _enabled("MP3BOT_EARLY_SINGLETON", True):
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


def bind_main_singleton(main_module: ModuleType) -> None:
    """Make the legacy singleton reuse the absolute early lock."""
    if hasattr(main_module, "_SINGLETON_LOCK_PATH"):
        main_module._SINGLETON_LOCK_PATH = singleton_lock_path()


class BoundedLRUDict(OrderedDict):
    """Minimal dict-compatible LRU used by existing cache call sites."""

    def __init__(self, *args: Any, max_entries: int = 256, **kwargs: Any) -> None:
        self.max_entries = max(8, int(max_entries))
        super().__init__()
        self.update(*args, **kwargs)
        self._trim()

    def __getitem__(self, key: Any) -> Any:
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def get(self, key: Any, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        self._trim()

    def _trim(self) -> None:
        while len(self) > self.max_entries:
            self.popitem(last=False)


def _active_video_ids() -> set[str]:
    try:
        import core.globals as globals_module

        with globals_module._video_locks_mutex:
            return {
                str(video_id)
                for video_id, lock in globals_module._video_processing_locks.items()
                if lock.locked()
            }
    except Exception:
        return set()


def _belongs_to_active_video(path: Path, active_ids: Iterable[str]) -> bool:
    name = path.name
    return any(
        name == f"{video_id}.mp3" or name.startswith(f"{video_id}_")
        for video_id in active_ids
    )


def cleanup_stale_cached_audio(max_age_days: int | None = None) -> int:
    """Delete expired MP3 cache files without touching active processing."""
    try:
        import core.database as database
        import core.globals as globals_module

        if max_age_days is None:
            raw = os.getenv("AUDIO_CACHE_TTL_DAYS", "").strip()
            max_age_days = int(raw) if raw else int(database.CACHE_TTL_DAYS)
        max_age_days = max(1, min(int(max_age_days), 3650))
        root = Path(globals_module.DOWNLOAD_DIR)
    except (TypeError, ValueError, OSError):
        return 0

    if not root.exists():
        return 0
    cutoff = time.time() - max_age_days * 86400
    active_ids = _active_video_ids()
    deleted = 0
    for path in root.glob("*.mp3"):
        try:
            if not path.is_file() or ".part-" in path.name:
                continue
            if _belongs_to_active_video(path, active_ids):
                continue
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            deleted += 1
        except OSError:
            continue
    if deleted:
        logger.info(
            "🧹 Audio cache: удалено %d MP3 старше %d дней",
            deleted,
            max_age_days,
        )
    return deleted


def _ffprobe_audio_ok(path: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.exists() or path.stat().st_size <= 10 * 1024:
        return False
    try:
        proc = _subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
        )
        if proc.returncode != 0:
            return False
        payload = json.loads(proc.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration") or 0)
        has_audio = any(
            stream.get("codec_type") == "audio"
            for stream in payload.get("streams") or []
        )
        return has_audio and duration > 0
    except Exception:
        return False


def _guarded_mp3_command(command: Any) -> tuple[list[str], Path, Path] | None:
    if not isinstance(command, (list, tuple)) or len(command) < 6:
        return None
    cmd = [str(part) for part in command]
    executable = Path(cmd[0]).name.lower()
    if "ffmpeg" not in executable or "-b:a" not in cmd:
        return None
    try:
        bitrate = cmd[cmd.index("-b:a") + 1].lower()
        source = Path(cmd[cmd.index("-i") + 1])
        output = Path(cmd[-1])
    except (ValueError, IndexError):
        return None
    if bitrate not in {"64k", "64"} or not output.name.lower().endswith("_64.mp3"):
        return None
    try:
        if source.resolve() == output.resolve():
            return None
    except OSError:
        return None
    return cmd, source, output


def _append_process_error(stderr: Any, message: str) -> Any:
    if isinstance(stderr, bytes):
        separator = b"\n" if stderr else b""
        return stderr + separator + message.encode("utf-8")
    return f"{stderr or ''}\n{message}".strip()


class _SubprocessProxy:
    """Delegate subprocess except for one atomic legacy MP3 conversion call."""

    conversion_postcondition_policy = _CONVERSION_POSTCONDITION

    def __getattr__(self, name: str) -> Any:
        return getattr(_subprocess, name)

    def run(self, *args: Any, **kwargs: Any) -> _subprocess.CompletedProcess[Any]:
        command = args[0] if args else kwargs.get("args")
        guarded = _guarded_mp3_command(command)
        if guarded is None:
            return _subprocess.run(*args, **kwargs)

        cmd, source, output = guarded
        output.parent.mkdir(parents=True, exist_ok=True)
        if (
            output.exists()
            and output.stat().st_mtime >= source.stat().st_mtime
            and _ffprobe_audio_ok(output)
        ):
            return _subprocess.CompletedProcess(cmd, 0, b"", b"")

        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        temp = output.with_name(
            f"{output.stem}.part-{os.getpid()}-{uuid.uuid4().hex[:8]}.mp3"
        )
        guarded_cmd = list(cmd)
        guarded_cmd[-1] = str(temp)
        guarded_args = (guarded_cmd, *args[1:]) if args else args
        guarded_kwargs = dict(kwargs)
        if not args:
            guarded_kwargs["args"] = guarded_cmd

        try:
            proc = _subprocess.run(*guarded_args, **guarded_kwargs)
            valid_output = _ffprobe_audio_ok(temp)
            if proc.returncode == 0 and valid_output:
                os.replace(temp, output)
                return proc

            logger.warning(
                "MP3 64k conversion rejected: rc=%s valid=%s source=%s",
                proc.returncode,
                valid_output,
                source.name,
            )
            if proc.returncode != 0:
                return proc

            message = (
                "MP3 conversion returned success without a valid output: "
                f"{output}"
            )
            return _subprocess.CompletedProcess(
                proc.args,
                1,
                proc.stdout,
                _append_process_error(proc.stderr, message),
            )
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass


def _safe_optional_wrapper(
    name: str,
    original: Callable[..., Any],
    default: Any,
) -> Callable[..., Any]:
    if getattr(original, "_mp3bot_optional_isolation", False):
        return original

    async def safe(*args: Any, **kwargs: Any) -> Any:
        try:
            result = original(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Optional stage %s failed but pipeline continues: %s",
                name,
                exc,
                exc_info=True,
            )
            return default() if callable(default) else default

    safe._mp3bot_optional_isolation = True  # type: ignore[attr-defined]
    safe.__name__ = getattr(original, "__name__", name)
    return safe


def _install_pipeline_adapters() -> list[str]:
    import pipelines.main_pipeline as pipeline

    installed: list[str] = []
    current_cache = getattr(pipeline, "_LIVEDUB_TITLE_CACHE", None)
    if isinstance(current_cache, dict) and not isinstance(current_cache, BoundedLRUDict):
        try:
            max_entries = int(
                os.getenv("LIVEDUB_TITLE_CACHE_MAX", "256").strip() or "256"
            )
        except ValueError:
            max_entries = 256
        pipeline._LIVEDUB_TITLE_CACHE = BoundedLRUDict(
            current_cache,
            max_entries=max_entries,
        )
        installed.append(f"title-cache<={max(8, max_entries)}")

    optional_defaults: dict[str, Any] = {
        "process_and_send_shorts": False,
        "process_and_send_clips": False,
        "process_and_send_montage": False,
        "process_and_send_highlights": False,
        "create_extras_candidates": lambda: {
            "montage_candidates": [],
            "highlights_candidates": [],
        },
    }
    for name, default in optional_defaults.items():
        original = getattr(pipeline, name, None)
        if callable(original) and not getattr(
            original,
            "_mp3bot_optional_isolation",
            False,
        ):
            setattr(pipeline, name, _safe_optional_wrapper(name, original, default))
            installed.append(f"isolate:{name}")

    if not isinstance(getattr(pipeline, "subprocess", None), _SubprocessProxy):
        pipeline.subprocess = _SubprocessProxy()
        installed.append("atomic-mp3-64k")
    return installed


def _install_periodic_audio_cleanup() -> bool:
    import core.utils as utils

    original = getattr(utils, "cleanup_stale_downloads", None)
    if not callable(original) or getattr(
        original,
        "_mp3bot_audio_cache_cleanup",
        False,
    ):
        return False

    def cleanup_with_audio(*args: Any, **kwargs: Any) -> int:
        video_deleted = int(original(*args, **kwargs) or 0)
        return video_deleted + cleanup_stale_cached_audio()

    cleanup_with_audio._mp3bot_audio_cache_cleanup = True  # type: ignore[attr-defined]
    utils.cleanup_stale_downloads = cleanup_with_audio
    return True


def install_project_runtime_hardening(
    main_module: ModuleType | None = None,
) -> None:
    """Install all post-import adapters exactly once."""
    global _INSTALLED
    if _INSTALLED or not _enabled():
        return
    if main_module is not None:
        bind_main_singleton(main_module)
    installed = _install_pipeline_adapters()
    if _install_periodic_audio_cleanup():
        installed.append("audio-cache-expiry")
    _INSTALLED = True
    logger.info(
        "🛡 Project runtime hardening: ✅ %s",
        ", ".join(installed) or "already installed",
    )
