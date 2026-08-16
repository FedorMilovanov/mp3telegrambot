#!/usr/bin/env python3
"""Backend-neutral durable worker for Dub Studio jobs."""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from services.dub_execution import (
    finish_execution_lease,
    heartbeat_execution_lease,
    record_execution_lease,
    recover_orphaned_executions,
    validate_recipe_outputs,
)
from services.dub_studio import DubStore, load_recipe, repo_root, studio_root, utc_now
from services.dub_worker_release import WORKER_RUNTIME
from tools.voxcpm2 import dub_job_preflight

_STAGE_RE = re.compile(r"^===\s*(.+?)\s*===\s*$")
_SEGMENT_RE = re.compile(r"\[(\d+)\s*/\s*(\d+)\]")
_PERCENT_RE = re.compile(r"(?<!\d)(\d{1,3})%")
_PARAMETER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_STOP = threading.Event()
_ALLOWED_MODULE_PREFIXES = (
    "services.dub_runtimes.",
    "tools.voxcpm2.",
    "pipelines.dubbing.",
)


def log(message: str) -> None:
    print(message, flush=True)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class WorkerLock:
    def __init__(self, root: Path) -> None:
        self.path = root / "worker.lock"
        self.acquired = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(3):
            try:
                fd = os.open(
                    str(self.path),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                try:
                    old_pid = int(self.path.read_text(encoding="utf-8").strip())
                except (OSError, ValueError):
                    old_pid = 0
                if old_pid and _pid_is_running(old_pid):
                    return False
                try:
                    self.path.unlink()
                except OSError:
                    time.sleep(0.1)
                continue
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(str(os.getpid()))
                self.acquired = True
                return True
        return False

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            own_pid = str(os.getpid())
            if self.path.exists() and self.path.read_text(
                encoding="utf-8"
            ).strip() == own_pid:
                self.path.unlink()
        except OSError:
            pass
        self.acquired = False


def _safe_repo_script(relative: str) -> Path:
    root = repo_root().resolve()
    allowed = (root / "tools" / "voxcpm2").resolve()
    script = (root / str(relative)).resolve()
    try:
        script.relative_to(allowed)
    except ValueError as exc:
        raise RuntimeError("Recipe script escaped tools/voxcpm2.") from exc
    if script.suffix.lower() != ".ps1" or not script.is_file():
        raise RuntimeError(f"PowerShell script not found: {script}")
    return script


def _powershell_executable() -> str:
    value = os.getenv("DUB_STUDIO_POWERSHELL", "").strip()
    if value:
        found = shutil.which(value) or value
        if Path(found).name.lower() not in {
            "pwsh.exe",
            "pwsh",
            "powershell.exe",
            "powershell",
        }:
            raise RuntimeError(
                "DUB_STUDIO_POWERSHELL must point to pwsh/powershell."
            )
        return found
    found = shutil.which("pwsh") or shutil.which("powershell")
    if not found:
        raise RuntimeError("pwsh/powershell not found in PATH.")
    return found


def _validated_parameters(spec: dict[str, Any]) -> list[str]:
    parameters = spec.get("parameters", {})
    if not isinstance(parameters, dict):
        raise RuntimeError("Recipe parameters must be an object.")
    args: list[str] = []
    for name, value in parameters.items():
        name = str(name)
        if not _PARAMETER_RE.fullmatch(name):
            raise RuntimeError(f"Invalid recipe parameter: {name}")
        if value is None or value is False:
            continue
        args.append("-" + name)
        if value is True:
            continue
        if not isinstance(value, (str, int, float)):
            raise RuntimeError(f"Unsupported value for parameter {name}.")
        args.append(str(value))
    return args


def build_command(
    recipe_id: str,
    action_name: str,
) -> tuple[list[str], dict[str, Any]]:
    recipe = load_recipe(recipe_id)
    spec = recipe.action(action_name)
    runner = str(spec.get("runner", "")).lower()
    if runner == "powershell":
        script = _safe_repo_script(str(spec.get("script", "")))
        command = [
            _powershell_executable(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *_validated_parameters(spec),
        ]
    elif runner == "python_module":
        module = str(spec.get("module", ""))
        if not module.startswith(_ALLOWED_MODULE_PREFIXES):
            raise RuntimeError("Recipe Python module is outside the allow-list.")
        python = str(spec.get("python") or sys.executable)
        command = [python, "-m", module, *_validated_parameters(spec)]
    else:
        raise RuntimeError(f"Unsupported recipe runner: {runner}")
    return command, spec


def _output_report(
    recipe_id: str,
    work_root: str,
    action_name: str,
    *,
    job_started_at: str = "1970-01-01T00:00:00+00:00",
) -> dict[str, Any]:
    recipe = load_recipe(recipe_id)
    return validate_recipe_outputs(
        recipe,
        action_name=action_name,
        work_root=work_root,
        job_started_at=job_started_at,
    )


def _basic_progress_from_line(line: str, current: int) -> tuple[int, str]:
    text = line.strip()
    if not text:
        return current, ""
    stage_match = _STAGE_RE.match(text)
    if stage_match:
        return max(current, 3), stage_match.group(1)[:160]
    segment = _SEGMENT_RE.search(text)
    if segment:
        index = max(1, int(segment.group(1)))
        total = max(index, int(segment.group(2)))
        return (
            max(current, min(92, 8 + round(index / total * 78))),
            f"segment {index}/{total}",
        )
    percentage = _PERCENT_RE.search(text)
    if percentage:
        value = max(0, min(int(percentage.group(1)), 100))
        return max(current, min(94, 8 + round(value * 0.72))), "synthesis"
    if "финальный master" in text.lower() or "master" in text.lower():
        return max(current, 94), "master"
    return current, ""


def _reader(pipe: Any, output: queue.Queue[str]) -> None:
    try:
        for line in iter(pipe.readline, ""):
            output.put(line)
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _basic_terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass


def _finish_lease_safely(
    store: DubStore,
    job_id: int,
    *,
    state: str,
    details: dict[str, Any] | None = None,
) -> None:
    try:
        finish_execution_lease(store, job_id, state=state, details=details)
    except Exception as exc:
        log(f"Execution lease finalization failed for job #{job_id}: {exc}")


def _execute_runner_job(store: DubStore, worker_id: str, job: dict[str, Any]) -> None:
    job_id = int(job["id"])
    project = store.get_project(str(job["project_id"]))
    action_name = str(job["action"])
    command, action_spec = build_command(project["recipe_id"], action_name)
    log_path = store.logs_dir / f"job-{job_id:06d}.log"
    store.set_job_log_path(job_id, log_path)

    creationflags = 0
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        creationflags = 0x08000000 | 0x00000200
    else:
        popen_kwargs["start_new_session"] = True

    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["DUB_STUDIO_PROJECT_ID"] = str(project["id"])
    env["DUB_STUDIO_JOB_ID"] = str(job_id)

    progress = 2
    stage = "starting"
    store.update_job_progress(
        job_id,
        progress=progress,
        stage=stage,
        message="Запуск production runner",
    )
    started_monotonic = time.monotonic()
    job_started_at = utc_now()
    recent_lines: list[str] = []

    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        log_file.write("COMMAND: " + json.dumps(command, ensure_ascii=False) + "\n\n")
        log_file.flush()
        proc = subprocess.Popen(
            command,
            cwd=str(repo_root()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            env=env,
            **popen_kwargs,
        )
        try:
            lease = record_execution_lease(
                store,
                job_id=job_id,
                worker_id=worker_id,
                runner_pid=proc.pid,
                process_group_id=proc.pid,
                command=command,
                details={
                    "project_id": project["id"],
                    "action": action_name,
                },
            )
        except Exception:
            _terminate_process(proc)
            raise

        lines: queue.Queue[str] = queue.Queue()
        thread = threading.Thread(
            target=_reader,
            args=(proc.stdout, lines),
            daemon=True,
        )
        thread.start()
        last_heartbeat = 0.0
        last_progress_update = 0.0

        while proc.poll() is None or thread.is_alive() or not lines.empty():
            drained = False
            while True:
                try:
                    line = lines.get_nowait()
                except queue.Empty:
                    break
                drained = True
                log_file.write(line)
                log_file.flush()
                clean = line.strip()
                if clean:
                    recent_lines.append(clean)
                    recent_lines = recent_lines[-25:]
                    next_progress, next_stage = _progress_from_line(clean, progress)
                    if next_stage:
                        stage = next_stage
                    if (
                        next_progress > progress
                        or time.monotonic() - last_progress_update > 30
                    ):
                        progress = next_progress
                        store.update_job_progress(
                            job_id,
                            progress=progress,
                            stage=stage,
                            message=clean[:800],
                        )
                        last_progress_update = time.monotonic()

            if store.is_cancel_requested(job_id) or _STOP.is_set():
                _terminate_process(proc)
                _finish_lease_safely(
                    store,
                    job_id,
                    state="cancelled",
                    details={"runner_pid": lease.runner_pid},
                )
                store.finish_job(
                    job_id,
                    status="cancelled",
                    error=(
                        "Остановлено пользователем."
                        if not _STOP.is_set()
                        else "Worker stopping."
                    ),
                )
                return

            if time.monotonic() - last_heartbeat >= 5:
                heartbeat_execution_lease(store, job_id)
                store.worker_heartbeat(
                    worker_id,
                    status="busy",
                    current_job_id=job_id,
                    details={
                        "action": action_name,
                        "project_id": project["id"],
                        "runner_pid": lease.runner_pid,
                        "command_fingerprint": lease.command_fingerprint,
                        "progress": progress,
                        "stage": stage,
                        "elapsed_seconds": round(
                            time.monotonic() - started_monotonic,
                            1,
                        ),
                    },
                )
                last_heartbeat = time.monotonic()
            if not drained:
                time.sleep(0.25)

        return_code = int(proc.wait())

    if return_code != 0:
        _finish_lease_safely(
            store,
            job_id,
            state="failed",
            details={"return_code": return_code},
        )
        tail = "\n".join(recent_lines[-12:])
        store.finish_job(
            job_id,
            status="failed",
            error=f"Runner exited with code {return_code}.\n{tail}"[:8000],
        )
        return

    refreshed_project = store.get_project(str(job["project_id"]))
    try:
        report = _output_report(
            refreshed_project["recipe_id"],
            str(refreshed_project.get("work_root") or ""),
            action_name,
            job_started_at=job_started_at,
        )
    except Exception as exc:
        _finish_lease_safely(
            store,
            job_id,
            state="failed",
            details={"artifact_error": f"{type(exc).__name__}: {exc}"},
        )
        store.finish_job(
            job_id,
            status="failed",
            error=f"Artifact postcondition failed: {type(exc).__name__}: {exc}",
        )
        return

    report.update(
        {
            "execution_lease": lease.as_dict(),
            "action": action_name,
            "action_kind": str(action_spec.get("kind") or ""),
            "duration_seconds": round(
                time.monotonic() - started_monotonic,
                1,
            ),
            "log_path": str(log_path),
        }
    )
    _finish_lease_safely(store, job_id, state="finished")
    store.finish_job(job_id, status="succeeded", result=report)


_PROGRESS_PREFIX = "DUB_PROGRESS "
_QA_ROUND_RE = re.compile(r"QA round\s+(\d+)\s*/\s*(\d+)", flags=re.I)
_MODEL_TQDM_RE = re.compile(
    r"^(?:\x1b\[[0-9;]*m)*\s*\d{1,3}%\|.*\|\s*\d+/\d+\s*\["
)
_MILESTONES = (25, 50, 75, 90)
_PULSE_SECONDS = 15.0
_FINAL_WORKER_JOB_STATES = {"succeeded", "failed", "cancelled"}
_LAST_JOB_PULSE: dict[int, float] = {}
_FINISH_LOCK = threading.RLock()
CANCELLATION_POLICY = "preflight-cancel-before-runner-v1"
STORE_ROOT_POLICY = "explicit-worker-root-propagation-v3"
DELIVERY_RESILIENCE_POLICY = "cadence-tail-fit-adaptive-resume-v1"
JOB_QUALITY_RETRY_POLICY = "worker-checkpoint-quality-restart-v1"
MAX_JOB_QUALITY_RESTARTS = 3


def _elapsed_label(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, rest = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин"
    if minutes:
        return f"{minutes} мин {rest:02d} сек"
    return f"{rest} сек"


def _versioned_details(details: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(details or {})
    payload["runtime"] = WORKER_RUNTIME
    return payload


def _highest_crossed_milestone(previous: int, current: int) -> int | None:
    crossed = [value for value in _MILESTONES if previous < value <= current]
    return max(crossed) if crossed else None


def _deepest_error_line(error: str) -> str:
    lines = [line.strip() for line in str(error or "").splitlines() if line.strip()]
    if not lines:
        return "Неизвестная ошибка runner."
    prefixes = (
        "RuntimeError:", "TypeError:", "ValueError:", "AttributeError:",
        "FileNotFoundError:", "ModuleNotFoundError:", "ImportError:",
        "OSError:", "ОШИБКА:",
    )
    for line in reversed(lines):
        if line.startswith(prefixes) or "Error:" in line:
            return line
    return lines[-1]


class WorkerDubStore(DubStore):
    """Worker-specific durable store policy expressed through normal overrides."""

    def register_worker(
        self,
        worker_id: str,
        *,
        pid: int,
        status: str,
        current_job_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().register_worker(
            worker_id,
            pid=pid,
            status=status,
            current_job_id=current_job_id,
            details=_versioned_details(details),
        )

    def worker_heartbeat(
        self,
        worker_id: str,
        *,
        status: str,
        current_job_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        payload = _versioned_details(details)
        super().worker_heartbeat(
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

    def update_job_progress(
        self,
        job_id: int,
        *,
        progress: int,
        stage: str,
        message: str = "",
    ) -> None:
        previous = 0
        project_id = ""
        status = ""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT project_id, progress, status FROM dub_jobs WHERE id=?",
                (int(job_id),),
            ).fetchone()
            if row is not None:
                previous = int(row["progress"] or 0)
                project_id = str(row["project_id"])
                status = str(row["status"] or "").lower()
        if status in _FINAL_WORKER_JOB_STATES:
            _LAST_JOB_PULSE.pop(int(job_id), None)
            return
        super().update_job_progress(
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

    def recover_abandoned_jobs(self, stale_seconds: int = 180) -> int:
        recovered = super().recover_abandoned_jobs(stale_seconds)
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT id, project_id FROM dub_jobs
                WHERE status='cancelled'
                  AND cancel_requested=1
                  AND finished_at=''
                ORDER BY id
                """
            ).fetchall()
            for row in rows:
                job_id = int(row["id"])
                project_id = str(row["project_id"])
                conn.execute(
                    """
                    UPDATE dub_jobs
                    SET progress=0, stage='cancelled', finished_at=?, updated_at=?
                    WHERE id=? AND status='cancelled' AND finished_at=''
                    """,
                    (now, now, job_id),
                )
                conn.execute(
                    """
                    UPDATE dub_projects
                    SET status='cancelled', stage='cancelled', progress=0, updated_at=?
                    WHERE id=?
                    """,
                    (now, project_id),
                )
                exists = conn.execute(
                    """
                    SELECT 1 FROM dub_events
                    WHERE job_id=? AND event_type='job_cancelled'
                    LIMIT 1
                    """,
                    (job_id,),
                ).fetchone()
                if exists is None:
                    self._insert_event(
                        conn,
                        project_id,
                        job_id,
                        "job_cancelled",
                        "warning",
                        f"Задание #{job_id} отменено после остановки worker.",
                        {"recovered_after_worker_stop": True},
                    )
            conn.commit()
        return recovered

    def finish_job(
        self,
        job_id: int,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        requested_status = str(status).lower()
        if requested_status not in _FINAL_WORKER_JOB_STATES:
            super().finish_job(
                job_id,
                status=status,
                result=result,
                error=error,
            )
            return
        with _FINISH_LOCK:
            try:
                current = self.get_job(int(job_id))
            except KeyError:
                _LAST_JOB_PULSE.pop(int(job_id), None)
                return
            if str(current.get("status") or "").lower() in _FINAL_WORKER_JOB_STATES:
                _LAST_JOB_PULSE.pop(int(job_id), None)
                return
            payload = str(error or "")
            if requested_status == "failed" and payload:
                cause = _deepest_error_line(payload)
                if not payload.startswith("Точная причина:"):
                    payload = f"Точная причина: {cause}\n\n{payload}"
            _LAST_JOB_PULSE.pop(int(job_id), None)
            super().finish_job(
                job_id,
                status=requested_status,
                result=result,
                error=payload,
            )


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
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
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
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


def _progress_from_line(line: str, current: int) -> tuple[int, str]:
    text = str(line or "").strip()
    if not text or _MODEL_TQDM_RE.match(text):
        return int(current), ""
    if text.startswith(_PROGRESS_PREFIX):
        try:
            payload = json.loads(text[len(_PROGRESS_PREFIX):])
        except (TypeError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            progress = max(0, min(int(payload.get("progress") or current), 94))
            stage = str(payload.get("stage") or payload.get("message") or "CPU-рендер")[:160]
            return max(current, progress), stage
    lowered = text.casefold()
    qa_round = _QA_ROUND_RE.search(text)
    if qa_round:
        index = max(1, int(qa_round.group(1)))
        total = max(index, int(qa_round.group(2)))
        return max(current, 88), f"Независимая QA: раунд {index}/{total}"
    if "все реплики прошли акустическую" in lowered or "акустическая qa пройдена" in lowered:
        return max(current, 93), "Независимая QA пройдена"
    if "qa отклонил" in lowered or "clean_qa" in lowered or ("независим" in lowered and "qa" in lowered):
        return max(current, 88), "Независимая QA"
    if (
        "создаю постоянный микс" in lowered
        or "двухпроходный loudness-master" in lowered
        or "собираю upload-ready" in lowered
        or lowered.startswith("=== master")
        or lowered.startswith("=== мастер")
    ):
        return max(current, 94), "master"
    stage_match = _STAGE_RE.match(text)
    if stage_match:
        return max(current, 3), stage_match.group(1)[:160]
    segment = _SEGMENT_RE.search(text)
    if segment:
        index = max(1, int(segment.group(1)))
        total = max(index, int(segment.group(2)))
        return max(current, min(92, 8 + round(index / total * 78))), f"segment {index}/{total}"
    percentage = _PERCENT_RE.search(text)
    if percentage:
        value = max(0, min(int(percentage.group(1)), 100))
        return max(current, min(94, 8 + round(value * 0.72))), "synthesis"
    return current, ""


class _TerminalCaptureStore:
    def __init__(self, store: Any) -> None:
        self._store = store
        self.terminal: dict[str, Any] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def finish_job(
        self,
        job_id: int,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        normalized = str(status).lower()
        if normalized in _FINAL_WORKER_JOB_STATES:
            self.terminal = {
                "job_id": int(job_id),
                "status": normalized,
                "result": result,
                "error": str(error or ""),
            }
            return
        self._store.finish_job(
            int(job_id), status=status, result=result, error=error
        )


def _quality_failure_detail(project: dict[str, Any], error: str) -> str:
    from tools.voxcpm2 import clean_production_core

    detail = str(error or "").strip()
    if clean_production_core._retryable_delivery_failure(detail):
        return detail
    if "Прямой VoxCPM2 renderer" not in detail:
        return ""
    root = str(project.get("work_root") or "").strip()
    report = clean_production_core._direct_failure_report(root) if root else ""
    combined = "\n".join(value for value in (report, detail) if value)
    return combined if clean_production_core._retryable_delivery_failure(combined) else ""


def _archive_quality_retry_log(store: Any, job_id: int, retry_index: int) -> None:
    source = Path(store.logs_dir) / f"job-{int(job_id):06d}.log"
    if not source.is_file():
        return
    target = source.with_name(
        f"job-{int(job_id):06d}.quality-retry-{int(retry_index):02d}.log"
    )
    try:
        shutil.copy2(source, target)
    except OSError:
        pass


def _stop_reason(store: Any, job_id: int) -> str:
    if _STOP.is_set():
        return "Worker stopping."
    try:
        if store.is_cancel_requested(int(job_id)):
            return "Остановлено пользователем."
    except Exception:
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
    detail = f"Preflight остановил задание до синтеза: {type(exc).__name__}: {exc}"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(detail + "\n", encoding="utf-8", errors="replace")
    store.finish_job(int(job_id), status="failed", error=detail)


def _run_with_quality_restarts(
    store: Any,
    worker_id: str,
    job: dict[str, Any],
    project: dict[str, Any],
) -> None:
    job_id = int(job["id"])
    try:
        retry_limit = int(
            os.environ.get(
                "DUB_WORKER_MAX_QUALITY_RESTARTS",
                str(MAX_JOB_QUALITY_RESTARTS),
            )
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("DUB_WORKER_MAX_QUALITY_RESTARTS должен быть целым.") from exc
    retry_limit = max(0, min(12, retry_limit))

    for retry_index in range(retry_limit + 1):
        capture = _TerminalCaptureStore(store)
        _execute_runner_job(capture, worker_id, job)
        terminal = capture.terminal
        if terminal is None:
            return
        status = str(terminal["status"])
        if status != "failed":
            store.finish_job(
                job_id,
                status=status,
                result=terminal.get("result"),
                error=str(terminal.get("error") or ""),
            )
            return
        detail = _quality_failure_detail(project, str(terminal.get("error") or ""))
        if not detail or retry_index >= retry_limit:
            error = str(terminal.get("error") or "")
            if detail and retry_limit:
                error = (
                    "Worker исчерпал автоматические quality-restarts "
                    f"({retry_limit}); успешные checkpoints сохранены.\n{error}"
                )
            store.finish_job(job_id, status="failed", error=error)
            return
        next_retry = retry_index + 1
        _archive_quality_retry_log(store, job_id, next_retry)
        store.update_job_progress(
            job_id,
            progress=2,
            stage=f"quality-retry:{next_retry}/{retry_limit}",
            message=(
                "Hard-quality gate отклонил только проблемный сегмент. "
                "Сохраняю принятые checkpoints и перезапускаю runner с новым "
                f"seed epoch ({next_retry}/{retry_limit})."
            ),
        )
        reason = _stop_reason(store, job_id)
        if reason:
            store.finish_job(job_id, status="cancelled", error=reason)
            return
    raise RuntimeError("Недостижимое состояние worker quality restart.")


def execute_job(store: WorkerDubStore, worker_id: str, job: dict[str, Any]) -> None:
    """Source-owned preflight, cancellation and bounded quality retry pipeline."""
    job_id = int(job["id"])
    reason = _stop_reason(store, job_id)
    if reason:
        _finish_cancelled(store, job_id, reason)
        return
    try:
        project = store.get_project(str(job["project_id"]))
        if not project:
            raise RuntimeError(f"Preflight: проект не найден: {job['project_id']}")
        store.update_job_progress(
            job_id,
            progress=1,
            stage="preflight",
            message=(
                "Проверяю CPU Python, модель, FFmpeg, cadence/tail/fit gates "
                "и production imports до синтеза."
            ),
        )
        report = dub_job_preflight.run(
            project,
            str(job.get("action") or ""),
            studio=store.root,
        )
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
        reason = _stop_reason(store, job_id)
        if reason:
            _finish_cancelled(store, job_id, reason)
            return
        _write_preflight_failure(store, job_id, exc)
        return
    _run_with_quality_restarts(store, worker_id, job, project)

def run_worker(
    root: str | None,
    *,
    once: bool = False,
    poll_seconds: float = 2.0,
) -> int:
    actual_root = studio_root(root)
    lock = WorkerLock(actual_root)
    if not lock.acquire():
        log(f"Dub Studio worker already running: {lock.path}")
        return 2

    worker_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    store = WorkerDubStore(actual_root)
    orphaned = recover_orphaned_executions(store)
    if orphaned:
        log(f"Terminated or cleared orphan runner leases: {orphaned}")
    recovered = store.recover_abandoned_jobs()
    if recovered:
        log(f"Recovered jobs: {recovered}")
    store.register_worker(
        worker_id,
        pid=os.getpid(),
        status="idle",
        details={"python": sys.executable, "root": str(actual_root)},
    )

    def stop_handler(_signum: int, _frame: Any) -> None:
        _STOP.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, stop_handler)
        except (ValueError, OSError):
            pass

    log(f"Dub Studio worker: {worker_id}")
    log(f"Root: {actual_root}")
    try:
        while not _STOP.is_set():
            store.worker_heartbeat(
                worker_id,
                status="idle",
                current_job_id=None,
            )
            job = store.claim_next_job(worker_id)
            if job is None:
                if once:
                    return 0
                _STOP.wait(max(0.25, float(poll_seconds)))
                continue
            try:
                execute_job(store, worker_id, job)
            except Exception as exc:
                _finish_lease_safely(
                    store,
                    int(job["id"]),
                    state="failed",
                    details={"worker_error": f"{type(exc).__name__}: {exc}"},
                )
                store.finish_job(
                    int(job["id"]),
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
                log(f"Job #{job['id']} failed: {exc}")
            if once:
                return 0
        return 0
    finally:
        try:
            store.worker_heartbeat(
                worker_id,
                status="stopped",
                current_job_id=None,
            )
        except Exception:
            pass
        lock.release()


def main() -> None:
    parser = argparse.ArgumentParser(description="Durable local Dub Studio worker")
    parser.add_argument("--root")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    raise SystemExit(
        run_worker(
            args.root,
            once=args.once,
            poll_seconds=args.poll_seconds,
        )
    )


if __name__ == "__main__":
    main()
