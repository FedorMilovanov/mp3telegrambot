#!/usr/bin/env python3
"""Hardened Dub Studio worker entrypoint with process-tree cancellation."""
from __future__ import annotations

import os
import subprocess
from typing import Any

from services.dub_studio import DubStore
from tools.voxcpm2 import dub_worker as worker

_RUNTIME_VERSION = "dub-worker-tree-cancel-v2"
_ORIGINAL_REGISTER = DubStore.register_worker
_ORIGINAL_HEARTBEAT = DubStore.worker_heartbeat
_ORIGINAL_FINISH_JOB = DubStore.finish_job
_ORIGINAL_TERMINATE = worker._terminate_process


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name != "nt":
        _ORIGINAL_TERMINATE(proc)
        return

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
            creationflags=creationflags,
        )
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass


def _versioned_details(details: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(details or {})
    payload["runtime"] = _RUNTIME_VERSION
    return payload


def _register_versioned_worker(
    self: DubStore,
    worker_id: str,
    *,
    pid: int,
    status: str,
    current_job_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    _ORIGINAL_REGISTER(
        self,
        worker_id,
        pid=pid,
        status=status,
        current_job_id=current_job_id,
        details=_versioned_details(details),
    )


def _heartbeat_versioned_worker(
    self: DubStore,
    worker_id: str,
    *,
    status: str,
    current_job_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Keep the runtime marker through idle and busy heartbeat replacements."""
    _ORIGINAL_HEARTBEAT(
        self,
        worker_id,
        status=status,
        current_job_id=current_job_id,
        details=_versioned_details(details),
    )


def _deepest_error_line(error: str) -> str:
    lines = [line.strip() for line in str(error or "").splitlines() if line.strip()]
    if not lines:
        return "Неизвестная ошибка runner."
    prefixes = (
        "RuntimeError:",
        "TypeError:",
        "ValueError:",
        "AttributeError:",
        "FileNotFoundError:",
        "ModuleNotFoundError:",
        "ImportError:",
        "OSError:",
        "ОШИБКА:",
    )
    for line in reversed(lines):
        if line.startswith(prefixes) or "Error:" in line:
            return line
    return lines[-1]


def _finish_job_with_root_cause(
    self: DubStore,
    job_id: int,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    payload = str(error or "")
    if str(status).lower() == "failed" and payload:
        cause = _deepest_error_line(payload)
        if not payload.startswith("Точная причина:"):
            payload = f"Точная причина: {cause}\n\n{payload}"
    _ORIGINAL_FINISH_JOB(
        self,
        job_id,
        status=status,
        result=result,
        error=payload,
    )


def install_hardening() -> None:
    worker._terminate_process = _terminate_process_tree
    DubStore.register_worker = _register_versioned_worker
    DubStore.worker_heartbeat = _heartbeat_versioned_worker
    DubStore.finish_job = _finish_job_with_root_cause


def main() -> None:
    install_hardening()
    worker.main()


if __name__ == "__main__":
    main()
