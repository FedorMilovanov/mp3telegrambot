"""Backend-neutral process leases and artifact postconditions for Dub jobs."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from services.dub_studio import DubStore, Recipe, resolve_recipe_path, utc_now

EXECUTION_LEASE_POLICY = "dub-runner-execution-lease-v1"
ARTIFACT_VALIDATION_POLICY = "current-job-artifact-postcondition-v1"


def _command_fingerprint(command: Sequence[str]) -> str:
    encoded = json.dumps(
        [str(part) for part in command],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _linux_start_token(pid: int) -> str:
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text(
            encoding="utf-8",
            errors="replace",
        ).split()
    except OSError:
        return ""
    return fields[21] if len(fields) > 21 else ""


def _windows_start_token(pid: int) -> str:
    try:
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            int(pid),
        )
        if not handle:
            return ""
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        try:
            ok = ctypes.windll.kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            if not ok:
                return ""
            value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
            return str(value)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return ""


def process_start_token(pid: int) -> str:
    if not _pid_is_running(pid):
        return ""
    if os.name == "nt":
        return _windows_start_token(pid)
    return _linux_start_token(pid)


def process_identity_matches(pid: int, expected_start_token: str) -> bool:
    if not _pid_is_running(pid):
        return False
    expected = str(expected_start_token or "")
    actual = process_start_token(pid)
    return not expected or not actual or actual == expected


def terminate_process_tree(pid: int, *, timeout: float = 20.0) -> None:
    """Terminate only a currently identified runner process tree."""
    pid = int(pid)
    if not _pid_is_running(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(1.0, float(timeout)),
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                return
    deadline = time.monotonic() + max(1.0, float(timeout))
    while _pid_is_running(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _pid_is_running(pid) and os.name != "nt":
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


@dataclass(frozen=True)
class ExecutionLease:
    job_id: int
    worker_id: str
    runner_pid: int
    runner_started_at: str
    process_start_token: str
    process_group_id: int | None
    command_fingerprint: str
    heartbeat_at: str
    state: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": EXECUTION_LEASE_POLICY,
            "job_id": self.job_id,
            "worker_id": self.worker_id,
            "runner_pid": self.runner_pid,
            "runner_started_at": self.runner_started_at,
            "process_start_token": self.process_start_token,
            "process_group_id": self.process_group_id,
            "command_fingerprint": self.command_fingerprint,
            "heartbeat_at": self.heartbeat_at,
            "state": self.state,
        }


def ensure_execution_schema(store: DubStore) -> None:
    with store.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dub_execution_leases (
                job_id INTEGER PRIMARY KEY,
                worker_id TEXT NOT NULL,
                runner_pid INTEGER NOT NULL,
                runner_started_at TEXT NOT NULL,
                process_start_token TEXT NOT NULL DEFAULT '',
                process_group_id INTEGER,
                command_fingerprint TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                state TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_dub_execution_leases_state_heartbeat
            ON dub_execution_leases(state, heartbeat_at);
            """
        )
        conn.commit()


def record_execution_lease(
    store: DubStore,
    *,
    job_id: int,
    worker_id: str,
    runner_pid: int,
    command: Sequence[str],
    process_group_id: int | None = None,
    details: Mapping[str, Any] | None = None,
) -> ExecutionLease:
    ensure_execution_schema(store)
    started_at = utc_now()
    start_token = process_start_token(runner_pid)
    fingerprint = _command_fingerprint(command)
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT runner_pid, process_start_token, state FROM dub_execution_leases WHERE job_id=?",
            (int(job_id),),
        ).fetchone()
        if current is not None and str(current["state"]) == "running":
            old_pid = int(current["runner_pid"])
            old_token = str(current["process_start_token"] or "")
            if process_identity_matches(old_pid, old_token):
                raise RuntimeError(
                    f"Job #{job_id} already has a live runner lease for PID {old_pid}."
                )
        conn.execute(
            """
            INSERT INTO dub_execution_leases(
                job_id, worker_id, runner_pid, runner_started_at,
                process_start_token, process_group_id, command_fingerprint,
                heartbeat_at, state, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?)
            ON CONFLICT(job_id) DO UPDATE SET
                worker_id=excluded.worker_id,
                runner_pid=excluded.runner_pid,
                runner_started_at=excluded.runner_started_at,
                process_start_token=excluded.process_start_token,
                process_group_id=excluded.process_group_id,
                command_fingerprint=excluded.command_fingerprint,
                heartbeat_at=excluded.heartbeat_at,
                state='running',
                details_json=excluded.details_json
            """,
            (
                int(job_id),
                str(worker_id),
                int(runner_pid),
                started_at,
                start_token,
                process_group_id,
                fingerprint,
                started_at,
                json.dumps(dict(details or {}), ensure_ascii=False, separators=(",", ":")),
            ),
        )
        conn.commit()
    return ExecutionLease(
        job_id=int(job_id),
        worker_id=str(worker_id),
        runner_pid=int(runner_pid),
        runner_started_at=started_at,
        process_start_token=start_token,
        process_group_id=process_group_id,
        command_fingerprint=fingerprint,
        heartbeat_at=started_at,
        state="running",
    )


