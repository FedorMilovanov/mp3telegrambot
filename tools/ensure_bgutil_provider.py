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
import time
import uuid
from contextlib import contextmanager
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
NODE_MODULES = SERVER_ROOT / "node_modules"
VERSION_MARKER = PROVIDER_ROOT / ".mp3bot-bgutil-version"
PROVISION_LOCK = RUNTIME_ROOT / ".bgutil-provision.lock"

_GIT_TIMEOUT_SEC = 120
_NPM_TIMEOUT_SEC = 360
_BUILD_TIMEOUT_SEC = 180
_SCRIPT_PROBE_TIMEOUT_SEC = 20
_LOCK_WAIT_SEC = 90
_LOCK_STALE_SEC = 900


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


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = _BUILD_TIMEOUT_SEC,
) -> None:
    effective = _platform_command(command)
    try:
        proc = subprocess.run(
            effective,
            cwd=str(cwd) if cwd else None,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProvisionError(
            f"command timed out after {timeout}s: {' '.join(command)}"
        ) from exc
    except OSError as exc:
        raise ProvisionError(f"command could not start: {' '.join(command)}") from exc
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
        process = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        version = (process.stdout or process.stderr or "").strip().lstrip("v")
        major = int(version.split(".", 1)[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        raise ProvisionError("Не удалось определить версию Node.js") from exc
    if process.returncode:
        raise ProvisionError("Не удалось запустить Node.js")
    if major < 22:
        raise ProvisionError(f"Node.js {version} < 22; обнови Node.js")
    return node


def _runtime_files_current() -> bool:
    try:
        marker = VERSION_MARKER.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    expected = f"{BGUTIL_VERSION}@{BGUTIL_COMMIT}"
    return (
        marker == expected
        and GENERATE_SCRIPT.is_file()
        and PLUGIN_ENTRY.is_file()
        and NODE_MODULES.is_dir()
    )


def _runtime_is_current() -> bool:
    """Backward-compatible filesystem-only readiness predicate used by tests."""
    return _runtime_files_current()


def _require_script_version(
    node: str,
    *,
    script: Path = GENERATE_SCRIPT,
    cwd: Path = SERVER_ROOT,
) -> str:
    """Offline smoke-test the compiled script and its installed dependency graph."""
    try:
        process = subprocess.run(
            [node, str(script), "--version"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SCRIPT_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProvisionError("bgutil runtime --version smoke test timed out") from exc
    except OSError as exc:
        raise ProvisionError("Не удалось запустить compiled bgutil runtime") from exc

    stdout = (process.stdout or "").strip()
    stderr = (process.stderr or "").strip()
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    actual = lines[-1] if lines else ""
    if process.returncode != 0 or actual != BGUTIL_VERSION:
        detail = (stderr or stdout)[-900:]
        suffix = f" Детали: {detail}" if detail else ""
        raise ProvisionError(
            "compiled bgutil runtime не проходит offline smoke test: "
            f"expected={BGUTIL_VERSION} actual={actual or 'unknown'}.{suffix}"
        )
    return actual


@contextmanager
def _provision_lock():
    """Serialize rebuilds so two launchers cannot delete each other's staging tree."""
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_WAIT_SEC
    acquired = False
    while not acquired:
        try:
            fd = os.open(str(PROVISION_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - PROVISION_LOCK.stat().st_mtime
            except OSError:
                age = 0
            if age > _LOCK_STALE_SEC:
                try:
                    PROVISION_LOCK.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise ProvisionError(
                    "Другая установка bgutil runtime не завершилась за 90 секунд"
                )
            time.sleep(0.25)
            continue
        except OSError as exc:
            raise ProvisionError("Не удалось создать bgutil provision lock") from exc
        else:
            try:
                os.write(fd, f"pid={os.getpid()}\n".encode("ascii", errors="strict"))
            finally:
                os.close(fd)
            acquired = True

    try:
        yield
    finally:
        try:
            PROVISION_LOCK.unlink()
        except OSError:
            pass


def _checkout_exact_source(git: str, staging: Path) -> None:
    """Fetch exactly the reviewed commit instead of trusting a moving branch/tag."""
    staging.mkdir(parents=True, exist_ok=False)
    _run([git, "init"], cwd=staging, timeout=_GIT_TIMEOUT_SEC)
    _run(
        [git, "remote", "add", "origin", BGUTIL_REPOSITORY],
        cwd=staging,
        timeout=_GIT_TIMEOUT_SEC,
    )
    _run(
        [git, "fetch", "--depth", "1", "origin", BGUTIL_COMMIT],
        cwd=staging,
        timeout=_GIT_TIMEOUT_SEC,
    )
    _run(
        [git, "checkout", "--detach", "FETCH_HEAD"],
        cwd=staging,
        timeout=_GIT_TIMEOUT_SEC,
    )


def _publish_staging(staging: Path) -> None:
    """Publish a prepared runtime with rollback instead of delete-then-replace."""
    backup = RUNTIME_ROOT / f".bgutil-backup-{uuid.uuid4().hex[:12]}"
    old_moved = False
    try:
        if PROVIDER_ROOT.exists():
            PROVIDER_ROOT.replace(backup)
            old_moved = True
        staging.replace(PROVIDER_ROOT)
    except OSError as exc:
        rollback_error: OSError | None = None
        if old_moved and backup.exists() and not PROVIDER_ROOT.exists():
            try:
                backup.replace(PROVIDER_ROOT)
            except OSError as rollback_exc:
                rollback_error = rollback_exc
        if rollback_error is not None:
            raise ProvisionError(
                "Не удалось опубликовать новый bgutil runtime и откатить старый"
            ) from rollback_error
        raise ProvisionError(
            "Не удалось атомарно опубликовать подготовленный bgutil runtime"
        ) from exc
    else:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)


def _current_runtime_healthy(node: str) -> bool:
    if not _runtime_files_current():
        return False
    try:
        _require_script_version(node)
    except ProvisionError:
        return False
    return True


def ensure_bgutil_provider() -> Path:
    """Return the ready provider server directory, provisioning it if needed."""
    node = _node_executable()
    if _current_runtime_healthy(node):
        return SERVER_ROOT

    git = shutil.which("git")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not git:
        raise ProvisionError("git не найден; он нужен для pinned bgutil runtime")
    if not npm:
        raise ProvisionError("npm не найден; установи Node.js с npm")

    with _provision_lock():
        # Another process may have completed provisioning while we waited.
        if _current_runtime_healthy(node):
            return SERVER_ROOT

        staging = RUNTIME_ROOT / (
            f".bgutil-{BGUTIL_COMMIT[:12]}-{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp"
        )
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

            plugin_entry = (
                staging
                / "plugin"
                / "yt_dlp_plugins"
                / "extractor"
                / "getpot_bgutil.py"
            )
            if not plugin_entry.is_file():
                raise ProvisionError(
                    "bgutil source checkout does not contain the yt-dlp plugin"
                )

            server = staging / "server"
            _run(
                [npm, "ci", "--no-audit", "--no-fund"],
                cwd=server,
                timeout=_NPM_TIMEOUT_SEC,
            )
            tsc_name = "tsc.cmd" if os.name == "nt" else "tsc"
            tsc = server / "node_modules" / ".bin" / tsc_name
            if not tsc.is_file():
                raise ProvisionError(
                    "bgutil npm ci не установил pinned local TypeScript compiler"
                )
            _run([str(tsc)], cwd=server, timeout=_BUILD_TIMEOUT_SEC)
            generated = server / "build" / "generate_once.js"
            if not generated.is_file():
                raise ProvisionError(
                    "bgutil build завершился без build/generate_once.js"
                )
            _require_script_version(node, script=generated, cwd=server)

            (staging / ".mp3bot-bgutil-version").write_text(
                f"{BGUTIL_VERSION}@{BGUTIL_COMMIT}\n", encoding="utf-8"
            )
            _publish_staging(staging)
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
