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
    job_started_at: str,
) -> dict[str, Any]:
    recipe = load_recipe(recipe_id)
    return validate_recipe_outputs(
        recipe,
        action_name=action_name,
        work_root=work_root,
        job_started_at=job_started_at,
    )


def _progress_from_line(line: str, current: int) -> tuple[int, str]:
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


def _terminate_process(proc: subprocess.Popen[str]) -> None:
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


def execute_job(store: DubStore, worker_id: str, job: dict[str, Any]) -> None:
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
    lease_recorded = False
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
            lease_recorded = True
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
    store = DubStore(actual_root)
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
                if lease_recorded := False:
                    log(str(lease_recorded))
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
