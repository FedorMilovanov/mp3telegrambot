#!/usr/bin/env python3
"""Fail-closed readiness checks for browserless YouTube PO-token routing."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path


BGUTIL_DISTRIBUTION = "bgutil-ytdlp-pot-provider"
BGUTIL_MODULE = "yt_dlp_plugins.extractor.getpot_bgutil"
BGUTIL_VERSION = "1.3.1"
BGUTIL_COMMIT = "7608dd51ee813b48cf9a6d68c6e42cb197ce10e0"
ROOT = Path(__file__).resolve().parents[1]
BGUTIL_HOME = ROOT / ".runtime" / "bgutil-ytdlp-pot-provider"
BGUTIL_SERVER = BGUTIL_HOME / "server"
BGUTIL_MARKER = BGUTIL_HOME / ".mp3bot-runtime.json"
_PLUGIN_PROBE_TIMEOUT_SEC = 20
_PLUGIN_PROBE_CODE = """\
import importlib
import sys
module = importlib.import_module(sys.argv[1])
version = str(getattr(module, "__version__", "") or "").strip()
print(version)
raise SystemExit(0 if version == sys.argv[2] else 3)
"""


class YouTubePoTokenRuntimeError(RuntimeError):
    """Raised when the required automatic YouTube PO-token path is unavailable."""


@dataclass(frozen=True)
class YouTubePoTokenRuntime:
    provider_version: str
    node_path: Path
    server_home: Path

    def status_text(self) -> str:
        return (
            f"bgutil {self.provider_version}; script-node; "
            f"browser=none; runtime={self.server_home.name}"
        )


def _distribution_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError as exc:
        raise YouTubePoTokenRuntimeError(
            f"{name} не установлен. Запусти 'Start Bot.bat', чтобы установить "
            "requirements-lock.txt."
        ) from exc


def _probe_tail(process: object, limit: int = 900) -> str:
    stderr = str(getattr(process, "stderr", "") or "").strip()
    stdout = str(getattr(process, "stdout", "") or "").strip()
    return (stderr or stdout)[-limit:]


def _require_bgutil_module(expected_version: str) -> None:
    """Validate the plugin in a child interpreter, keeping yt-dlp registry clean."""
    try:
        process = subprocess.run(
            [sys.executable, "-c", _PLUGIN_PROBE_CODE, BGUTIL_MODULE, expected_version],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PLUGIN_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise YouTubePoTokenRuntimeError(
            "bgutil PO Token plugin installed but compatibility probe cannot run"
        ) from exc
    if process.returncode == 0:
        return
    detail = _probe_tail(process)
    if process.returncode == 3:
        actual = str(process.stdout or "").strip() or "unknown"
        raise YouTubePoTokenRuntimeError(
            f"bgutil plugin version mismatch: distribution={expected_version} module={actual}"
        )
    raise YouTubePoTokenRuntimeError(
        "bgutil PO Token plugin is not importable with current yt-dlp"
        + (f": {detail}" if detail else "")
    )


def _require_node() -> Path:
    raw = shutil.which("node")
    if not raw:
        raise YouTubePoTokenRuntimeError(
            "Node.js not found; browserless bgutil PO Token runtime requires Node.js >=20"
        )
    path = Path(raw)
    try:
        process = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        value = (process.stdout or process.stderr or "").strip().lstrip("v")
        major = int(value.split(".", 1)[0])
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise YouTubePoTokenRuntimeError("Cannot validate Node.js version") from exc
    if process.returncode != 0 or major < 20:
        raise YouTubePoTokenRuntimeError(
            f"Node.js >=20 required for bgutil; detected {value or 'unknown'}"
        )
    return path


def _require_built_runtime() -> Path:
    main_js = BGUTIL_SERVER / "build" / "main.js"
    once_js = BGUTIL_SERVER / "build" / "generate_once.js"
    if not BGUTIL_MARKER.is_file() or not main_js.is_file() or not once_js.is_file():
        raise YouTubePoTokenRuntimeError(
            "bgutil JS runtime is not built. Run 'Start Bot.bat'; it bootstraps the "
            "pinned provider before starting the bot."
        )
    try:
        marker = json.loads(BGUTIL_MARKER.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise YouTubePoTokenRuntimeError("bgutil runtime marker is unreadable") from exc
    expected = {"version": BGUTIL_VERSION, "commit": BGUTIL_COMMIT}
    if marker != expected:
        raise YouTubePoTokenRuntimeError(
            "bgutil JS runtime does not match the pinned provider version/commit"
        )
    return BGUTIL_SERVER


def require_youtube_po_token_runtime() -> YouTubePoTokenRuntime:
    """Require browserless mweb/GVS token support without quality fallback."""
    provider_version = _distribution_version(BGUTIL_DISTRIBUTION)
    if provider_version != BGUTIL_VERSION:
        raise YouTubePoTokenRuntimeError(
            f"bgutil distribution mismatch: expected={BGUTIL_VERSION} actual={provider_version}"
        )
    _require_bgutil_module(provider_version)
    node_path = _require_node()
    server_home = _require_built_runtime()
    return YouTubePoTokenRuntime(
        provider_version=provider_version,
        node_path=node_path,
        server_home=server_home,
    )


__all__ = [
    "BGUTIL_COMMIT",
    "BGUTIL_SERVER",
    "BGUTIL_VERSION",
    "YouTubePoTokenRuntime",
    "YouTubePoTokenRuntimeError",
    "require_youtube_po_token_runtime",
]
