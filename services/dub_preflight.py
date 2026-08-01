"""Backend-neutral preflight for speech, media master and final validation."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from services.dub_studio import repo_root, studio_root
from services.media_masters import get_media_master
from services.speech_backends import DEFAULT_BACKEND_ID, get_backend

POLICY = "backend-neutral-dub-production-preflight-v2"
_ACTIONS = {"render", "render_direct", "render_gemini", "repair_audio"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _project_root(project: dict[str, Any]) -> Path:
    raw = str(project.get("work_root") or "").strip()
    root = (
        Path(raw).resolve()
        if raw
        else (studio_root() / "projects" / str(project["id"])).resolve()
    )
    allowed = (studio_root() / "projects").resolve()
    try:
        root.relative_to(allowed)
    except ValueError as exc:
        raise RuntimeError("Project root escaped Dub Studio projects directory.") from exc
    return root


def _model_fingerprint(model_path: Path) -> dict[str, Any]:
    path = Path(model_path).resolve()
    if path.is_file():
        return {
            "path": str(path),
            "kind": "file",
            "sha256": _sha256(path),
        }
    if path.is_dir():
        config = path / "config.json"
        if config.is_file():
            return {
                "path": str(path),
                "kind": "directory-config",
                "config_sha256": _sha256(config),
            }
        stat = path.stat()
        return {
            "path": str(path),
            "kind": "directory",
            "mtime_ns": stat.st_mtime_ns,
        }
    raise RuntimeError(f"Preflight: backend model/archive path не найден: {path}")


def _runtime_plan(project: dict[str, Any]) -> dict[str, Any]:
    root = _project_root(project)
    request_path = root / "request.json"
    request = _read_json(request_path)
    if int(request.get("schema_version") or 0) != 1:
        raise RuntimeError(
            f"Preflight: request.json отсутствует или повреждён: {request_path}"
        )
    repo = repo_root().resolve()
    backend = get_backend(request.get("speech_backend") or DEFAULT_BACKEND_ID)
    speech_runtime = backend.runtime_paths(repo, request)
    media_master = get_media_master(request.get("media_master") or "constant-mix")
    media_runtime = media_master.runtime_paths(
        repo,
        request,
        fallback_python=speech_runtime.cpu_python,
    )
    identity = backend.identity(speech_runtime.archive_root)
    modules = tuple(
        dict.fromkeys(
            (
                *speech_runtime.import_modules,
                media_runtime.import_module,
                "services.media_masters",
            )
        )
    )
    return {
        "root": root,
        "request_path": request_path,
        "request": request,
        "repo": repo,
        "backend": backend,
        "identity": identity,
        "speech_runtime": speech_runtime,
        "media_master": media_master,
        "media_runtime": media_runtime,
        "modules": modules,
    }


def _signature(plan: dict[str, Any]) -> dict[str, Any]:
    speech = plan["speech_runtime"]
    media = plan["media_runtime"]
    for label, path in (
        ("speech renderer", speech.renderer_entrypoint),
        ("media master", media.entrypoint),
    ):
        if not Path(path).is_file():
            raise RuntimeError(f"Preflight: {label} entrypoint не найден: {path}")
    return {
        "policy": POLICY,
        "backend": plan["identity"].as_dict(),
        "speech_runtime": speech.as_dict(),
        "media_runtime": media.as_dict(),
        "speech_renderer_sha256": _sha256(Path(speech.renderer_entrypoint)),
        "media_master_sha256": _sha256(Path(media.entrypoint)),
        "model": _model_fingerprint(Path(plan["identity"].model_path)),
        "modules": list(plan["modules"]),
    }


def _probe_imports(plan: dict[str, Any]) -> dict[str, Any]:
    speech = plan["speech_runtime"]
    media = plan["media_runtime"]
    python = Path(speech.cpu_python)
    if not python.is_file():
        raise RuntimeError(f"Preflight: speech Python не найден: {python}")
    if not Path(media.python_executable).is_file():
        raise RuntimeError(
            f"Preflight: media master Python не найден: {media.python_executable}"
        )
    for executable in ("ffmpeg", "ffprobe"):
        if not shutil.which(executable):
            raise RuntimeError(f"Preflight: {executable} не найден в PATH.")

    modules = list(plan["modules"])
    script = (
        "import importlib, json\n"
        f"names = {modules!r}\n"
        "loaded = {}\n"
        "for name in names:\n"
        "    module = importlib.import_module(name)\n"
        "    loaded[name] = str(getattr(module, '__file__', '') or '')\n"
        "print(json.dumps(loaded, ensure_ascii=False, sort_keys=True))\n"
    )
    backend = plan["backend"]
    request = plan["request"]
    env = backend.process_environment(
        request,
        base_environment=os.environ,
    ).as_dict(os.environ)
    repo = str(plan["repo"])
    current = str(env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = repo + (os.pathsep + current if current else "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.run(
        [str(python), "-c", script],
        cwd=str(plan["repo"]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if process.returncode != 0:
        tail = (process.stderr or process.stdout or "")[-5000:]
        raise RuntimeError(
            "Preflight: backend runtime/import graph завершился с кодом "
            f"{process.returncode}:\n{tail}"
        )
    try:
        loaded = json.loads((process.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Preflight: backend runtime вернул некорректный JSON.") from exc
    if not isinstance(loaded, dict) or set(loaded) != set(modules):
        raise RuntimeError("Preflight: импортирован не полный production import graph.")
    return {
        "python_returncode": process.returncode,
        "loaded_modules": loaded,
        "ffmpeg": str(shutil.which("ffmpeg")),
        "ffprobe": str(shutil.which("ffprobe")),
    }


def run(project: dict[str, Any], action: str) -> dict[str, Any]:
    """Validate one expensive production job and return its durable report."""
    action = str(action or "").strip().lower()
    if action not in _ACTIONS or str(project.get("recipe_id") or "") != "generic_short_v1":
        return {"policy": POLICY, "passed": True, "skipped": True, "action": action}

    plan = _runtime_plan(project)
    report_path = plan["root"] / "output" / "production_preflight.json"
    signature = _signature(plan)
    current = _read_json(report_path)
    if current.get("passed") is True and current.get("signature") == signature:
        return current

    probe = _probe_imports(plan)
    report = {
        "schema_version": 2,
        "policy": POLICY,
        "passed": True,
        "skipped": False,
        "project_id": str(project["id"]),
        "action": action,
        "signature": signature,
        "probe": probe,
    }
    _atomic_json(report_path, report)
    return report


__all__ = [
    "POLICY",
    "_model_fingerprint",
    "_probe_imports",
    "_runtime_plan",
    "_signature",
    "run",
]
