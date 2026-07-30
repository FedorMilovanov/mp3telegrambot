#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cancellation-safe package entrypoint for the hardened Dub worker.

The parallel agent's complete v4.6 implementation remains in the sibling
``dub_worker_hardened.py`` file. This package shadows it for imports and
``python -m`` execution, installs every original hardening patch, and replaces
only the preflight wrapper so cancellation never becomes a false failure, the
production runner never starts after cancellation, and an explicit worker
``--root`` is used consistently by every preflight storage lookup.
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
from typing import Any, Iterator

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "dub_worker_hardened.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._dub_worker_hardened_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить hardened Dub worker: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_RUNTIME_VERSION = "dub-worker-quality-v4.6"
CANCELLATION_POLICY = "preflight-cancel-before-runner-v1"
STORE_ROOT_POLICY = "explicit-worker-root-propagation-v1"


@contextmanager
def _store_root_environment(store: Any) -> Iterator[Path]:
    """Expose the actual DubStore root to preflight modules for this job."""
    raw_root = getattr(store, "root", None)
    if raw_root is None or isinstance(raw_root, bool) or not str(raw_root).strip():
        raise RuntimeError("Worker store root отсутствует или некорректен.")
    root = Path(raw_root).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"Worker store root отсутствует: {root}")
    previous = os.environ.get("DUB_STUDIO_ROOT")
    os.environ["DUB_STUDIO_ROOT"] = str(root)
    try:
        yield root
    finally:
        if previous is None:
            os.environ.pop("DUB_STUDIO_ROOT", None)
        else:
            os.environ["DUB_STUDIO_ROOT"] = previous


def _stop_reason(store: Any, job_id: int) -> str:
    if _legacy.worker._STOP.is_set():
        return "Worker stopping."
    try:
        if store.is_cancel_requested(int(job_id)):
            return "Остановлено пользователем."
    except Exception:
        # A transient read error must not be mistaken for a cancellation.
        return ""
    return ""


def _finish_cancelled(store: Any, job_id: int, reason: str) -> None:
    store.finish_job(
        int(job_id),
        status="cancelled",
        error=str(reason or "Остановлено пользователем."),
    )


def _write_preflight_failure(store: Any, job_id: int, exc: BaseException) -> None:
    log_path = store.logs_dir / f"job-{int(job_id):06d}.log"
    store.set_job_log_path(int(job_id), log_path)
    detail = (
        "Preflight остановил задание до синтеза: "
        f"{type(exc).__name__}: {exc}"
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(detail + "\n", encoding="utf-8", errors="replace")
    store.finish_job(int(job_id), status="failed", error=detail)


def _execute_job_with_cancellable_preflight(
    store: Any,
    worker_id: str,
    job: dict[str, Any],
) -> None:
    """Run the agent's preflight, but never start runner after cancellation."""
    job_id = int(job["id"])
    reason = _stop_reason(store, job_id)
    if reason:
        _finish_cancelled(store, job_id, reason)
        return

    try:
        with _store_root_environment(store):
            project = store.get_project(str(job["project_id"]))
            if not project:
                raise RuntimeError(f"Preflight: проект не найден: {job['project_id']}")
            store.update_job_progress(
                job_id,
                progress=1,
                stage="preflight",
                message="Проверяю CPU Python, модель, FFmpeg и production imports до синтеза.",
            )
            report = _legacy.dub_job_preflight.run(
                project,
                str(job.get("action") or ""),
            )

            # The request can arrive while model/runtime hashes or import probes
            # are running. Check once more before any runner process is created.
            reason = _stop_reason(store, job_id)
            if reason:
                _finish_cancelled(store, job_id, reason)
                return

            if not report.get("skipped"):
                store.update_job_progress(
                    job_id,
                    progress=2,
                    stage="preflight:ok",
                    message="Production preflight пройден; запускаю runner.",
                )
    except Exception as exc:
        # A cancellation racing with a failing import/model probe is still a
        # cancellation, not a misleading preflight failure.
        reason = _stop_reason(store, job_id)
        if reason:
            _finish_cancelled(store, job_id, reason)
            return
        _write_preflight_failure(store, job_id, exc)
        return

    _legacy._ORIGINAL_EXECUTE_JOB(store, worker_id, job)


def install_hardening() -> None:
    _legacy.install_hardening()
    _legacy.worker.execute_job = _execute_job_with_cancellable_preflight


def main() -> None:
    install_hardening()
    _legacy.worker.main()


__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {
        "CANCELLATION_POLICY",
        "STORE_ROOT_POLICY",
        "_execute_job_with_cancellable_preflight",
        "_finish_cancelled",
        "_stop_reason",
        "_store_root_environment",
        "_write_preflight_failure",
        "install_hardening",
        "main",
    }
)
