#!/usr/bin/env python3
"""Durable control-plane storage for local VoxCPM2 Dub Studio.

Telegram remains responsive and only creates/inspects jobs.  A separate local
worker claims those jobs from this SQLite database and performs the expensive
CPU render.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{3,31}$")
_RECIPE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_ACTION_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_FINAL_JOB_STATES = {"succeeded", "failed", "cancelled"}
_ACTIVE_JOB_STATES = {"queued", "running", "cancel_requested"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def studio_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    raw = str(explicit or os.getenv("DUB_STUDIO_ROOT", "")).strip()
    if raw:
        root = Path(raw).expanduser()
    elif os.name == "nt":
        root = Path(r"C:\AI-Archive\MP3Bot-Dub-Studio")
    else:
        root = repo_root() / ".runtime" / "dub_studio"
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def recipe_dir() -> Path:
    return repo_root() / "tools" / "voxcpm2" / "recipes"


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    title: str
    speaker: str
    source_url: str
    description: str
    work_root: str
    actions: dict[str, dict[str, Any]]
    outputs: dict[str, dict[str, Any]]
    raw: dict[str, Any]

    def action(self, action_name: str) -> dict[str, Any]:
        if action_name not in self.actions:
            raise KeyError(f"Recipe {self.recipe_id!r} has no action {action_name!r}")
        return dict(self.actions[action_name])

    def repair_actions(self) -> list[str]:
        return [
            name
            for name, spec in self.actions.items()
            if str(spec.get("kind", "")).lower() == "repair"
        ]


def load_recipe(recipe_id: str) -> Recipe:
    recipe_id = str(recipe_id or "").strip().lower()
    if not _RECIPE_ID_RE.fullmatch(recipe_id):
        raise ValueError("Некорректный recipe_id.")
    path = (recipe_dir() / f"{recipe_id}.json").resolve()
    allowed = recipe_dir().resolve()
    if not _is_relative_to(path, allowed) or not path.is_file():
        raise FileNotFoundError(f"Рецепт не найден: {recipe_id}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or int(payload.get("schema_version", 0)) != 1:
        raise ValueError(f"Неподдерживаемая схема рецепта: {path.name}")
    if str(payload.get("id", "")).strip().lower() != recipe_id:
        raise ValueError(f"ID внутри рецепта не совпадает с именем файла: {path.name}")

    actions = payload.get("actions")
    outputs = payload.get("outputs", {})
    if not isinstance(actions, dict) or not actions:
        raise ValueError(f"В рецепте {recipe_id} нет actions.")
    if not isinstance(outputs, dict):
        raise ValueError(f"В рецепте {recipe_id} outputs должен быть объектом.")

    for name, spec in actions.items():
        if not _ACTION_RE.fullmatch(str(name)):
            raise ValueError(f"Некорректное имя action: {name}")
        if not isinstance(spec, dict):
            raise ValueError(f"Action {name} должен быть объектом.")
        runner = str(spec.get("runner", "")).lower()
        if runner not in {"powershell", "python_module"}:
            raise ValueError(f"Action {name}: запрещён runner {runner!r}.")
        if runner == "powershell":
            script = (repo_root() / str(spec.get("script", ""))).resolve()
            allowed_scripts = (repo_root() / "tools" / "voxcpm2").resolve()
            if not _is_relative_to(script, allowed_scripts) or script.suffix.lower() != ".ps1":
                raise ValueError(f"Action {name}: script должен быть .ps1 внутри tools/voxcpm2.")
        else:
            module = str(spec.get("module", ""))
            if not module.startswith(("tools.voxcpm2.", "pipelines.dubbing.")):
                raise ValueError(f"Action {name}: запрещён Python module {module!r}.")

    return Recipe(
        recipe_id=recipe_id,
        title=str(payload.get("title") or recipe_id),
        speaker=str(payload.get("speaker") or ""),
        source_url=str(payload.get("source_url") or ""),
        description=str(payload.get("description") or ""),
        work_root=str(payload.get("work_root") or ""),
        actions={str(k): dict(v) for k, v in actions.items()},
        outputs={str(k): dict(v) for k, v in outputs.items()},
        raw=payload,
    )


def list_recipes() -> list[Recipe]:
    result: list[Recipe] = []
    root = recipe_dir()
    if not root.is_dir():
        return result
    for path in sorted(root.glob("*.json")):
        try:
            result.append(load_recipe(path.stem))
        except Exception:
            continue
    return result


def resolve_recipe_path(value: str, *, work_root: str = "") -> Path:
    """Resolve a recipe-controlled output path without accepting user shell input."""
    expanded = os.path.expandvars(str(value or ""))
    expanded = expanded.replace("{work_root}", str(work_root or ""))
    path = Path(expanded).expanduser()
    return path.resolve()


class DubStore:
    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = studio_root(root)
        self.db_path = self.root / "dub_studio.sqlite3"
        self.logs_dir = self.root / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS dub_projects (
                    id TEXT PRIMARY KEY,
                    recipe_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    owner_user_id INTEGER NOT NULL,
                    owner_chat_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    work_root TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_dub_projects_owner_updated
                ON dub_projects(owner_user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS dub_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL REFERENCES dub_projects(id) ON DELETE CASCADE,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    log_path TEXT NOT NULL DEFAULT '',
                    worker_id TEXT NOT NULL DEFAULT '',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    claimed_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_dub_jobs_queue
                ON dub_jobs(status, id);

                CREATE TABLE IF NOT EXISTS dub_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL REFERENCES dub_projects(id) ON DELETE CASCADE,
                    job_id INTEGER,
                    event_type TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    delivered_at TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_dub_events_delivery
                ON dub_events(delivered_at, id);

                CREATE TABLE IF NOT EXISTS dub_workers (
                    worker_id TEXT PRIMARY KEY,
                    pid INTEGER NOT NULL,
                    hostname TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_job_id INTEGER,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            conn.commit()

    def _new_project_id(self) -> str:
        return "dub-" + uuid.uuid4().hex[:10]

    def create_project(
        self,
        recipe_id: str,
        *,
        owner_user_id: int,
        owner_chat_id: int,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        recipe = load_recipe(recipe_id)
        now = utc_now()
        project_id = self._new_project_id()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO dub_projects(
                    id, recipe_id, title, source_url, owner_user_id, owner_chat_id,
                    status, stage, progress, work_root, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'draft', 'created', 0, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    recipe.recipe_id,
                    str(title or recipe.title),
                    recipe.source_url,
                    int(owner_user_id),
                    int(owner_chat_id),
                    recipe.work_root,
                    _json_dump(metadata or {}),
                    now,
                    now,
                ),
            )
            self._insert_event(
                conn,
                project_id,
                None,
                "project_created",
                "info",
                f"Создан проект {project_id}: {title or recipe.title}",
                {"recipe_id": recipe.recipe_id},
            )
            conn.commit()
        return self.get_project(project_id)

    def _row_project(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["metadata"] = _json_load(item.pop("metadata_json", "{}"), {})
        item["progress"] = int(item.get("progress") or 0)
        return item

    def _row_job(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["payload"] = _json_load(item.pop("payload_json", "{}"), {})
        item["result"] = _json_load(item.pop("result_json", "{}"), {})
        item["cancel_requested"] = bool(item.get("cancel_requested"))
        item["progress"] = int(item.get("progress") or 0)
        return item

    def get_project(self, project_id: str) -> dict[str, Any]:
        project_id = str(project_id or "").strip().lower()
        if not _PROJECT_ID_RE.fullmatch(project_id):
            raise ValueError("Некорректный project_id.")
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM dub_projects WHERE id=?", (project_id,)).fetchone()
        project = self._row_project(row)
        if not project:
            raise KeyError(f"Проект не найден: {project_id}")
        return project

    def latest_project(self, owner_user_id: int | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM dub_projects"
        params: tuple[Any, ...] = ()
        if owner_user_id is not None:
            query += " WHERE owner_user_id=?"
            params = (int(owner_user_id),)
        query += " ORDER BY updated_at DESC LIMIT 1"
        with self.connect() as conn:
            row = conn.execute(query, params).fetchone()
        return self._row_project(row)

    def list_projects(
        self,
        *,
        owner_user_id: int | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 50))
        query = "SELECT * FROM dub_projects"
        params: list[Any] = []
        if owner_user_id is not None:
            query += " WHERE owner_user_id=?"
            params.append(int(owner_user_id))
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._row_project(row) for row in rows if row is not None]  # type: ignore[list-item]

    def recent_jobs(self, project_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM dub_jobs WHERE project_id=? ORDER BY id DESC LIMIT ?",
                (project_id, max(1, min(int(limit), 50))),
            ).fetchall()
        return [self._row_job(row) for row in rows if row is not None]  # type: ignore[list-item]

    def get_job(self, job_id: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM dub_jobs WHERE id=?", (int(job_id),)).fetchone()
        job = self._row_job(row)
        if not job:
            raise KeyError(f"Задание не найдено: {job_id}")
        return job

    def enqueue_job(
        self,
        project_id: str,
        action: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        recipe = load_recipe(project["recipe_id"])
        action = str(action or "").strip().lower()
        recipe.action(action)
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                """
                SELECT id, status FROM dub_jobs
                WHERE project_id=? AND status IN ('queued','running','cancel_requested')
                ORDER BY id DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if active:
                conn.rollback()
                raise RuntimeError(
                    f"У проекта уже есть активное задание #{active['id']} ({active['status']})."
                )
            cur = conn.execute(
                """
                INSERT INTO dub_jobs(
                    project_id, action, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, 'queued', ?, ?, ?)
                """,
                (project_id, action, _json_dump(payload or {}), now, now),
            )
            job_id = int(cur.lastrowid)
            conn.execute(
                """
                UPDATE dub_projects
                SET status='queued', stage=?, progress=0, last_error='', updated_at=?
                WHERE id=?
                """,
                (f"queued:{action}", now, project_id),
            )
            self._insert_event(
                conn,
                project_id,
                job_id,
                "job_queued",
                "info",
                f"Задание #{job_id} поставлено в очередь: {action}",
                {"action": action},
            )
            conn.commit()
        return self.get_job(job_id)

    def claim_next_job(self, worker_id: str) -> dict[str, Any] | None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM dub_jobs WHERE status='queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            job_id = int(row["id"])
            updated = conn.execute(
                """
                UPDATE dub_jobs
                SET status='running', stage='starting', progress=1,
                    worker_id=?, claimed_at=?, updated_at=?
                WHERE id=? AND status='queued'
                """,
                (worker_id, now, now, job_id),
            ).rowcount
            if updated != 1:
                conn.rollback()
                return None
            conn.execute(
                """
                UPDATE dub_projects
                SET status='rendering', stage='starting', progress=1,
                    last_error='', updated_at=?
                WHERE id=?
                """,
                (now, str(row["project_id"])),
            )
            self._insert_event(
                conn,
                str(row["project_id"]),
                job_id,
                "job_started",
                "info",
                f"Worker начал задание #{job_id}",
                {"worker_id": worker_id},
            )
            conn.commit()
        return self.get_job(job_id)

    def update_job_progress(
        self,
        job_id: int,
        *,
        progress: int,
        stage: str,
        message: str = "",
    ) -> None:
        progress = max(0, min(int(progress), 99))
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT project_id, progress, stage FROM dub_jobs WHERE id=?",
                (int(job_id),),
            ).fetchone()
            if row is None:
                return
            conn.execute(
                """
                UPDATE dub_jobs SET progress=?, stage=?, updated_at=? WHERE id=?
                """,
                (progress, str(stage)[:160], now, int(job_id)),
            )
            conn.execute(
                """
                UPDATE dub_projects SET progress=?, stage=?, updated_at=? WHERE id=?
                """,
                (progress, str(stage)[:160], now, str(row["project_id"])),
            )
            if message and (
                str(row["stage"]) != str(stage)
                or progress >= int(row["progress"] or 0) + 10
            ):
                self._insert_event(
                    conn,
                    str(row["project_id"]),
                    int(job_id),
                    "job_progress",
                    "info",
                    str(message)[:1000],
                    {"progress": progress, "stage": str(stage)[:160]},
                )
            conn.commit()

    def request_cancel(self, project_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM dub_jobs
                WHERE project_id=? AND status IN ('queued','running','cancel_requested')
                ORDER BY id DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if row is None:
                conn.rollback()
                raise RuntimeError("У проекта нет активного задания.")
            job_id = int(row["id"])
            if row["status"] == "queued":
                conn.execute(
                    """
                    UPDATE dub_jobs
                    SET status='cancelled', cancel_requested=1,
                        finished_at=?, updated_at=? WHERE id=?
                    """,
                    (now, now, job_id),
                )
                conn.execute(
                    """
                    UPDATE dub_projects
                    SET status='cancelled', stage='cancelled', updated_at=? WHERE id=?
                    """,
                    (now, project_id),
                )
                event_type = "job_cancelled"
                message = f"Задание #{job_id} отменено до запуска."
            else:
                conn.execute(
                    """
                    UPDATE dub_jobs
                    SET status='cancel_requested', cancel_requested=1, updated_at=? WHERE id=?
                    """,
                    (now, job_id),
                )
                conn.execute(
                    """
                    UPDATE dub_projects
                    SET status='cancelling', stage='cancel_requested', updated_at=? WHERE id=?
                    """,
                    (now, project_id),
                )
                event_type = "job_cancel_requested"
                message = f"Запрошена остановка задания #{job_id}."
            self._insert_event(conn, project_id, job_id, event_type, "warning", message, {})
            conn.commit()
        return self.get_job(job_id)

    def is_cancel_requested(self, job_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT cancel_requested, status FROM dub_jobs WHERE id=?",
                (int(job_id),),
            ).fetchone()
        return bool(row and (row["cancel_requested"] or row["status"] == "cancel_requested"))

    def finish_job(
        self,
        job_id: int,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        status = str(status).lower()
        if status not in _FINAL_JOB_STATES:
            raise ValueError(f"Недопустимый финальный статус: {status}")
        now = utc_now()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT project_id, action FROM dub_jobs WHERE id=?",
                (int(job_id),),
            ).fetchone()
            if row is None:
                conn.rollback()
                return
            project_id = str(row["project_id"])
            project_status = {
                "succeeded": "done",
                "failed": "failed",
                "cancelled": "cancelled",
            }[status]
            stage = {
                "succeeded": "completed",
                "failed": "failed",
                "cancelled": "cancelled",
            }[status]
            progress = 100 if status == "succeeded" else 0
            conn.execute(
                """
                UPDATE dub_jobs
                SET status=?, progress=?, stage=?, result_json=?, error=?,
                    finished_at=?, updated_at=?
                WHERE id=?
                """,
                (
                    status,
                    progress,
                    stage,
                    _json_dump(result or {}),
                    str(error or "")[:8000],
                    now,
                    now,
                    int(job_id),
                ),
            )
            conn.execute(
                """
                UPDATE dub_projects
                SET status=?, stage=?, progress=?, last_error=?, updated_at=?
                WHERE id=?
                """,
                (
                    project_status,
                    stage,
                    progress,
                    str(error or "")[:4000],
                    now,
                    project_id,
                ),
            )
            event_type = {
                "succeeded": "job_succeeded",
                "failed": "job_failed",
                "cancelled": "job_cancelled",
            }[status]
            level = "info" if status == "succeeded" else "error" if status == "failed" else "warning"
            message = (
                f"Задание #{job_id} завершено: {row['action']}"
                if status == "succeeded"
                else f"Задание #{job_id}: {stage}. {str(error)[:500]}"
            )
            self._insert_event(
                conn,
                project_id,
                int(job_id),
                event_type,
                level,
                message.strip(),
                result or {},
            )
            conn.commit()

    def set_job_log_path(self, job_id: int, path: Path) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE dub_jobs SET log_path=?, updated_at=? WHERE id=?",
                (str(path), now, int(job_id)),
            )
            conn.commit()

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        project_id: str,
        job_id: int | None,
        event_type: str,
        level: str,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO dub_events(
                project_id, job_id, event_type, level, message, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                job_id,
                str(event_type),
                str(level),
                str(message)[:2000],
                _json_dump(payload),
                utc_now(),
            ),
        )

    def undelivered_terminal_events(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT e.*, p.owner_chat_id, p.title AS project_title
                FROM dub_events e
                JOIN dub_projects p ON p.id=e.project_id
                WHERE e.delivered_at=''
                  AND e.event_type IN ('job_succeeded','job_failed','job_cancelled')
                ORDER BY e.id LIMIT ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = _json_load(item.pop("payload_json", "{}"), {})
            result.append(item)
        return result

    def mark_event_delivered(self, event_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE dub_events SET delivered_at=? WHERE id=?",
                (utc_now(), int(event_id)),
            )
            conn.commit()

    def register_worker(
        self,
        worker_id: str,
        *,
        pid: int,
        status: str,
        current_job_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO dub_workers(
                    worker_id, pid, hostname, status, current_job_id,
                    started_at, heartbeat_at, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    pid=excluded.pid,
                    hostname=excluded.hostname,
                    status=excluded.status,
                    current_job_id=excluded.current_job_id,
                    heartbeat_at=excluded.heartbeat_at,
                    details_json=excluded.details_json
                """,
                (
                    worker_id,
                    int(pid),
                    socket.gethostname(),
                    str(status),
                    current_job_id,
                    now,
                    now,
                    _json_dump(details or {}),
                ),
            )
            conn.commit()

    def worker_heartbeat(
        self,
        worker_id: str,
        *,
        status: str,
        current_job_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE dub_workers
                SET status=?, current_job_id=?, heartbeat_at=?, details_json=?
                WHERE worker_id=?
                """,
                (str(status), current_job_id, now, _json_dump(details or {}), worker_id),
            )
            conn.commit()

    def latest_worker(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM dub_workers ORDER BY heartbeat_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["details"] = _json_load(item.pop("details_json", "{}"), {})
        return item

    def recover_abandoned_jobs(self, stale_seconds: int = 180) -> int:
        """Requeue jobs owned by a worker whose heartbeat is stale."""
        cutoff = time.time() - max(30, int(stale_seconds))
        now = utc_now()
        recovered = 0
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT j.id, j.project_id, j.cancel_requested, j.status, w.heartbeat_at
                FROM dub_jobs j
                LEFT JOIN dub_workers w ON w.worker_id=j.worker_id
                WHERE j.status IN ('running','cancel_requested')
                """
            ).fetchall()
            for row in rows:
                heartbeat = str(row["heartbeat_at"] or "")
                try:
                    heartbeat_ts = datetime.fromisoformat(heartbeat).timestamp()
                except (TypeError, ValueError):
                    heartbeat_ts = 0.0
                if heartbeat_ts >= cutoff:
                    continue
                if bool(row["cancel_requested"]) or row["status"] == "cancel_requested":
                    new_status = "cancelled"
                    project_status = "cancelled"
                    stage = "cancelled"
                else:
                    new_status = "queued"
                    project_status = "queued"
                    stage = "recovered_after_worker_stop"
                conn.execute(
                    """
                    UPDATE dub_jobs SET status=?, worker_id='', claimed_at='',
                        stage=?, updated_at=? WHERE id=?
                    """,
                    (new_status, stage, now, int(row["id"])),
                )
                conn.execute(
                    """
                    UPDATE dub_projects SET status=?, stage=?, updated_at=? WHERE id=?
                    """,
                    (project_status, stage, now, str(row["project_id"])),
                )
                recovered += 1
            conn.commit()
        return recovered


def worker_is_fresh(worker: dict[str, Any] | None, *, max_age_seconds: int = 45) -> bool:
    if not worker:
        return False
    try:
        age = time.time() - datetime.fromisoformat(str(worker["heartbeat_at"])).timestamp()
    except (KeyError, TypeError, ValueError):
        return False
    return age <= max(5, int(max_age_seconds))


__all__ = [
    "DubStore",
    "Recipe",
    "list_recipes",
    "load_recipe",
    "repo_root",
    "resolve_recipe_path",
    "studio_root",
    "worker_is_fresh",
]
