#!/usr/bin/env python3
"""Provision the pinned browserless BgUtils PO-token runtime for yt-dlp.

The Python plugin comes from requirements-lock.txt.  Its JavaScript provider is
kept outside git under .runtime/ and is cloned/built once from the matching
upstream release.  Subsequent starts only verify the pinned runtime marker.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

BGUTIL_VERSION = "1.3.1"
BGUTIL_REPOSITORY = "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = PROJECT_ROOT / ".runtime"
PROVIDER_ROOT = RUNTIME_ROOT / "bgutil-ytdlp-pot-provider"
SERVER_ROOT = PROVIDER_ROOT / "server"
GENERATE_SCRIPT = SERVER_ROOT / "build" / "generate_once.js"
VERSION_MARKER = PROVIDER_ROOT / ".mp3bot-bgutil-version"


class ProvisionError(RuntimeError):
    """Raised when the pinned provider cannot be provisioned safely."""


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    proc = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode:
        raise ProvisionError(
            f"command failed ({proc.returncode}): {' '.join(command)}"
        )


def _node_executable() -> str:
    node = shutil.which("node")
    if not node:
        raise ProvisionError(
            "Node.js не найден. Для browserless YouTube PO Token нужен Node.js >=20 "
            "(проект уже рекомендует Node >=22 для yt-dlp/VOT)."
        )
    try:
        version = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        ).stdout.strip().lstrip("v")
        major = int(version.split(".", 1)[0])
    except (OSError, ValueError, IndexError) as exc:
        raise ProvisionError("Не удалось определить версию Node.js") from exc
    if major < 20:
        raise ProvisionError(f"Node.js {version} < 20; обнови Node.js")
    return node


def _runtime_is_current() -> bool:
    try:
        marker = VERSION_MARKER.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return marker == BGUTIL_VERSION and GENERATE_SCRIPT.is_file()


def ensure_bgutil_provider() -> Path:
    """Return the ready provider server directory, provisioning it if needed."""
    _node_executable()
    if _runtime_is_current():
        return SERVER_ROOT

    git = shutil.which("git")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not git:
        raise ProvisionError("git не найден; он нужен для pinned bgutil runtime")
    if not npm:
        raise ProvisionError("npm не найден; установи Node.js с npm")

    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    staging = RUNTIME_ROOT / f".bgutil-{BGUTIL_VERSION}.tmp"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    print(f"[SETUP] Installing browserless YouTube PO Token provider bgutil {BGUTIL_VERSION}...")
    try:
        _run([
            git,
            "clone",
            "--depth",
            "1",
            "--branch",
            BGUTIL_VERSION,
            BGUTIL_REPOSITORY,
            str(staging),
        ])
        server = staging / "server"
        _run([npm, "ci"], cwd=server)
        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if not npx:
            raise ProvisionError("npx не найден после установки Node.js/npm")
        _run([npx, "tsc"], cwd=server)
        generated = server / "build" / "generate_once.js"
        if not generated.is_file():
            raise ProvisionError("bgutil build завершился без build/generate_once.js")

        (staging / ".mp3bot-bgutil-version").write_text(
            BGUTIL_VERSION + "\n", encoding="utf-8"
        )
        if PROVIDER_ROOT.exists():
            shutil.rmtree(PROVIDER_ROOT)
        staging.replace(PROVIDER_ROOT)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    print(f"[SETUP] bgutil {BGUTIL_VERSION} installed: {SERVER_ROOT}")
    return SERVER_ROOT


def main() -> int:
    try:
        path = ensure_bgutil_provider()
    except ProvisionError as exc:
        print(f"ERROR: YouTube PO Token runtime setup failed: {exc}", file=sys.stderr)
        return 1
    print(f"[SETUP] Browserless YouTube PO Token runtime ready: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
