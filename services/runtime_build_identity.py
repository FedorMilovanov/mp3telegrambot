#!/usr/bin/env python3
"""Privacy-safe build and dependency identity for startup and admin diagnostics."""
from __future__ import annotations

import html
import os
import platform
import re
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping

RUNTIME_BUILD_IDENTITY_POLICY = "privacy-safe-runtime-build-identity-v1"
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DISTRIBUTIONS = ("google-genai", "faster-whisper", "ctranslate2")


def _distribution_version(name: str) -> str:
    try:
        value = metadata.version(name).strip()
    except metadata.PackageNotFoundError:
        return "missing"
    except Exception:
        return "unknown"
    return value[:80] or "unknown"


def _validated_sha(value: object) -> str:
    text = str(value or "").strip()
    return text.lower() if _SHA_RE.fullmatch(text) else "unknown"


def _git_command(*args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(_PROJECT_ROOT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _git_identity() -> tuple[str, str]:
    env_sha = _validated_sha(
        os.getenv("MP3BOT_BUILD_SHA")
        or os.getenv("GITHUB_SHA")
        or os.getenv("RENDER_GIT_COMMIT")
    )
    head = _git_command("rev-parse", "HEAD")
    git_sha = "unknown"
    if head is not None and head.returncode == 0:
        git_sha = _validated_sha((head.stdout or "").strip())
    build_sha = env_sha if env_sha != "unknown" else git_sha

    status = _git_command("status", "--porcelain", "--untracked-files=no")
    dirty = "unknown"
    if status is not None and status.returncode == 0:
        dirty = "yes" if (status.stdout or "").strip() else "no"
    return build_sha, dirty


def runtime_build_identity_payload(
    *,
    build_sha: str | None = None,
    dirty: str | None = None,
    python_version: str | None = None,
    versions: Mapping[str, str] | None = None,
    factory_model: str | None = None,
    gemini_client_count: int | None = None,
) -> dict[str, Any]:
    """Return a strict allowlist; never include keys, paths, URLs, or env dumps."""
    if build_sha is None or dirty is None:
        detected_sha, detected_dirty = _git_identity()
        build_sha = detected_sha if build_sha is None else build_sha
        dirty = detected_dirty if dirty is None else dirty
    safe_sha = _validated_sha(build_sha)
    safe_dirty = str(dirty or "unknown").strip().casefold()
    if safe_dirty not in {"yes", "no", "unknown"}:
        safe_dirty = "unknown"

    package_versions = {
        name: str((versions or {}).get(name) or _distribution_version(name))[:80]
        for name in _DISTRIBUTIONS
    }
    model = str(
        factory_model
        or os.getenv("SHORTS_FACTORY_MODEL")
        or os.getenv("GEMINI_MODEL")
        or "unknown"
    ).strip()[:80] or "unknown"

    if gemini_client_count is None:
        try:
            from core.globals import GEMINI_CLIENTS

            gemini_client_count = len(GEMINI_CLIENTS)
        except Exception:
            gemini_client_count = 0
    try:
        client_count = max(0, min(int(gemini_client_count), 100))
    except (TypeError, ValueError, OverflowError):
        client_count = 0

    return {
        "policy": RUNTIME_BUILD_IDENTITY_POLICY,
        "build_sha": safe_sha,
        "dirty": safe_dirty,
        "python": str(python_version or platform.python_version()).strip()[:80] or "unknown",
        "packages": package_versions,
        "factory_model": model,
        "gemini_client_count": client_count,
    }


def runtime_build_identity_log_line(payload: Mapping[str, Any] | None = None) -> str:
    data = runtime_build_identity_payload() if payload is None else dict(payload)
    packages = dict(data.get("packages") or {})
    sha = str(data.get("build_sha") or "unknown")
    if sha != "unknown":
        sha = sha[:12]
    return (
        "🧾 Runtime build: "
        f"sha={sha} dirty={data.get('dirty', 'unknown')} "
        f"python={data.get('python', 'unknown')} "
        f"google-genai={packages.get('google-genai', 'unknown')} "
        f"faster-whisper={packages.get('faster-whisper', 'unknown')} "
        f"ctranslate2={packages.get('ctranslate2', 'unknown')} "
        f"factory_model={data.get('factory_model', 'unknown')} "
        f"gemini_clients={data.get('gemini_client_count', 0)} "
        f"[{data.get('policy', RUNTIME_BUILD_IDENTITY_POLICY)}]"
    )


def runtime_build_identity_html_lines(
    payload: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    data = runtime_build_identity_payload() if payload is None else dict(payload)
    packages = dict(data.get("packages") or {})
    sha = str(data.get("build_sha") or "unknown")
    sha = sha[:12] if sha != "unknown" else "unknown"
    return (
        "🧾 Build: "
        f"<code>{html.escape(sha)}</code> · dirty=<code>{html.escape(str(data.get('dirty', 'unknown')))}</code> · "
        f"Python <code>{html.escape(str(data.get('python', 'unknown')))}</code>",
        "🧠 Factory runtime: "
        f"model=<code>{html.escape(str(data.get('factory_model', 'unknown')))}</code> · "
        f"clients={int(data.get('gemini_client_count') or 0)} · "
        f"genai=<code>{html.escape(str(packages.get('google-genai', 'unknown')))}</code> · "
        f"whisper=<code>{html.escape(str(packages.get('faster-whisper', 'unknown')))}</code> · "
        f"ctranslate2=<code>{html.escape(str(packages.get('ctranslate2', 'unknown')))}</code>",
    )


__all__ = [
    "RUNTIME_BUILD_IDENTITY_POLICY",
    "runtime_build_identity_html_lines",
    "runtime_build_identity_log_line",
    "runtime_build_identity_payload",
]
