#!/usr/bin/env python3
"""Install the pinned bgutil PO-token server into the repo-local runtime cache.

The Python yt-dlp plugin is pinned by requirements-lock.txt. The companion JS
server is source-built once from the matching signed upstream tag and then reused
on subsequent bot starts. Nothing is fetched during normal media processing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BGUTIL_VERSION = "1.3.1"
BGUTIL_COMMIT = "7608dd51ee813b48cf9a6d68c6e42cb197ce10e0"
BGUTIL_REPOSITORY = "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git"
ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / ".runtime"
TARGET = RUNTIME_ROOT / "bgutil-ytdlp-pot-provider"
SERVER = TARGET / "server"
MARKER = TARGET / ".mp3bot-runtime.json"


class BootstrapError(RuntimeError):
    pass


def _run(command: list[str], *, cwd: Path | None = None, capture: bool = False) -> subprocess.CompletedProcess[str]:
    kwargs = dict(
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if capture:
        kwargs["capture_output"] = True
    process = subprocess.run(command, **kwargs)
    if process.returncode != 0:
        detail = ""
        if capture:
            detail = (process.stderr or process.stdout or "").strip()[-1200:]
        suffix = f"\n{detail}" if detail else ""
        raise BootstrapError(f"Command failed ({process.returncode}): {' '.join(command)}{suffix}")
    return process


def _tool_version(executable: str, args: list[str]) -> tuple[int, ...]:
    path = shutil.which(executable)
    if not path:
        raise BootstrapError(f"Required executable not found in PATH: {executable}")
    process = _run([path, *args], capture=True)
    text = (process.stdout or process.stderr or "").strip()
    import re
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text)
    if not match:
        raise BootstrapError(f"Cannot parse {executable} version: {text[:200]}")
    return tuple(int(x or 0) for x in match.groups())


def _require_tools() -> None:
    _tool_version("git", ["--version"])
    if _tool_version("node", ["--version"]) < (20, 0, 0):
        raise BootstrapError("bgutil requires Node.js >=20")
    if _tool_version("npm", ["--version"]) < (9, 0, 0):
        raise BootstrapError("bgutil requires npm >=9")


def _marker_ok() -> bool:
    if not MARKER.is_file():
        return False
    if not (SERVER / "build" / "main.js").is_file():
        return False
    if not (SERVER / "build" / "generate_once.js").is_file():
        return False
    try:
        data = json.loads(MARKER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data == {"version": BGUTIL_VERSION, "commit": BGUTIL_COMMIT}


def bootstrap() -> Path:
    if _marker_ok():
        print(f"[SETUP] bgutil PO runtime {BGUTIL_VERSION} is current.")
        return SERVER

    _require_tools()
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    temp_parent = Path(tempfile.mkdtemp(prefix="bgutil-bootstrap-", dir=RUNTIME_ROOT))
    checkout = temp_parent / "provider"
    try:
        print(f"[SETUP] Installing pinned bgutil PO runtime {BGUTIL_VERSION}...")
        _run([
            shutil.which("git") or "git",
            "clone",
            "--depth", "1",
            "--single-branch",
            "--branch", BGUTIL_VERSION,
            BGUTIL_REPOSITORY,
            str(checkout),
        ])
        head = _run(
            [shutil.which("git") or "git", "rev-parse", "HEAD"],
            cwd=checkout,
            capture=True,
        ).stdout.strip().lower()
        if head != BGUTIL_COMMIT:
            raise BootstrapError(
                "Pinned bgutil tag resolved to an unexpected commit: "
                f"expected={BGUTIL_COMMIT} actual={head or 'unknown'}"
            )

        server = checkout / "server"
        _run([shutil.which("npm") or "npm", "ci", "--no-audit", "--no-fund"], cwd=server)
        # Use the project's pinned TypeScript tool from node_modules; npm exec does
        # not fetch an unpinned compiler when the dependency already exists.
        _run([shutil.which("npm") or "npm", "exec", "--", "tsc"], cwd=server)
        if not (server / "build" / "main.js").is_file():
            raise BootstrapError("bgutil build/main.js was not produced")
        if not (server / "build" / "generate_once.js").is_file():
            raise BootstrapError("bgutil build/generate_once.js was not produced")

        marker = checkout / ".mp3bot-runtime.json"
        marker.write_text(
            json.dumps({"version": BGUTIL_VERSION, "commit": BGUTIL_COMMIT}, sort_keys=True),
            encoding="utf-8",
        )

        old = RUNTIME_ROOT / "bgutil-ytdlp-pot-provider.old"
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        if TARGET.exists():
            TARGET.replace(old)
        checkout.replace(TARGET)
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        print(f"[SETUP] bgutil PO runtime {BGUTIL_VERSION} installed and verified.")
        return SERVER
    finally:
        shutil.rmtree(temp_parent, ignore_errors=True)


def main() -> int:
    try:
        bootstrap()
        return 0
    except (BootstrapError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: bgutil PO runtime bootstrap failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
