#!/usr/bin/env python3
"""Hardened Dub Studio worker entrypoint with exact progress stages."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any

from services.dub_studio import DubStore
from tools.voxcpm2 import dub_worker as worker

_RUNTIME_VERSION = "dub-worker-quality-v4.4"
_PROGRESS_PREFIX = "DUB_PROGRESS "
_QA_ROUND_RE = re.compile(r"QA round\s+(\d+)\s*/\s*(\d+)", flags=re.I)
_MILESTONES = (25, 50, 75, 90)
_PULSE_SECONDS = 15.0
_ORIGINAL_REGISTER = DubStore.register_worker
_ORIGINAL_HEARTBEAT = DubStore.worker_heartbeat
_ORIGINAL_UPDATE_JOB_PROGRESS = DubStore.update_job_progress
_ORIGINAL_FINISH_JOB = DubStore.finish_job
_ORIGINAL_TERMINATE = worker._terminate_process
_LAST_JOB_PULSE: dict[int, float] = {}


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


def _elapsed_label(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, rest = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин"
    if minutes:
        return f"{minutes} мин {rest:02d} сек"
    return f"{rest} сек"


def _heartbeat_versioned_worker(
    self: DubStore,
    worker_id: str,
    *,
    status: str,
    current_job_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Keep runtime marker and pulse a silent CPU stage into project status."""
    payload = _versioned_details(details)
    _ORIGINAL_HEARTBEAT(
        self,
        worker_id,
        status=status,
        current_job_id=current_job_id,
        details=payload,
    )

    if str(status) != "busy" or current_job_id is None:
        return
    job_id = int(current_job_id)
    now = time.monotonic()
    if now - _LAST_JOB_PULSE.get(job_id, 0.0) < _PULSE_SECONDS:
        return
    progress = max(1, min(int(payload.get("progress") or 1), 99))
    stage = str(payload.get("stage") or "CPU-рендер")[:160]
    elapsed = float(payload.get("elapsed_seconds") or 0.0)
    self.update_job_progress(
        job_id,
        progress=progress,
        stage=stage,
        message=(
            f"{stage}: CPU-процесс активен; прошло {_elapsed_label(elapsed)}. "
            "Процент обновится на следующем подтверждённом шаге модели."
        ),
    )
    _LAST_JOB_PULSE[job_id] = now


def _highest_crossed_milestone(previous: int, current: int) -> int | None:
    crossed = [value for value in _MILESTONES if previous < value <= current]
    return max(crossed) if crossed else None


def _update_progress_with_milestones(
    self: DubStore,
    job_id: int,
    *,
    progress: int,
    stage: str,
    message: str = "",
) -> None:
    previous = 0
    project_id = ""
    with self.connect() as conn:
        row = conn.execute(
            "SELECT project_id, progress FROM dub_jobs WHERE id=?",
            (int(job_id),),
        ).fetchone()
        if row is not None:
            previous = int(row["progress"] or 0)
            project_id = str(row["project_id"])

    _ORIGINAL_UPDATE_JOB_PROGRESS(
        self,
        job_id,
        progress=progress,
        stage=stage,
        message=message,
    )

    milestone = _highest_crossed_milestone(previous, int(progress))
    if milestone is None or not project_id:
        return
    event_type = f"job_progress_{milestone}"
    with self.connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM dub_events WHERE job_id=? AND event_type=? LIMIT 1",
            (int(job_id), event_type),
        ).fetchone()
        if exists is None:
            self._insert_event(
                conn,
                project_id,
                int(job_id),
                event_type,
                "info",
                f"Задание #{job_id}: {milestone}% — {str(stage)[:160]}",
                {
                    "progress": milestone,
                    "stage": str(stage)[:160],
                    "message": str(message)[:800],
                },
            )
            conn.commit()


def _progress_from_line_v44(line: str, current: int) -> tuple[int, str]:
    """Parse only explicit production signals, never traceback function names."""
    text = str(line or "").strip()
    if not text:
        return current, ""

    if text.startswith(_PROGRESS_PREFIX):
        try:
            payload = json.loads(text[len(_PROGRESS_PREFIX) :])
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            progress = max(0, min(int(payload.get("progress") or current), 94))
            stage = str(
                payload.get("stage")
                or payload.get("message")
                or "CPU-рендер"
            )[:160]
            return max(current, progress), stage

    lowered = text.casefold()
    qa_round = _QA_ROUND_RE.search(text)
    if qa_round:
        index = max(1, int(qa_round.group(1)))
        total = max(index, int(qa_round.group(2)))
        return max(current, 88), f"Независимая QA: раунд {index}/{total}"
    if (
        "все реплики прошли акустическую" in lowered
        or "акустическая qa пройдена" in lowered
    ):
        return max(current, 93), "Независимая QA пройдена"
    if (
        "qa отклонил" in lowered
        or "clean_qa" in lowered
        or "независим" in lowered and "qa" in lowered
    ):
        return max(current, 88), "Независимая QA"

    explicit_master = (
        "создаю постоянный микс" in lowered
        or "двухпроходный loudness-master" in lowered
        or "собираю upload-ready" in lowered
        or lowered.startswith("=== master")
        or lowered.startswith("=== мастер")
    )
    if explicit_master:
        return max(current, 94), "master"

    stage_match = worker._STAGE_RE.match(text)
    if stage_match:
        stage = stage_match.group(1)[:160]
        return max(current, 3), stage

    segment = worker._SEGMENT_RE.search(text)
    if segment:
        index = max(1, int(segment.group(1)))
        total = max(index, int(segment.group(2)))
        return (
            max(current, min(92, 8 + round(index / total * 78))),
            f"segment {index}/{total}",
        )

    percentage = worker._PERCENT_RE.search(text)
    if percentage:
        value = max(0, min(int(percentage.group(1)), 100))
        return max(current, min(94, 8 + round(value * 0.72))), "synthesis"

    # In particular, lines such as ``clean.render_and_master(...)`` and
    # ``master_constant_mix.py`` inside a traceback must not change the stage.
    return current, ""


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
    _LAST_JOB_PULSE.pop(int(job_id), None)
    _ORIGINAL_FINISH_JOB(
        self,
        job_id,
        status=status,
        result=result,
        error=payload,
    )


def install_hardening() -> None:
    worker._terminate_process = _terminate_process_tree
    worker._progress_from_line = _progress_from_line_v44
    DubStore.register_worker = _register_versioned_worker
    DubStore.worker_heartbeat = _heartbeat_versioned_worker
    DubStore.update_job_progress = _update_progress_with_milestones
    DubStore.finish_job = _finish_job_with_root_cause


def main() -> None:
    install_hardening()
    worker.main()


if __name__ == "__main__":
    main()
