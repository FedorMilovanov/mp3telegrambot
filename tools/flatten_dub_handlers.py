#!/usr/bin/env python3
"""Flatten active Dub handler shadow packages into one source owner each."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_function(rel: str, name: str, replacement: str) -> None:
    text = read(rel)
    tree = ast.parse(text, filename=rel)
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    if len(nodes) != 1:
        raise RuntimeError(f"{rel}: expected one {name}, got {len(nodes)}")
    node = nodes[0]
    lines = text.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno or node.lineno
    lines[start:end] = [replacement.rstrip() + "\n"]
    write(rel, "".join(lines))


def flatten_audio_repair() -> None:
    rel = "handlers/dub_audio_repair.py"
    text = read(rel)
    text = text.replace("import hashlib\n", "import asyncio\nimport hashlib\n", 1)
    text = text.replace("import re\n", "import re\nimport time\nfrom contextlib import contextmanager\n", 1)
    text = text.replace("from typing import Any, Iterable\n", "from typing import Any, Iterable, Iterator\n", 1)
    import_anchor = "from services.dub_studio import DubStore, studio_root, utc_now\n"
    if import_anchor not in text:
        raise RuntimeError("audio repair import anchor missing")
    text = text.replace(import_anchor, import_anchor + "from tools.voxcpm2 import clean_production_core as strict_core\n", 1)
    const_anchor = "_ACTIVE_JOB_STATES = {\"queued\", \"running\", \"cancel_requested\"}\n"
    helpers = '''_ACTIVE_JOB_STATES = {"queued", "running", "cancel_requested"}\n_DUBFIX_LOCK = asyncio.Lock()\n_DUBFIX_PROCESS_LOCK_STALE_SECONDS = 30 * 60\n\n\ndef _process_lock_path() -> Path:\n    root = Path(studio_root()).resolve()\n    root.mkdir(parents=True, exist_ok=True)\n    return root / ".dubfix.request.lock"\n\n\n@contextmanager\ndef _dubfix_process_lock() -> Iterator[Path]:\n    """Hold an atomic cross-process lock for request-write + enqueue."""\n    path = _process_lock_path()\n    descriptor: int | None = None\n    for attempt in range(2):\n        try:\n            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)\n            break\n        except FileExistsError as exc:\n            try:\n                age = max(0.0, time.time() - path.stat().st_mtime)\n            except FileNotFoundError:\n                continue\n            if age > _DUBFIX_PROCESS_LOCK_STALE_SECONDS and attempt == 0:\n                path.unlink(missing_ok=True)\n                continue\n            raise RuntimeError(\n                "Другой процесс уже создаёт /dubfix request; повторите команду после завершения."\n            ) from exc\n    if descriptor is None:\n        raise RuntimeError("Не удалось захватить межпроцессный /dubfix lock.")\n    try:\n        payload = json.dumps(\n            {"pid": os.getpid(), "acquired_unix": time.time()},\n            ensure_ascii=False, allow_nan=False,\n        ).encode("utf-8")\n        os.write(descriptor, payload)\n        os.fsync(descriptor)\n        yield path\n    finally:\n        try:\n            os.close(descriptor)\n        finally:\n            path.unlink(missing_ok=True)\n\n\ndef _strict_ids(values: Any, *, field: str) -> list[int]:\n    if not isinstance(values, (list, tuple)) or not values:\n        raise RuntimeError(f"{field} должен быть непустым списком ID.")\n    result: list[int] = []\n    seen: set[int] = set()\n    for position, value in enumerate(values, start=1):\n        item_id = strict_core._strict_int(\n            value, field=f"{field}[{position}]", low=1, high=2**31 - 1\n        )\n        if item_id in seen:\n            raise RuntimeError(f"{field} содержит повторный ID={item_id}.")\n        seen.add(item_id)\n        result.append(item_id)\n    return result\n'''
    if const_anchor not in text:
        raise RuntimeError("audio repair constant anchor missing")
    write(rel, text.replace(const_anchor, helpers, 1))

    replace_function(rel, "load_repair_segments", '''def load_repair_segments(project_id: str) -> list[dict[str, Any]]:\n    path = _segments_path(project_id)\n    if not path.is_file():\n        raise RuntimeError(\n            "У проекта ещё нет segments_ru_final.json; сначала завершите обычный рендер."\n        )\n    try:\n        payload = json.loads(path.read_text(encoding="utf-8-sig"))\n    except (OSError, json.JSONDecodeError) as exc:\n        raise RuntimeError("segments_ru_final.json повреждён.") from exc\n    if not isinstance(payload, list) or not payload:\n        raise RuntimeError("Список реплик проекта пуст или повреждён.")\n    result: list[dict[str, Any]] = []\n    raw_ids: list[Any] = []\n    for position, raw in enumerate(payload, start=1):\n        if not isinstance(raw, dict):\n            raise RuntimeError(\n                f"segment[{position}] должен быть JSON-объектом, получено {type(raw).__name__}."\n            )\n        item = dict(raw)\n        raw_ids.append(item.get("id"))\n        start = strict_core._finite(item.get("start"), field=f"segment[{position}].start")\n        end = strict_core._finite(item.get("end"), field=f"segment[{position}].end")\n        if start < 0.0 or end <= start:\n            raise RuntimeError(f"Некорректный timing segment[{position}].")\n        item["start"] = start\n        item["end"] = end\n        if item.get("source_end") is not None:\n            source_end = strict_core._finite(\n                item.get("source_end"), field=f"segment[{position}].source_end"\n            )\n            if source_end < start:\n                raise RuntimeError(f"source_end segment[{position}] раньше start.")\n            item["source_end"] = source_end\n        item["start_delay_ms"] = strict_core._strict_int(\n            item.get("start_delay_ms", 0),\n            field=f"segment[{position}].start_delay_ms", low=0, high=1500,\n        )\n        if not str(item.get("text") or "").strip():\n            raise RuntimeError(f"segment[{position}] не содержит текста.")\n        result.append(item)\n    ids = _strict_ids(raw_ids, field="segments.id")\n    for item, segment_id in zip(result, ids, strict=True):\n        item["id"] = segment_id\n    return sorted(result, key=lambda item: int(item["id"]))\n''')

    replace_function(rel, "_write_repair_request", '''def _write_repair_request(\n    project: dict[str, Any],\n    segments: list[dict[str, Any]],\n    selected_ids: list[int],\n    *,\n    requested_by: int,\n) -> Path:\n    if not isinstance(project, dict):\n        raise RuntimeError("Project должен быть JSON-объектом.")\n    project_id = str(project.get("id") or "").strip()\n    if not project_id:\n        raise RuntimeError("Project ID пуст.")\n    owner_id = strict_core._strict_int(\n        requested_by, field="audio_repair.requested_by", low=1, high=2**63 - 1\n    )\n    all_ids = _strict_ids(\n        [item.get("id") if isinstance(item, dict) else None for item in segments],\n        field="segments.id",\n    )\n    selected = _strict_ids(selected_ids, field="audio_repair.segment_ids")\n    selected_set = set(selected)\n    all_set = set(all_ids)\n    if not selected_set.issubset(all_set):\n        raise RuntimeError("Выбраны неизвестные segment ID.")\n    root = _project_root(project_id)\n    input_dir = root / "input"\n    input_dir.mkdir(parents=True, exist_ok=True)\n    segments_path = _segments_path(project_id)\n    if not segments_path.is_file():\n        raise RuntimeError("segments_ru_final.json исчез до создания repair request.")\n    digest = hashlib.sha256(segments_path.read_bytes()).hexdigest()\n    payload = {\n        "schema_version": 1,\n        "project_id": project_id,\n        "segment_ids": selected,\n        "repair_all": selected_set == all_set,\n        "segments_sha256": digest,\n        "requested_by": owner_id,\n        "requested_at": utc_now(),\n    }\n    destination = input_dir / "audio_repair.json"\n    temporary = destination.with_name(destination.name + f".tmp.{os.getpid()}.{id(payload)}")\n    try:\n        temporary.write_text(\n            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),\n            encoding="utf-8",\n        )\n        os.replace(temporary, destination)\n    finally:\n        temporary.unlink(missing_ok=True)\n    return destination\n''')

    text = read(rel)
    signature = "async def dubfix_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:\n"
    if signature not in text:
        raise RuntimeError("dubfix command signature missing")
    text = text.replace(signature, "async def _dubfix_command_unlocked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:\n", 1)
    register_anchor = "\ndef register_dub_audio_repair_handlers(application: Any) -> None:\n"
    wrapper = '''\nasync def dubfix_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:\n    async with _DUBFIX_LOCK:\n        try:\n            with _dubfix_process_lock():\n                await _dubfix_command_unlocked(update, context)\n        except RuntimeError as exc:\n            if update.effective_message is None:\n                raise\n            await update.effective_message.reply_text(f"⚠️ {exc}")\n\n\ndef register_dub_audio_repair_handlers(application: Any) -> None:\n'''
    if register_anchor not in text:
        raise RuntimeError("audio repair register anchor missing")
    text = text.replace(register_anchor, wrapper, 1)
    if '    "_dubfix_process_lock",\n' not in text:
        text = text.replace('__all__ = [\n', '__all__ = [\n    "_dubfix_process_lock",\n    "_write_repair_request",\n', 1)
    write(rel, text)

    package = ROOT / "handlers/dub_audio_repair/__init__.py"
    if not package.is_file():
        raise RuntimeError("audio repair shadow package missing")
    package.unlink()
    print("flattened handlers.dub_audio_repair")


def flatten_wizard() -> None:
    rel = "handlers/dub_wizard.py"
    text = read(rel)
    text = text.replace("from urllib.parse import parse_qs, urlparse\n", "")
    text = text.replace("from services.dub_studio import DubStore, studio_root\n", "from services.dub_studio import DubStore, studio_root\nfrom services.speech_backends import DEFAULT_MODEL_PROFILE_ID\n", 1)
    old_import = '''from services.tts_profile_selection import (\n    ProductionTTSProfileChoice,\n    normalize_new_production_tts_request,\n    production_tts_profile_choice,\n    production_tts_profile_choices,\n    write_durable_request,\n)\n'''
    new_import = '''from services.tts_profile_selection import (\n    ProductionTTSProfileChoice,\n    normalize_new_production_tts_request,\n    production_tts_profile_choice,\n    production_tts_profile_choices,\n    rebind_inactive_project_tts_profile,\n    write_durable_request,\n)\nfrom tools.voxcpm2 import clean_source_download, generic_project_runtime\n'''
    if old_import not in text:
        raise RuntimeError("wizard TTS import anchor missing")
    text = text.replace(old_import, new_import, 1)
    write(rel, text)

    replace_function(rel, "_extract_youtube_video_id", '''def _extract_youtube_video_id(value: str) -> tuple[str, str]:\n    raw = str(value or "").strip()\n    if not raw.startswith(("http://", "https://")):\n        raw = "https://" + raw\n    video_id = clean_source_download._url_video_id(raw)\n    if not video_id:\n        raise ValueError(\n            "Нужна каноническая ссылка на один YouTube-ролик: watch, youtu.be, Shorts, live или embed."\n        )\n    return video_id, f"https://youtube.com/watch?v={video_id}"\n''')

    replace_function(rel, "dubtts_command", '''async def dubtts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:\n    if not await _admin(update):\n        return\n    args = [str(value).strip() for value in (context.args or []) if str(value).strip()]\n    if not args:\n        text, page, page_count = _catalog_text(0)\n        await update.effective_message.reply_text(\n            text, parse_mode="HTML", reply_markup=_catalog_keyboard(page, page_count)\n        )\n        return\n    if len(args) != 2:\n        await update.effective_message.reply_text(\n            "Использование:\\n<code>/dubtts</code> — каталог моделей\\n"\n            "<code>/dubtts PROJECT_ID PROFILE_ID</code> — сменить модель у draft/failed/cancelled проекта.",\n            parse_mode="HTML",\n        )\n        return\n    project_id, profile_id = args[0].casefold(), args[1]\n    try:\n        store = DubStore()\n        request_path = store.root / "projects" / project_id / "request.json"\n        result = rebind_inactive_project_tts_profile(\n            store, project_id, owner_user_id=update.effective_user.id,\n            request_path=request_path, profile_value=profile_id,\n        )\n        choice = production_tts_profile_choice(result.choice.profile_id)\n        status = "уже был закреплён" if not result.changed else "закреплён"\n        await update.effective_message.reply_text(\n            "🎙 <b>TTS-профиль проекта обновлён</b>\\n\\n"\n            f"Проект: <code>{html.escape(result.project_id)}</code>\\n"\n            f"Профиль {status}: <code>{html.escape(choice.profile_id)}</code>\\n"\n            f"Backend: <code>{html.escape(choice.backend_id)}</code>\\n"\n            f"Revision: <code>{html.escape(choice.model_revision)}</code>\\n"\n            f"Fingerprint: <code>{html.escape(choice.fingerprint[:12])}</code>\\n\\n"\n            "Изменение разрешено только без active job. Параметры TTS нового профиля сброшены к его валидированным defaults.",\n            parse_mode="HTML",\n        )\n    except Exception as exc:\n        await update.effective_message.reply_text(\n            "⚠️ TTS-профиль не изменён: " + html.escape(_short(str(exc), 1400)),\n            parse_mode="HTML",\n        )\n''')

    text = read(rel)
    write_anchor = '    write_durable_request(root / "request.json", request)\n'
    write_new = '''    validated_request = generic_project_runtime.validate_request_payload(request)\n    write_durable_request(root / "request.json", validated_request)\n'''
    if write_anchor not in text:
        raise RuntimeError("wizard request write anchor missing")
    text = text.replace(write_anchor, write_new, 1)
    write(rel, text)

    package = ROOT / "handlers/dub_wizard/__init__.py"
    if not package.is_file():
        raise RuntimeError("wizard shadow package missing")
    package.unlink()
    print("flattened handlers.dub_wizard")


def flatten_health() -> None:
    rel = "handlers/dub_health.py"
    text = read(rel)
    import_anchor = "from services.dub_studio import DubStore, load_recipe, studio_root, worker_is_fresh\n"
    if import_anchor not in text:
        raise RuntimeError("health import anchor missing")
    text = text.replace(import_anchor, import_anchor + "from services.dub_worker_release import WORKER_RUNTIME\n", 1)
    text = text.replace('_WORKER_RUNTIME = "dub-worker-quality-v4.5"', '_WORKER_RUNTIME = WORKER_RUNTIME', 1)
    write(rel, text)
    package = ROOT / "handlers/dub_health/__init__.py"
    if not package.is_file():
        raise RuntimeError("health shadow package missing")
    package.unlink()
    print("flattened handlers.dub_health")


def main() -> int:
    flatten_audio_repair()
    flatten_wizard()
    flatten_health()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
