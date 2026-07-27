#!/usr/bin/env python3
"""Hardened Dub Studio worker entrypoint with process-tree cancellation."""
from __future__ import annotations

import os
import signal
import subprocess
from typing import Any

from services.dub_studio import DubStore
from tools.voxcpm2 import dub_worker as worker

_RUNTIME_VERSION = "dub-worker-tree-cancel-v2"
_ORIGINAL_REGISTER = DubStore.register_worker
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


def _register_versioned_worker(
    self: DubStore,
    worker_id: str,
    *,
    pid: int,
    status: str,
    current_job_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    payload = dict(details or {})
    payload["runtime"] = _RUNTIME_VERSION
    _ORIGINAL_REGISTER(
        self,
        worker_id,
        pid=pid,
        status=status,
        current_job_id=current_job_id,
        details=payload,
    )


def install_hardening() -> None:
    worker._terminate_process = _terminate_process_tree
    DubStore.register_worker = _register_versioned_worker


def main() -> None:
    install_hardening()
    worker.main()


if __name__ == "__main__":
    main()
