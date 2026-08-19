#!/usr/bin/env python3
"""Fail-closed readiness checks for browserless YouTube PO-token routing."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path


BGUTIL_DISTRIBUTION = "bgutil-ytdlp-pot-provider"
LEGACY_WPC_DISTRIBUTION = "yt-dlp-getpot-wpc"
BGUTIL_MODULE = "yt_dlp_plugins.extractor.getpot_bgutil"
BGUTIL_EXPECTED_VERSION = "1.3.1"
BGUTIL_EXPECTED_COMMIT = "7608dd51ee813b48cf9a6d68c6e42cb197ce10e0"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROVIDER_HOME = PROJECT_ROOT / ".runtime" / "bgutil-ytdlp-pot-provider" / "server"
_PROVIDER_PROBE_TIMEOUT_SEC = 20
_PROVIDER_PROBE_CODE = """\
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
    provider_commit: str
    node_version: str
    provider_home: Path

    def status_text(self) -> str:
        return (
            f"bgutil {self.provider_version}@{self.provider_commit[:8]}; "
            f"node={self.node_version}; browserless=on"
        )


def _require_no_legacy_browser_provider() -> None:
    try:
        legacy_version = metadata.version(LEGACY_WPC_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return
    raise YouTubePoTokenRuntimeError(
        "Обнаружен устаревший browser-based PO Token provider "
        f"{LEGACY_WPC_DISTRIBUTION} {legacy_version}. Удали его из project venv: "
        ".\\.venv\\Scripts\\python.exe -m pip uninstall -y "
        "yt-dlp-getpot-wpc nodriver; затем снова запусти Start Bot.bat. "
        "Chrome fallback в production запрещён."
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


def _require_bgutil_module(expected_version: str) -> None:
    """Validate the plugin in a child interpreter without polluting yt-dlp state."""
    command = [
        sys.executable,
        "-c",
        _PROVIDER_PROBE_CODE,
        BGUTIL_MODULE,
        expected_version,
    ]
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROVIDER_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise YouTubePoTokenRuntimeError(
            "bgutil PO Token plugin установлен, но его изолированная проверка "
            "совместимости не запускается. Переустанови requirements-lock.txt."
        ) from exc

    if process.returncode == 0:
        return

    detail = _probe_tail(process)
    if process.returncode == 3:
        actual_version = str(getattr(process, "stdout", "") or "").strip() or "unknown"
        raise YouTubePoTokenRuntimeError(
            "bgutil PO Token plugin имеет рассинхронизированную версию: "
            f"distribution={expected_version} module={actual_version}. "
            "Переустанови requirements-lock.txt."
        )

    suffix = f" Детали: {detail}" if detail else ""
    raise YouTubePoTokenRuntimeError(
        "bgutil PO Token plugin установлен, но не импортируется вместе с текущим "
        f"yt-dlp. Переустанови requirements-lock.txt.{suffix}"
    )


def _provider_home() -> Path:
    configured = os.getenv("BGUTIL_PROVIDER_HOME", "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_PROVIDER_HOME


def _require_provider_build() -> Path:
    home = _provider_home()
    generated = home / "build" / "generate_once.js"
    if not generated.is_file():
        raise YouTubePoTokenRuntimeError(
            "browserless bgutil runtime не собран. Запусти 'Start Bot.bat': "
            "он установит pinned bgutil provider в .runtime без Chrome."
        )
    marker = home.parent / ".mp3bot-bgutil-version"
    try:
        marker_value = marker.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise YouTubePoTokenRuntimeError(
            "browserless bgutil runtime не имеет commit marker; "
            "перезапусти Start Bot.bat для безопасной пересборки"
        ) from exc
    expected_marker = f"{BGUTIL_EXPECTED_VERSION}@{BGUTIL_EXPECTED_COMMIT}"
    if marker_value != expected_marker:
        raise YouTubePoTokenRuntimeError(
            "browserless bgutil runtime не соответствует pinned commit: "
            f"expected={expected_marker} actual={marker_value or 'empty'}"
        )
    return home


def _require_node() -> str:
    node = shutil.which("node")
    if not node:
        raise YouTubePoTokenRuntimeError(
            "Node.js не найден; browserless bgutil требует Node.js >=20"
        )
    try:
        process = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise YouTubePoTokenRuntimeError("Не удалось запустить Node.js") from exc
    version = (process.stdout or process.stderr or "").strip().lstrip("v")
    match = re.match(r"(\d+)", version)
    if process.returncode or not match:
        raise YouTubePoTokenRuntimeError("Не удалось определить версию Node.js")
    if int(match.group(1)) < 20:
        raise YouTubePoTokenRuntimeError(
            f"Node.js {version} < 20; обнови Node.js"
        )
    return version


def require_youtube_po_token_runtime() -> YouTubePoTokenRuntime:
    """Require mweb + automatic browserless GVS token generation, fail closed."""
    _require_no_legacy_browser_provider()
    provider_version = _distribution_version(BGUTIL_DISTRIBUTION)
    if provider_version != BGUTIL_EXPECTED_VERSION:
        raise YouTubePoTokenRuntimeError(
            f"ожидался bgutil {BGUTIL_EXPECTED_VERSION}, установлен {provider_version}; "
            "переустанови requirements-lock.txt"
        )
    _require_bgutil_module(provider_version)
    provider_home = _require_provider_build()
    node_version = _require_node()
    return YouTubePoTokenRuntime(
        provider_version=provider_version,
        provider_commit=BGUTIL_EXPECTED_COMMIT,
        node_version=node_version,
        provider_home=provider_home,
    )


__all__ = [
    "BGUTIL_DISTRIBUTION",
    "BGUTIL_EXPECTED_VERSION",
    "BGUTIL_EXPECTED_COMMIT",
    "BGUTIL_MODULE",
    "DEFAULT_PROVIDER_HOME",
    "LEGACY_WPC_DISTRIBUTION",
    "YouTubePoTokenRuntime",
    "YouTubePoTokenRuntimeError",
    "require_youtube_po_token_runtime",
]