def heartbeat_execution_lease(store: DubStore, job_id: int) -> None:
    ensure_execution_schema(store)
    with store.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE dub_execution_leases
            SET heartbeat_at=?
            WHERE job_id=? AND state='running'
            """,
            (utc_now(), int(job_id)),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"Running execution lease not found for job #{job_id}.")
        conn.commit()


def finish_execution_lease(
    store: DubStore,
    job_id: int,
    *,
    state: str,
    details: Mapping[str, Any] | None = None,
) -> None:
    state = str(state or "").strip().lower()
    if state not in {"finished", "failed", "cancelled", "terminated", "stale"}:
        raise ValueError(f"Unsupported execution lease state: {state}")
    ensure_execution_schema(store)
    with store.connect() as conn:
        conn.execute(
            """
            UPDATE dub_execution_leases
            SET state=?, heartbeat_at=?, details_json=?
            WHERE job_id=?
            """,
            (
                state,
                utc_now(),
                json.dumps(dict(details or {}), ensure_ascii=False, separators=(",", ":")),
                int(job_id),
            ),
        )
        conn.commit()


def active_execution_leases(store: DubStore) -> list[ExecutionLease]:
    ensure_execution_schema(store)
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT job_id, worker_id, runner_pid, runner_started_at,
                   process_start_token, process_group_id, command_fingerprint,
                   heartbeat_at, state
            FROM dub_execution_leases
            WHERE state='running'
            ORDER BY job_id
            """
        ).fetchall()
    return [
        ExecutionLease(
            job_id=int(row["job_id"]),
            worker_id=str(row["worker_id"]),
            runner_pid=int(row["runner_pid"]),
            runner_started_at=str(row["runner_started_at"]),
            process_start_token=str(row["process_start_token"] or ""),
            process_group_id=(
                int(row["process_group_id"])
                if row["process_group_id"] is not None
                else None
            ),
            command_fingerprint=str(row["command_fingerprint"]),
            heartbeat_at=str(row["heartbeat_at"]),
            state=str(row["state"]),
        )
        for row in rows
    ]


def recover_orphaned_executions(store: DubStore) -> int:
    """Terminate matching orphan runners before abandoned jobs are requeued."""
    recovered = 0
    for lease in active_execution_leases(store):
        if process_identity_matches(lease.runner_pid, lease.process_start_token):
            terminate_process_tree(lease.runner_pid)
            if _pid_is_running(lease.runner_pid):
                raise RuntimeError(
                    f"Unable to terminate orphan runner PID {lease.runner_pid} "
                    f"for job #{lease.job_id}."
                )
            state = "terminated"
        else:
            state = "stale"
        finish_execution_lease(
            store,
            lease.job_id,
            state=state,
            details={"recovered_before_requeue": True},
        )
        recovered += 1
    return recovered


def _parse_utc_timestamp(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _ffprobe_media_ok(path: Path, *, require_video: bool, require_audio: bool) -> bool:
    executable = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    try:
        proc = subprocess.run(
            [
                executable,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    try:
        payload = json.loads(proc.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    types = {str(item.get("codec_type")) for item in payload.get("streams") or []}
    return (
        duration > 0
        and (not require_video or "video" in types)
        and (not require_audio or "audio" in types)
    )


def validate_recipe_outputs(
    recipe: Recipe,
    *,
    action_name: str,
    work_root: str,
    job_started_at: str,
) -> dict[str, Any]:
    """Validate required outputs were produced by the current job."""
    started_timestamp = _parse_utc_timestamp(job_started_at)
    outputs: dict[str, Any] = {}
    required_failures: list[str] = []
    for name, raw_spec in recipe.outputs.items():
        spec = dict(raw_spec)
        applicable = spec.get("actions")
        if isinstance(applicable, list) and action_name not in {
            str(item) for item in applicable
        }:
            continue
        raw_path = str(spec.get("path") or "")
        if not raw_path:
            continue
        path = resolve_recipe_path(raw_path, work_root=work_root)
        try:
            stat = path.stat()
            exists = path.is_file()
        except OSError:
            stat = None
            exists = False
        size = int(stat.st_size) if stat is not None and exists else 0
        current_job = bool(
            stat is not None
            and stat.st_mtime >= max(0.0, started_timestamp - 2.0)
        )
        min_bytes = max(1, int(spec.get("min_bytes") or 1))
        media = str(spec.get("media") or "").lower()
        media_ok = True
        if exists and media in {"audio", "video", "av"}:
            media_ok = _ffprobe_media_ok(
                path,
                require_video=media in {"video", "av"},
                require_audio=media in {"audio", "av"},
            )
        valid = exists and size >= min_bytes and current_job and media_ok
        outputs[name] = {
            "path": str(path),
            "exists": exists,
            "size_bytes": size,
            "current_job": current_job,
            "media_ok": media_ok,
            "valid": valid,
            "required": bool(spec.get("required")),
            "label": str(spec.get("label") or name),
            "primary": bool(spec.get("primary")),
        }
        if bool(spec.get("required")) and not valid:
            required_failures.append(name)

    manifest_entry = outputs.get("manifest")
    if manifest_entry and manifest_entry["valid"]:
        manifest_path = Path(manifest_entry["path"])
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            required_failures.append("manifest:invalid-json")
            manifest = {"error": f"{type(exc).__name__}: {exc}"}
        outputs["manifest"]["payload"] = manifest
        if action_name.startswith("render") and manifest.get("phase") != "completed":
            required_failures.append("manifest:phase")

    if required_failures:
        raise RuntimeError(
            "Required current-job artifacts failed validation: "
            + ", ".join(dict.fromkeys(required_failures))
        )
    return {
        "artifact_validation_policy": ARTIFACT_VALIDATION_POLICY,
        "outputs": outputs,
    }


__all__ = [
    "ARTIFACT_VALIDATION_POLICY",
    "EXECUTION_LEASE_POLICY",
    "ExecutionLease",
    "active_execution_leases",
    "ensure_execution_schema",
    "finish_execution_lease",
    "heartbeat_execution_lease",
    "process_identity_matches",
    "process_start_token",
    "record_execution_lease",
    "recover_orphaned_executions",
    "terminate_process_tree",
    "validate_recipe_outputs",
]
