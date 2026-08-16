#!/usr/bin/env python3
"""Move Dub worker hardening into explicit source owners without runtime surgery."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "services" / "dub_worker.py"
PREFLIGHT = ROOT / "tools" / "voxcpm2" / "dub_job_preflight.py"
TOOLS_WORKER = ROOT / "tools" / "voxcpm2" / "dub_worker.py"
HARDENED = ROOT / "tools" / "voxcpm2" / "dub_worker_hardened.py"
HARDENED_INIT = ROOT / "tools" / "voxcpm2" / "dub_worker_hardened" / "__init__.py"
HARDENED_MAIN = ROOT / "tools" / "voxcpm2" / "dub_worker_hardened" / "__main__.py"
BASE_SNAPSHOT = ROOT / "tools" / "voxcpm2" / "_dub_worker_hardened_base.py"

SOURCE_BLOCK = r'''
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
'''


def _replace_preflight_signature(text: str) -> str:
    text = text.replace(
        "def _project_root(project: dict[str, Any]) -> Path:\n",
        "def _project_root(\n    project: dict[str, Any],\n    *,\n    studio: Path | None = None,\n) -> Path:\n",
        1,
    )
    text = text.replace(
        "    studio = studio_root().resolve()\n    allowed = (studio / \"projects\").resolve()\n",
        "    studio_base = Path(studio).resolve() if studio is not None else studio_root().resolve()\n    allowed = (studio_base / \"projects\").resolve()\n",
        1,
    )
    text = text.replace("_normalized_path(studio),", "_normalized_path(studio_base),", 1)
    text = text.replace(
        "def _runtime_paths(project: dict[str, Any]) -> dict[str, Any]:\n    root = _project_root(project)\n",
        "def _runtime_paths(\n    project: dict[str, Any],\n    *,\n    studio: Path | None = None,\n) -> dict[str, Any]:\n    root = _project_root(project, studio=studio)\n",
        1,
    )
    text = text.replace(
        "def run(project: dict[str, Any], action: str) -> dict[str, Any]:\n",
        "def run(\n    project: dict[str, Any],\n    action: str,\n    *,\n    studio: Path | None = None,\n) -> dict[str, Any]:\n",
        1,
    )
    text = text.replace("    paths = _runtime_paths(project)\n", "    paths = _runtime_paths(project, studio=studio)\n", 1)
    return text


def main() -> int:
    worker = WORKER.read_text(encoding="utf-8")
    if "class WorkerDubStore(DubStore)" in worker:
        raise RuntimeError("services.dub_worker is already source-owned")
    worker = worker.replace("from typing import Any\n", "from typing import Any\n", 1)
    if "from services.dub_worker_release import WORKER_RUNTIME" not in worker:
        anchor = "from services.dub_studio import DubStore, load_recipe, repo_root, studio_root, utc_now\n"
        worker = worker.replace(
            anchor,
            anchor + "from services.dub_worker_release import WORKER_RUNTIME\nfrom tools.voxcpm2 import dub_job_preflight\n",
            1,
        )
    worker = worker.replace("def _progress_from_line(", "def _basic_progress_from_line(", 1)
    worker = worker.replace("def _terminate_process(", "def _basic_terminate_process(", 1)
    worker = worker.replace("def execute_job(", "def _execute_runner_job(", 1)
    anchor = "\n\ndef run_worker(\n"
    if anchor not in worker:
        raise RuntimeError("services.dub_worker run_worker anchor missing")
    worker = worker.replace(anchor, "\n\n" + SOURCE_BLOCK.strip() + anchor, 1)
    if "store = DubStore(actual_root)" not in worker:
        raise RuntimeError("services.dub_worker store construction anchor missing")
    worker = worker.replace("store = DubStore(actual_root)", "store = WorkerDubStore(actual_root)", 1)
    ast.parse(worker, filename=str(WORKER))
    WORKER.write_text(worker, encoding="utf-8")

    preflight = PREFLIGHT.read_text(encoding="utf-8")
    preflight = _replace_preflight_signature(preflight)
    if "studio=studio" not in preflight or "studio=store.root" not in worker:
        raise RuntimeError("explicit worker root propagation was not installed")
    ast.parse(preflight, filename=str(PREFLIGHT))
    PREFLIGHT.write_text(preflight, encoding="utf-8")

    health = ROOT / "handlers" / "dub_health.py"
    if health.is_file():
        health_text = health.read_text(encoding="utf-8")
        health_text = health_text.replace(
            "from tools.voxcpm2.dub_worker import build_command",
            "from services.dub_worker import build_command",
        )
        health.write_text(health_text, encoding="utf-8")

    cli = '''#!/usr/bin/env python3\n"""CLI entrypoint for the source-owned Dub worker."""\nfrom services.dub_worker import main\n\nif __name__ == "__main__":\n    main()\n'''
    TOOLS_WORKER.write_text(cli, encoding="utf-8")
    HARDENED.write_text(cli, encoding="utf-8")
    for path in (HARDENED_INIT, HARDENED_MAIN, BASE_SNAPSHOT):
        if path.is_file():
            path.unlink()

    forbidden_worker = (
        "sys.modules[__name__].__class__",
        "install_hardening",
        "exec(compile(",
        "_dub_worker_hardened_base",
        "install_worker_progress",
    )
    combined = WORKER.read_text(encoding="utf-8") + TOOLS_WORKER.read_text(encoding="utf-8") + HARDENED.read_text(encoding="utf-8")
    bad = [token for token in forbidden_worker if token in combined]
    if bad:
        raise RuntimeError(f"worker surgery survived: {bad}")

    print("source-owned Dub worker composition installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
