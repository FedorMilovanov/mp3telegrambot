#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict compatibility facade for the Dub production preflight.

The parallel agent's durable implementation remains in ``dub_job_preflight.py``.
This package shadows it for normal imports and strengthens only the trust
boundaries: canonical project/request identity, complete implementation-aware
cache signatures, deterministic child imports, and collision-safe report writes.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
from typing import Any
import uuid

from tools.voxcpm2 import clean_production_core
from tools.voxcpm2 import clean_runtime_contract
from tools.voxcpm2 import generic_project_runtime

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "dub_job_preflight.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._dub_job_preflight_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить Dub production preflight: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

POLICY = "dub-production-preflight-v2"
REPORT_SCHEMA = 2
_ACTIONS = {
    "render",
    "render_direct",
    "render_gemini",
    "render_custom",
    "repair_audio",
}
_MODULES = tuple(_legacy._MODULES)


def _sha256(path: Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(f"Preflight fingerprint file не найден: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise RuntimeError("Preflight report должен быть JSON-объектом.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        path.name + f".tmp.{os.getpid()}.{uuid.uuid4().hex}"
    )
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_report(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    try:
        schema = clean_production_core._strict_int(
            payload.get("schema_version"),
            field="production_preflight.schema_version",
            low=REPORT_SCHEMA,
            high=REPORT_SCHEMA,
        )
    except RuntimeError:
        return {}
    return payload if schema == REPORT_SCHEMA else {}


def _project_root(project: dict[str, Any]) -> Path:
    if not isinstance(project, dict):
        raise RuntimeError("Preflight project должен быть JSON-объектом.")
    project_id = str(project.get("id") or "").strip().lower()
    if not generic_project_runtime._legacy._PROJECT_RE.fullmatch(project_id):
        raise RuntimeError("Preflight: некорректный Dub Studio project ID.")

    allowed = (_legacy.studio_root() / "projects").resolve()
    expected = (allowed / project_id).resolve()
    raw = str(project.get("work_root") or "").strip()
    root = Path(raw).resolve() if raw else expected
    try:
        root.relative_to(allowed)
    except ValueError as exc:
        raise RuntimeError("Project root escaped Dub Studio projects directory.") from exc
    if os.path.normcase(str(root)) != os.path.normcase(str(expected)):
        raise RuntimeError(
            "Preflight: work_root проекта не совпадает с его canonical project ID."
        )
    return root


def _path_setting(request: dict[str, Any], key: str, default: str) -> Path:
    value = default if key not in request or request[key] is None else request[key]
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise RuntimeError(f"Preflight: request.{key} должен быть непустым путём.")
    return Path(value.strip()).expanduser().resolve()


def _runtime_paths(project: dict[str, Any]) -> dict[str, Path]:
    root = _project_root(project)
    request = generic_project_runtime.load_request(root)
    request_path = root / "request.json"
    venv = _path_setting(
        request,
        "cpu_venv",
        r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv",
    )
    archive = _path_setting(
        request,
        "vox_archive",
        r"C:\AI-Archive\VoxCPM2-paused-RTX3060",
    )
    cpu_python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    repo = _legacy.repo_root().resolve()
    example = repo / "tools" / "voxcpm2" / "examples" / "john_piper_z20py4yqhyq"
    return {
        "root": root,
        "request": request_path,
        "repo": repo,
        "cpu_python": cpu_python,
        "archive": archive,
        "renderer": example / "voxcpm2_cpu_shorts_production.py",
        "master": example / "master_constant_mix.py",
    }


def _executable_identity(name: str) -> dict[str, Any]:
    resolved = shutil.which(name)
    if not resolved:
        raise RuntimeError(f"Preflight: {name} не найден в PATH.")
    path = Path(resolved).resolve()
    stat = path.stat()
    return {
        "path": str(path),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _implementation_identity(repo: Path) -> dict[str, Any]:
    names = tuple(
        dict.fromkeys(
            (
                *clean_runtime_contract._RENDER_MODULES,
                *clean_runtime_contract._RELEASE_MODULES,
                "tools/voxcpm2/dub_job_preflight.py",
                "tools/voxcpm2/dub_job_preflight/__init__.py",
            )
        )
    )
    files = {name: _sha256(repo / name) for name in names}
    return {
        "files": files,
        "sha256": _payload_sha256(files),
    }


def _signature(paths: dict[str, Path], *, action: str) -> dict[str, Any]:
    repo = Path(paths["repo"]).resolve()
    cpu_python = Path(paths["cpu_python"]).resolve()
    if not cpu_python.is_file():
        raise RuntimeError(f"Preflight: CPU Python не найден: {cpu_python}")
    python_stat = cpu_python.stat()

    model = _legacy.discover_model(paths["archive"])
    config = model / "config.json"
    if not config.is_file():
        raise RuntimeError(f"Preflight: config.json модели не найден: {config}")
    return {
        "policy": POLICY,
        "action": action,
        "cpu_python": {
            "path": str(cpu_python),
            "size": int(python_stat.st_size),
            "mtime_ns": int(python_stat.st_mtime_ns),
        },
        "renderer": str(paths["renderer"]),
        "renderer_sha256": _sha256(paths["renderer"]),
        "master": str(paths["master"]),
        "master_sha256": _sha256(paths["master"]),
        "model": str(model),
        "model_config_sha256": _sha256(config),
        "implementation": _implementation_identity(repo),
        "ffmpeg": _executable_identity("ffmpeg"),
        "ffprobe": _executable_identity("ffprobe"),
        "modules": list(_MODULES),
    }


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _probe_imports(paths: dict[str, Path]) -> dict[str, Any]:
    python = Path(paths["cpu_python"]).resolve()
    repo = Path(paths["repo"]).resolve()
    for label in ("renderer", "master"):
        path = Path(paths[label]).resolve()
        if not path.is_file():
            raise RuntimeError(f"Preflight: {label} entrypoint не найден: {path}")

    script = (
        "import importlib, json, sys\n"
        f"names = {list(_MODULES)!r}\n"
        "loaded = {}\n"
        "for name in names:\n"
        "    module = importlib.import_module(name)\n"
        "    loaded[name] = str(getattr(module, '__file__', '') or '')\n"
        "print(json.dumps({'python': sys.executable, 'loaded': loaded}, "
        "ensure_ascii=False, sort_keys=True))\n"
    )
    env = clean_production_core._child_python_env(dict(os.environ))
    process = _legacy.subprocess.run(
        [str(python), "-c", script],
        cwd=str(repo),
        env=env,
        stdout=_legacy.subprocess.PIPE,
        stderr=_legacy.subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if process.returncode != 0:
        tail = (process.stderr or process.stdout or "")[-8000:]
        raise RuntimeError(
            f"Preflight: CPU runtime/import graph завершился с кодом "
            f"{process.returncode}:\n{tail}"
        )
    try:
        payload = json.loads((process.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Preflight: CPU runtime вернул некорректный JSON.") from exc
    loaded = payload.get("loaded") if isinstance(payload, dict) else None
    reported_python = str(payload.get("python") or "") if isinstance(payload, dict) else ""
    if not isinstance(loaded, dict) or set(loaded) != set(_MODULES):
        raise RuntimeError("Preflight: импортирован не полный набор production-модулей.")
    if os.path.normcase(str(Path(reported_python).resolve())) != os.path.normcase(str(python)):
        raise RuntimeError("Preflight: import probe запущен не тем CPU Python.")

    expected_files = {
        "tools.voxcpm2.examples.john_piper_z20py4yqhyq.master_constant_mix": Path(paths["master"]),
        "tools.voxcpm2.examples.john_piper_z20py4yqhyq.voxcpm2_cpu_shorts_production": Path(paths["renderer"]),
    }
    for name, raw_path in loaded.items():
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise RuntimeError(f"Preflight: module {name} не сообщил __file__.")
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise RuntimeError(f"Preflight: module {name} загружен из отсутствующего файла: {path}")
        if name.startswith("tools.") and not _inside(path, repo):
            raise RuntimeError(f"Preflight: project module {name} загружен вне repo: {path}")
        expected = expected_files.get(name)
        if expected is not None and os.path.normcase(str(path)) != os.path.normcase(
            str(expected.resolve())
        ):
            raise RuntimeError(f"Preflight: module {name} загружен не из production entrypoint.")
    final_qa = Path(loaded["tools.voxcpm2.final_media_qa"]).resolve()
    if final_qa.name != "__init__.py" or final_qa.parent.name != "final_media_qa":
        raise RuntimeError("Preflight: active final_media_qa compatibility package не загружен.")

    return {
        "python_returncode": int(process.returncode),
        "python": reported_python,
        "loaded_modules": loaded,
        "ffmpeg": str(_signature_executable_path("ffmpeg")),
        "ffprobe": str(_signature_executable_path("ffprobe")),
    }


def _signature_executable_path(name: str) -> Path:
    value = shutil.which(name)
    if not value:
        raise RuntimeError(f"Preflight: {name} не найден в PATH.")
    return Path(value).resolve()


def _cache_hit(
    current: dict[str, Any],
    *,
    project_id: str,
    action: str,
    signature: dict[str, Any],
) -> bool:
    return bool(
        current.get("passed") is True
        and current.get("skipped") is False
        and current.get("policy") == POLICY
        and current.get("project_id") == project_id
        and current.get("action") == action
        and current.get("signature") == signature
        and isinstance(current.get("probe"), dict)
    )


def run(project: dict[str, Any], action: str) -> dict[str, Any]:
    action = str(action or "").strip().lower()
    if action not in _ACTIONS or str((project or {}).get("recipe_id") or "") != "generic_short_v1":
        return {
            "schema_version": REPORT_SCHEMA,
            "policy": POLICY,
            "passed": True,
            "skipped": True,
            "action": action,
        }

    paths = _runtime_paths(project)
    project_id = str(project["id"]).strip().lower()
    report_path = paths["root"] / "output" / "production_preflight.json"
    signature = _signature(paths, action=action)
    current = _read_report(report_path)
    if _cache_hit(
        current,
        project_id=project_id,
        action=action,
        signature=signature,
    ):
        return current

    probe = _probe_imports(paths)
    report = {
        "schema_version": REPORT_SCHEMA,
        "policy": POLICY,
        "passed": True,
        "skipped": False,
        "project_id": project_id,
        "action": action,
        "signature": signature,
        "probe": probe,
    }
    _atomic_json(report_path, report)
    return report


# Patch the parallel agent's module as well, so any already-captured legacy
# function resolves the same strict helpers and policy.
_legacy.POLICY = POLICY
_legacy._ACTIONS = set(_ACTIONS)
_legacy._atomic_json = _atomic_json
_legacy._project_root = _project_root
_legacy._runtime_paths = _runtime_paths
_legacy._signature = lambda paths: _signature(paths, action="legacy")
_legacy._probe_imports = _probe_imports
_legacy.run = run

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {
        "POLICY",
        "REPORT_SCHEMA",
        "_atomic_json",
        "_cache_hit",
        "_probe_imports",
        "_project_root",
        "_read_report",
        "_runtime_paths",
        "_signature",
        "run",
    }
)
