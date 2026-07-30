#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail fast on structural Dub runtime errors before expensive synthesis.

The production runner uses a separate CPU virtualenv and executes renderer/master
entrypoints as child processes. This preflight validates that exact interpreter,
package import graph, model snapshot and FFmpeg tools before a job can spend time
on VoxCPM synthesis. A fingerprinted passing report is reused until one of those
inputs changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from services.dub_studio import repo_root, studio_root
from tools.voxcpm2.direct_max_quality_io import discover_model

POLICY = "dub-production-preflight-v1"
_ACTIONS = {"render", "render_direct", "render_gemini", "repair_audio"}
_MODULES = (
    "tools.voxcpm2.final_media_qa",
    "tools.voxcpm2.examples.john_piper_z20py4yqhyq.master_constant_mix",
    "tools.voxcpm2.examples.john_piper_z20py4yqhyq.voxcpm2_cpu_shorts_production",
    "voxcpm",
    "torch",
    "soundfile",
)


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
    if raw:
        root = Path(raw).resolve()
    else:
        root = (studio_root() / "projects" / str(project["id"])).resolve()
    allowed = (studio_root() / "projects").resolve()
    try:
        root.relative_to(allowed)
    except ValueError as exc:
        raise RuntimeError("Project root escaped Dub Studio projects directory.") from exc
    return root


def _runtime_paths(project: dict[str, Any]) -> dict[str, Path]:
    root = _project_root(project)
    request_path = root / "request.json"
    request = _read_json(request_path)
    if int(request.get("schema_version") or 0) != 1:
        raise RuntimeError(f"Preflight: request.json отсутствует или повреждён: {request_path}")

    venv = Path(
        str(request.get("cpu_venv") or r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv")
    ).resolve()
    cpu_python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    archive = Path(
        str(request.get("vox_archive") or r"C:\AI-Archive\VoxCPM2-paused-RTX3060")
    ).resolve()
    repo = repo_root().resolve()
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


def _signature(paths: dict[str, Path]) -> dict[str, Any]:
    model = discover_model(paths["archive"])
    config = model / "config.json"
    if not config.is_file():
        raise RuntimeError(f"Preflight: config.json модели не найден: {config}")
    return {
        "policy": POLICY,
        "cpu_python": str(paths["cpu_python"]),
        "renderer": str(paths["renderer"]),
        "renderer_sha256": _sha256(paths["renderer"]),
        "master": str(paths["master"]),
        "master_sha256": _sha256(paths["master"]),
        "model": str(model),
        "model_config_sha256": _sha256(config),
        "modules": list(_MODULES),
    }


def _probe_imports(paths: dict[str, Path]) -> dict[str, Any]:
    python = paths["cpu_python"]
    if not python.is_file():
        raise RuntimeError(f"Preflight: CPU Python не найден: {python}")
    for label in ("renderer", "master"):
        path = paths[label]
        if not path.is_file():
            raise RuntimeError(f"Preflight: {label} entrypoint не найден: {path}")
    for executable in ("ffmpeg", "ffprobe"):
        if not shutil.which(executable):
            raise RuntimeError(f"Preflight: {executable} не найден в PATH.")

    script = (
        "import importlib, json\n"
        f"names = {list(_MODULES)!r}\n"
        "loaded = {}\n"
        "for name in names:\n"
        "    module = importlib.import_module(name)\n"
        "    loaded[name] = str(getattr(module, '__file__', '') or '')\n"
        "print(json.dumps(loaded, ensure_ascii=False, sort_keys=True))\n"
    )
    env = dict(os.environ)
    repo = str(paths["repo"])
    current = str(env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = repo + (os.pathsep + current if current else "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.run(
        [str(python), "-c", script],
        cwd=str(paths["repo"]),
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
            f"Preflight: CPU runtime/import graph завершился с кодом {process.returncode}:\n{tail}"
        )
    try:
        loaded = json.loads((process.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Preflight: CPU runtime вернул некорректный JSON.") from exc
    if not isinstance(loaded, dict) or set(loaded) != set(_MODULES):
        raise RuntimeError("Preflight: импортирован не полный набор production-модулей.")
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

    paths = _runtime_paths(project)
    report_path = paths["root"] / "output" / "production_preflight.json"
    signature = _signature(paths)
    current = _read_json(report_path)
    if current.get("passed") is True and current.get("signature") == signature:
        return current

    probe = _probe_imports(paths)
    report = {
        "schema_version": 1,
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


__all__ = ["POLICY", "run"]
