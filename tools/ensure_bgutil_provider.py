#!/usr/bin/env python3
"""Provision the pinned browserless BgUtils PO-token runtime for yt-dlp.

The Python plugin comes from requirements-lock.txt. Its JavaScript provider is
kept outside git under .runtime/ and is cloned/built once from the matching
upstream release. Subsequent starts only verify the pinned runtime marker.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

BGUTIL_VERSION = "1.3.1"
BGUTIL_COMMIT = "7608dd51ee813b48cf9a6d68c6e42cb197ce10e0"
BGUTIL_REPOSITORY = "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = PROJECT_ROOT / ".runtime"
PROVIDER_ROOT = RUNTIME_ROOT / "bgutil-ytdlp-pot-provider"
SERVER_ROOT = PROVIDER_ROOT / "server"
GENERATE_SCRIPT = SERVER_ROOT / "build" / "generate_once.js"
VERSION_MARKER = PROVIDER_ROOT / ".mp3bot-bgutil-version"


class ProvisionError(RuntimeError):
    """Raised when the pinned provider cannot be provisioned safely."""


def _platform_command(
    command: list[str], *, platform_name: str | None = None
) -> list[str]:
    """Wrap Windows .cmd/.bat shims explicitly through cmd.exe.

    ``platform_name`` exists only so the Windows branch can be unit-tested on a
    non-Windows runner without mutating the process-wide ``os.name`` used by
    pathlib/pytest. Production callers leave it unset.
    """
    if not command:
        raise ProvisionError("empty command")
    suffix = Path(command[0]).suffix.lower()
    effective_platform = platform_name or os.name
    if effective_platform == "nt" and suffix in {".cmd", ".bat"}:
        comspec = os.environ.get("COMSPEC", "cmd.exe")
        return [comspec, "/d", "/s", "/c", subprocess.list2cmdline(command)]
    return command


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    effective = _platform_command(command)
    proc = subprocess.run(
        effective,
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
    expected = f"{BGUTIL_VERSION}@{BGUTIL_COMMIT}"
    return marker == expected and GENERATE_SCRIPT.is_file()


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
        head_process = subprocess.run(
            [git, "rev-parse", "HEAD"],
            cwd=str(staging),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        head = (head_process.stdout or "").strip().lower()
        if head_process.returncode != 0 or head != BGUTIL_COMMIT:
            raise ProvisionError(
                "Pinned bgutil tag resolved to an unexpected commit: "
                f"expected={BGUTIL_COMMIT} actual={head or 'unknown'}"
            )

        server = staging / "server"
        _run([npm, "ci"], cwd=server)
        tsc_name = "tsc.cmd" if os.name == "nt" else "tsc"
        tsc = server / "node_modules" / ".bin" / tsc_name
        if not tsc.is_file():
            raise ProvisionError(
                "bgutil npm ci не установил pinned local TypeScript compiler"
            )
        _run([str(tsc)], cwd=server)
        generated = server / "build" / "generate_once.js"
        if not generated.is_file():
            raise ProvisionError("bgutil build завершился без build/generate_once.js")

        (staging / ".mp3bot-bgutil-version").write_text(
            f"{BGUTIL_VERSION}@{BGUTIL_COMMIT}\n", encoding="utf-8"
        )
        if PROVIDER_ROOT.exists():
            shutil.rmtree(PROVIDER_ROOT)
        staging.replace(PROVIDER_ROOT)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    print(
        f"[SETUP] bgutil {BGUTIL_VERSION}@{BGUTIL_COMMIT[:8]} installed: "
        f"{SERVER_ROOT}"
    )
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
