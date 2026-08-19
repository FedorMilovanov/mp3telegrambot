#!/usr/bin/env python3
"""Provision one exact browserless bgutil source tree for yt-dlp.

The Python plugin and JavaScript token generator are both loaded from the same
repo-local source checkout under .runtime/. Nothing from the bgutil PyPI wheel is
required at runtime, which prevents plugin/server drift and keeps one immutable
upstream commit as the supply-chain identity.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

BGUTIL_VERSION = "1.3.1"
BGUTIL_COMMIT = "a0be2352807e3bd6991f09d2cab685a0ab825b26"
BGUTIL_REPOSITORY = "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = PROJECT_ROOT / ".runtime"
PROVIDER_ROOT = RUNTIME_ROOT / "bgutil-ytdlp-pot-provider"
SERVER_ROOT = PROVIDER_ROOT / "server"
PLUGIN_ROOT = PROVIDER_ROOT / "plugin"
PLUGIN_ENTRY = PLUGIN_ROOT / "yt_dlp_plugins" / "extractor" / "getpot_bgutil.py"
GENERATE_SCRIPT = SERVER_ROOT / "build" / "generate_once.js"
VERSION_MARKER = PROVIDER_ROOT / ".mp3bot-bgutil-version"


class ProvisionError(RuntimeError):
    """Raised when the pinned provider cannot be provisioned safely."""


def _platform_command(
    command: list[str], *, platform_name: str | None = None
) -> list[str]:
    """Wrap Windows .cmd/.bat shims explicitly through cmd.exe."""
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
            "Node.js не найден. Exact-source YouTube PO Token runtime требует "
            "Node.js >=22."
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
    if major < 22:
        raise ProvisionError(f"Node.js {version} < 22; обнови Node.js")
    return node


def _runtime_is_current() -> bool:
    try:
        marker = VERSION_MARKER.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    expected = f"{BGUTIL_VERSION}@{BGUTIL_COMMIT}"
    return (
        marker == expected
        and GENERATE_SCRIPT.is_file()
        and PLUGIN_ENTRY.is_file()
    )


def _checkout_exact_source(git: str, staging: Path) -> None:
    """Fetch exactly the reviewed commit instead of trusting a moving branch/tag."""
    staging.mkdir(parents=True, exist_ok=False)
    _run([git, "init"], cwd=staging)
    _run([git, "remote", "add", "origin", BGUTIL_REPOSITORY], cwd=staging)
    _run([git, "fetch", "--depth", "1", "origin", BGUTIL_COMMIT], cwd=staging)
    _run([git, "checkout", "--detach", "FETCH_HEAD"], cwd=staging)


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
    staging = RUNTIME_ROOT / f".bgutil-{BGUTIL_COMMIT[:12]}.tmp"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    print(
        "[SETUP] Installing exact-source browserless YouTube PO Token runtime "
        f"{BGUTIL_VERSION}@{BGUTIL_COMMIT[:8]}..."
    )
    try:
        _checkout_exact_source(git, staging)
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
                "Fetched bgutil source does not match the reviewed commit: "
                f"expected={BGUTIL_COMMIT} actual={head or 'unknown'}"
            )

        if not (staging / "plugin" / "yt_dlp_plugins" / "extractor" / "getpot_bgutil.py").is_file():
            raise ProvisionError("bgutil source checkout does not contain the yt-dlp plugin")

        server = staging / "server"
        _run([npm, "ci", "--no-audit", "--no-fund"], cwd=server)
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
        f"[SETUP] Exact-source bgutil {BGUTIL_VERSION}@{BGUTIL_COMMIT[:8]} ready: "
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
