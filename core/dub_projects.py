from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PROJECT_ID_RE = re.compile(r"^dub-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{10}(?:-[0-9]+)?$")
PROJECT_MARKER_RE = re.compile(r"(?:#|<code>)?DUBPROJECT:([A-Za-z0-9_-]+)")
MIN_TRANSLATION_CHARS = 20
MAX_TRANSLATION_CHARS = int(os.getenv("DUB_TRANSLATION_MAX_CHARS", "1000000"))

_PROJECT_LOCKS: dict[str, threading.RLock] = {}
_PROJECT_LOCKS_GUARD = threading.Lock()


class DubProjectError(RuntimeError):
    """A user-facing production-project validation error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def projects_root() -> Path:
    configured = os.getenv("DUB_PROJECTS_DIR", "").strip()
    root = Path(configured) if configured else Path("data") / "dub_projects"
    return root.expanduser().resolve()


def _project_lock(project_id: str) -> threading.RLock:
    with _PROJECT_LOCKS_GUARD:
        return _PROJECT_LOCKS.setdefault(project_id, threading.RLock())


def validate_project_id(project_id: str) -> str:
    value = str(project_id or "").strip()
    if not PROJECT_ID_RE.fullmatch(value):
        raise DubProjectError("Некорректный идентификатор проекта дубляжа.")
    return value


def project_dir(project_id: str) -> Path:
    return projects_root() / validate_project_id(project_id)


def manifest_path(project_id: str) -> Path:
    return project_dir(project_id) / "manifest.json"


def project_marker(project_id: str) -> str:
    return f"DUBPROJECT:{validate_project_id(project_id)}"


def extract_project_id(value: str | None) -> str | None:
    match = PROJECT_MARKER_RE.search(str(value or ""))
    if not match:
        return None
    candidate = match.group(1)
    try:
        return validate_project_id(candidate)
    except DubProjectError:
        return None


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _append_event(project_id: str, event: str, **details: Any) -> None:
    record = {"at": utc_now(), "event": event, **details}
    path = project_dir(project_id) / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        try:
            os.fsync(stream.fileno())
        except OSError:
            pass


def _source_fingerprint(source: dict[str, Any]) -> str:
    stable = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:10]


def _new_project_id(source: dict[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"dub-{stamp}-{_source_fingerprint(source)}"
    root = projects_root()
    candidate = base
    suffix = 2
    while (root / candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _validate_source(source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise DubProjectError("Источник проекта не задан.")
    kind = str(source.get("kind") or "").strip()
    if kind == "url":
        url = str(source.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            raise DubProjectError("Нужна полная ссылка http:// или https://.")
        return {"kind": "url", "url": url}
    if kind == "telegram_file":
        file_id = str(source.get("file_id") or "").strip()
        if not file_id:
            raise DubProjectError("У исходного Telegram-файла отсутствует file_id.")
        return {
            "kind": "telegram_file",
            "file_id": file_id,
            "file_unique_id": str(source.get("file_unique_id") or "").strip(),
            "filename": str(source.get("filename") or "source.mp4").strip() or "source.mp4",
            "mime_type": str(source.get("mime_type") or "video/mp4").strip(),
            "file_size": int(source.get("file_size") or 0),
        }
    raise DubProjectError("Поддерживаются ссылка или исходный видеофайл Telegram.")


def create_project(*, owner_user_id: int, source: dict[str, Any]) -> dict[str, Any]:
    source = _validate_source(source)
    project_id = _new_project_id(source)
    root = project_dir(project_id)
    for name in (
        "source",
        "editorial",
        "references",
        "segments",
        "synthesis",
        "masters",
        "outputs",
        "reports",
        "incoming",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)

    now = utc_now()
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project_id": project_id,
        "created_at": now,
        "updated_at": now,
        "owner_user_id": int(owner_user_id),
        "status": "awaiting_translation",
        "source": source,
        "translation": {
            "state": "missing",
            "origin": "approved_external",
            "locked": False,
        },
        "policy": {
            "translation_is_preapproved": True,
            "rewrite_translation": False,
            "auto_shorten_translation": False,
            "shorts_max_seconds": 180.0,
            "shorts_hardsub": True,
            "long_hardsub": False,
            "translate_on_screen_text": False,
            "synthesis_engine": "VoxCPM2",
            "synthesis_device": "cpu",
            "hidden_tts_fallback": False,
            "sidechain": False,
            "original_audio_mode": "constant_gain",
        },
        "production": {
            "profile": "pending_source_probe",
            "stage": "editorial_intake",
            "ready": False,
        },
    }
    _atomic_write_json(manifest_path(project_id), manifest)
    _append_event(project_id, "project_created", owner_user_id=int(owner_user_id), source_kind=source["kind"])
    return manifest


def load_project(project_id: str) -> dict[str, Any]:
    path = manifest_path(project_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DubProjectError("Проект дубляжа не найден.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DubProjectError("Manifest проекта повреждён или недоступен.") from exc
    if not isinstance(payload, dict) or payload.get("project_id") != project_id:
        raise DubProjectError("Manifest проекта имеет неверный формат.")
    return payload


def save_project(project_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    project_id = validate_project_id(project_id)
    if manifest.get("project_id") != project_id:
        raise DubProjectError("Нельзя сохранить manifest другого проекта.")
    manifest["updated_at"] = utc_now()
    _atomic_write_json(manifest_path(project_id), manifest)
    return manifest


def assert_project_owner(manifest: dict[str, Any], user_id: int, *, admin_ids: set[int] | None = None) -> None:
    owner = int(manifest.get("owner_user_id") or 0)
    admins = admin_ids or set()
    if int(user_id) != owner and int(user_id) not in admins:
        raise DubProjectError("Этот проект принадлежит другому пользователю.")


def attach_source_file(project_id: str, source_path: Path) -> dict[str, Any]:
    source_path = Path(source_path).expanduser().resolve()
    if not source_path.is_file():
        raise DubProjectError("Загруженный исходный файл не найден.")
    with _project_lock(project_id):
        manifest = load_project(project_id)
        digest = hashlib.sha256()
        with source_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        manifest["source"]["local_path"] = str(source_path)
        manifest["source"]["sha256"] = digest.hexdigest()
        manifest["source"]["bytes"] = source_path.stat().st_size
        save_project(project_id, manifest)
        _append_event(project_id, "source_file_attached", bytes=source_path.stat().st_size, sha256=digest.hexdigest())
        return manifest


def normalize_approved_translation(text: str) -> str:
    value = str(text or "").replace("\ufeff", "").replace("\x00", "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "\n".join(line.rstrip() for line in value.split("\n")).strip()
    if len(value) < MIN_TRANSLATION_CHARS:
        raise DubProjectError("Перевод слишком короткий. Пришлите полный утверждённый текст.")
    if len(value) > MAX_TRANSLATION_CHARS:
        raise DubProjectError(f"Перевод слишком большой: {len(value)} символов; лимит {MAX_TRANSLATION_CHARS}.")
    return value


_UNIT_HEADING_RE = re.compile(
    r"^(?:\[\s*\d+\s*\]|\d+[.)]|#{1,6}\s*TU[-_ ]?\d+|TU[-_ ]?\d+)\s*$",
    re.IGNORECASE,
)


def split_translation_units(text: str) -> list[dict[str, Any]]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    if not paragraphs:
        return []
    units: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        lines = paragraph.splitlines()
        heading = None
        if lines and _UNIT_HEADING_RE.fullmatch(lines[0].strip()):
            heading = lines.pop(0).strip()
        body = "\n".join(lines).strip()
        if not body:
            continue
        units.append(
            {
                "id": f"TU-{len(units) + 1:03d}",
                "source_heading": heading,
                "display_text": body,
                "spoken_text": body,
                "spoken_text_state": "identical",
            }
        )
    if not units:
        units.append(
            {
                "id": "TU-001",
                "source_heading": None,
                "display_text": text,
                "spoken_text": text,
                "spoken_text_state": "identical",
            }
        )
    return units


def attach_approved_translation(
    project_id: str,
    *,
    text: str,
    approved_by_user_id: int,
    original_filename: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_approved_translation(text)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    units = split_translation_units(normalized)
    if not units:
        raise DubProjectError("Не удалось выделить ни одного абзаца перевода.")

    with _project_lock(project_id):
        manifest = load_project(project_id)
        revision = int((manifest.get("translation") or {}).get("revision") or 0) + 1
        editorial = project_dir(project_id) / "editorial"
        revisions = editorial / "revisions"
        translation_path = revisions / f"translation_ru_r{revision:03d}.txt"
        units_path = revisions / f"translation_units_r{revision:03d}.json"
        current_translation = editorial / "translation_ru_approved.txt"
        current_units = editorial / "translation_units.json"
        _atomic_write_text(translation_path, normalized + "\n")
        _atomic_write_json(units_path, units)
        _atomic_write_text(current_translation, normalized + "\n")
        _atomic_write_json(current_units, units)

        manifest["translation"] = {
            "state": "approved",
            "origin": "approved_external",
            "locked": True,
            "revision": revision,
            "sha256": digest,
            "approved_at": utc_now(),
            "approved_by_user_id": int(approved_by_user_id),
            "original_filename": str(original_filename or "telegram-message"),
            "display_text_path": str(translation_path),
            "units_path": str(units_path),
            "current_display_text_path": str(current_translation),
            "current_units_path": str(current_units),
            "character_count": len(normalized),
            "word_count": len(re.findall(r"\S+", normalized)),
            "unit_count": len(units),
        }
        manifest["status"] = "translation_ready"
        manifest["production"]["stage"] = "preflight_pending"
        manifest["production"]["ready"] = False
        save_project(project_id, manifest)
        _append_event(
            project_id,
            "approved_translation_attached",
            revision=revision,
            sha256=digest,
            character_count=len(normalized),
            unit_count=len(units),
        )
        return manifest


def cancel_project(project_id: str, *, cancelled_by_user_id: int) -> dict[str, Any]:
    with _project_lock(project_id):
        manifest = load_project(project_id)
        manifest["status"] = "cancelled"
        manifest["production"]["ready"] = False
        manifest["production"]["stage"] = "cancelled"
        save_project(project_id, manifest)
        _append_event(project_id, "project_cancelled", cancelled_by_user_id=int(cancelled_by_user_id))
        return manifest


def record_preflight(project_id: str, report: dict[str, Any]) -> dict[str, Any]:
    with _project_lock(project_id):
        manifest = load_project(project_id)
        report_path = project_dir(project_id) / "reports" / "preflight.json"
        _atomic_write_json(report_path, report)
        ok = bool(report.get("ok"))
        manifest["preflight"] = {
            "ok": ok,
            "checked_at": report.get("checked_at") or utc_now(),
            "report_path": str(report_path),
            "blocking_error_count": len(report.get("blocking_errors") or []),
            "warning_count": len(report.get("warnings") or []),
        }
        manifest["production"]["profile"] = report.get("profile") or "pending_source_probe"
        manifest["production"]["ready"] = ok
        manifest["production"]["stage"] = "ready_for_production" if ok else "preflight_failed"
        manifest["status"] = "ready_for_production" if ok else "translation_ready"
        save_project(project_id, manifest)
        _append_event(project_id, "preflight_completed", ok=ok)
        return manifest
