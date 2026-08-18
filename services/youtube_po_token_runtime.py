#!/usr/bin/env python3
"""Fail-closed readiness checks for YouTube maximum-quality PO-token routing."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from pathlib import Path


WPC_DISTRIBUTION = "yt-dlp-getpot-wpc"
NODRIVER_DISTRIBUTION = "nodriver"


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
            "Chrome/Chromium-compatible browser не найден для автоматического "
            "YouTube PO Token. Установи Chrome/Chromium/Edge/Brave в стандартное "
            "место и перезапусти бот."
        ) from exc

    path = Path(str(raw_path or "")).expanduser()
    if not raw_path or not path.is_file():
        raise YouTubePoTokenRuntimeError(
            "Chrome/Chromium-compatible browser не найден для автоматического "
            "YouTube PO Token. Установи Chrome/Chromium/Edge/Brave в стандартное "
            "место и перезапусти бот."
        )
    return path


def require_youtube_po_token_runtime() -> YouTubePoTokenRuntime:
    """Require the production mweb/GVS token provider without quality fallback."""
    provider_version = _distribution_version(WPC_DISTRIBUTION)
    nodriver_version = _distribution_version(NODRIVER_DISTRIBUTION)
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
