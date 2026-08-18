#!/usr/bin/env python3
"""Fail-closed readiness checks for YouTube maximum-quality PO-token routing."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path


WPC_DISTRIBUTION = "yt-dlp-getpot-wpc"
WPC_MODULE = "yt_dlp_plugins.extractor.getpot_wpc"
NODRIVER_DISTRIBUTION = "nodriver"
_WPC_PROBE_TIMEOUT_SEC = 20
_WPC_PROBE_CODE = """\
import importlib
import sys

module = importlib.import_module(sys.argv[1])
module_version = str(getattr(module, "__version__", "") or "").strip()
print(module_version)
raise SystemExit(0 if not module_version or module_version == sys.argv[2] else 3)
"""


class YouTubePoTokenRuntimeError(RuntimeError):
    """Raised when the required automatic YouTube PO-token path is unavailable."""


@dataclass(frozen=True)
class YouTubePoTokenRuntime:
    provider_version: str
    nodriver_version: str
    browser_path: Path

    def status_text(self) -> str:
        return (
            f"WPC {self.provider_version}; nodriver {self.nodriver_version}; "
            f"browser={self.browser_path.name}"
        )


def _distribution_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError as exc:
        raise YouTubePoTokenRuntimeError(
            f"{name} не установлен. Запусти 'Start Bot.bat' или установи "
            "requirements-lock.txt перед запуском bot_new.py."
        ) from exc


def _probe_tail(process: object, limit: int = 900) -> str:
    stderr = str(getattr(process, "stderr", "") or "").strip()
    stdout = str(getattr(process, "stdout", "") or "").strip()
    detail = stderr or stdout
    return detail[-limit:]


def _require_wpc_module(expected_version: str) -> None:
    """Validate WPC in an isolated interpreter without polluting yt-dlp's registry.

    Importing a yt-dlp extractor plugin in the bot process registers its provider
    globally. yt-dlp's normal plugin loader then imports/registers it again and
    raises ``PoTokenProvider WPC already registered``. Probe compatibility in a
    child interpreter instead, leaving the production process untouched for the
    official plugin loader.
    """
    command = [
        sys.executable,
        "-c",
        _WPC_PROBE_CODE,
        WPC_MODULE,
        expected_version,
    ]
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_WPC_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise YouTubePoTokenRuntimeError(
            "WPC PO Token provider установлен, но его изолированная проверка "
            "совместимости не запускается. Переустанови requirements-lock.txt."
        ) from exc

    if process.returncode == 0:
        return

    detail = _probe_tail(process)
    if process.returncode == 3:
        actual_version = str(getattr(process, "stdout", "") or "").strip() or "unknown"
        raise YouTubePoTokenRuntimeError(
            "WPC PO Token provider имеет рассинхронизированную версию: "
            f"distribution={expected_version} module={actual_version}. "
            "Переустанови requirements-lock.txt."
        )

    suffix = f" Детали: {detail}" if detail else ""
    raise YouTubePoTokenRuntimeError(
        "WPC PO Token provider установлен, но не импортируется вместе с текущим "
        f"yt-dlp/nodriver. Переустанови requirements-lock.txt.{suffix}"
    )


def _discover_chromium_browser() -> Path:
    try:
        from nodriver.core.config import find_chrome_executable
    except Exception as exc:  # pragma: no cover - dependency failure path
        raise YouTubePoTokenRuntimeError(
            "nodriver не импортируется; автоматический YouTube PO Token недоступен"
        ) from exc

    try:
        raw_path = find_chrome_executable()
    except Exception as exc:
        raise YouTubePoTokenRuntimeError(
            "Chrome/Chromium не найден для автоматического YouTube PO Token. "
            "WPC использует Chromium-браузер для выдачи video-bound GVS token; "
            "установи Google Chrome или Chromium и перезапусти бот."
        ) from exc

    path = Path(str(raw_path or "")).expanduser()
    if not raw_path or not path.is_file():
        raise YouTubePoTokenRuntimeError(
            "Chrome/Chromium не найден для автоматического YouTube PO Token. "
            "WPC использует Chromium-браузер для выдачи video-bound GVS token; "
            "установи Google Chrome или Chromium и перезапусти бот."
        )
    return path


def require_youtube_po_token_runtime() -> YouTubePoTokenRuntime:
    """Require the production mweb/GVS token provider without quality fallback."""
    provider_version = _distribution_version(WPC_DISTRIBUTION)
    nodriver_version = _distribution_version(NODRIVER_DISTRIBUTION)
    _require_wpc_module(provider_version)
    browser_path = _discover_chromium_browser()
    return YouTubePoTokenRuntime(
        provider_version=provider_version,
        nodriver_version=nodriver_version,
        browser_path=browser_path,
    )


__all__ = [
    "YouTubePoTokenRuntime",
    "YouTubePoTokenRuntimeError",
    "require_youtube_po_token_runtime",
]
