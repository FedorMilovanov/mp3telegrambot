#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backend-aware, noise-tolerant JSON transport for Dub CPU import preflight.

Some third-party runtime modules print banners or warnings to stdout during
import. Preflight must not confuse that diagnostic noise with its machine
payload, but it must still validate the exact CPU interpreter, complete module
set and production entrypoint paths. Runtime paths and import probes are supplied
by the selected speech backend rather than hardcoded in shared orchestration.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from services.speech_backends import DEFAULT_BACKEND_ID, get_backend

POLICY = "marked-preflight-json-transport-v2"
RUNTIME_PATH_POLICY = "backend-owned-preflight-runtime-paths-v1"
MARKER = "__DUB_PREFLIGHT_JSON_V1__="
MAX_DIAGNOSTIC_CHARS = 4000


def encode_payload(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        raise RuntimeError("Preflight probe payload должен быть JSON-объектом.")
    return MARKER + json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def decode_payload(stdout: str) -> tuple[dict[str, Any], str]:
    """Return the last marked object and bounded non-protocol stdout diagnostics."""
    lines = str(stdout or "").splitlines()
    marker_index: int | None = None
    payload: dict[str, Any] | None = None
    parse_error = ""

    for index in range(len(lines) - 1, -1, -1):
        line = lines[index].strip().lstrip("\ufeff")
        if not line.startswith(MARKER):
            continue
        marker_index = index
        raw = line[len(MARKER) :].strip()
        try:
            candidate = json.loads(raw)
        except json.JSONDecodeError as exc:
            parse_error = f"{type(exc).__name__}: {exc}"
            continue
        if not isinstance(candidate, dict):
            parse_error = "marked payload не является JSON-объектом"
            continue
        payload = candidate
        break

    noise_lines = [
        line
        for index, line in enumerate(lines)
        if index != marker_index and line.strip()
    ]
    noise = "\n".join(noise_lines)[-MAX_DIAGNOSTIC_CHARS:]
    if payload is None:
        detail = f" Последняя ошибка: {parse_error}." if parse_error else ""
        if noise:
            detail += " stdout tail:\n" + noise
        raise RuntimeError(
            "Preflight: CPU runtime не вернул маркированный JSON." + detail
        )
    return payload, noise


def runtime_paths(project: dict[str, Any]) -> dict[str, Any]:
    """Resolve exact preflight paths through the selected backend adapter."""
    from tools.voxcpm2 import dub_job_preflight as preflight

    root = preflight._project_root(project)
    request = preflight.generic_project_runtime.load_request(root)
    repo = preflight._legacy.repo_root().resolve()
    backend = get_backend(request.get("speech_backend") or DEFAULT_BACKEND_ID)
    if (
        not callable(getattr(backend, "build_renderer_command", None))
        or not callable(getattr(backend, "build_master_command", None))
        or not callable(getattr(backend, "process_environment", None))
    ):
        raise RuntimeError(
            "Preflight: выбранный speech backend не реализует process/command contract: "
            f"{backend.backend_id}"
        )
    runtime = backend.runtime_paths(repo, request)
    environment_policy = backend.process_environment(
        request,
        base_environment=os.environ,
    )

    # Existing strict preflight signatures resolve this global at call time.
    # Jobs are processed serially by one worker, so the active backend module set
    # can be made explicit without weakening any validation.
    preflight._MODULES = tuple(runtime.import_modules)
    legacy = getattr(preflight, "_legacy", None)
    if legacy is not None:
        legacy._MODULES = tuple(runtime.import_modules)

    return {
        "root": root,
        "request": root / "request.json",
        "repo": runtime.repo_root,
        "cpu_python": runtime.cpu_python,
        "archive": runtime.archive_root,
        "renderer": runtime.renderer_entrypoint,
        "master": runtime.master_entrypoint,
        "speech_backend": runtime.backend_id,
        "import_modules": tuple(runtime.import_modules),
        "renderer_module": runtime.renderer_module,
        "master_module": runtime.master_module,
        "final_qa_module": runtime.final_qa_module,
        "runtime_path_policy": RUNTIME_PATH_POLICY,
        "environment_policy": environment_policy.as_metadata()["environment_policy"],
        "environment_metadata": environment_policy.as_metadata(),
    }


def probe_imports(paths: dict[str, Any]) -> dict[str, Any]:
    """Run the active strict preflight import probe through the marked protocol."""
    from tools.voxcpm2 import dub_job_preflight as preflight

    python = Path(paths["cpu_python"]).resolve()
    repo = Path(paths["repo"]).resolve()
    modules = tuple(paths.get("import_modules") or preflight._MODULES)
    for label in ("renderer", "master"):
        path = Path(paths[label]).resolve()
        if not path.is_file():
            raise RuntimeError(f"Preflight: {label} entrypoint не найден: {path}")

    script = (
        "import importlib, json, sys\n"
        f"names = {list(modules)!r}\n"
        "loaded = {}\n"
        "for name in names:\n"
        "    module = importlib.import_module(name)\n"
        "    loaded[name] = str(getattr(module, '__file__', '') or '')\n"
        f"print({MARKER!r} + json.dumps({{'python': sys.executable, 'loaded': loaded}}, "
        "ensure_ascii=False, sort_keys=True, separators=(',', ':')), flush=True)\n"
    )
    env = preflight.clean_production_core._child_python_env(dict(os.environ))
    process = preflight._legacy.subprocess.run(
        [str(python), "-c", script],
        cwd=str(repo),
        env=env,
        stdout=preflight._legacy.subprocess.PIPE,
        stderr=preflight._legacy.subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if int(process.returncode) != 0:
        tail = (process.stderr or process.stdout or "")[-8000:]
        raise RuntimeError(
            "Preflight: CPU runtime/import graph завершился с кодом "
            f"{process.returncode}:\n{tail}"
        )

    payload, stdout_noise = decode_payload(process.stdout or "")
    loaded = payload.get("loaded") if isinstance(payload, dict) else None
    reported_python = str(payload.get("python") or "") if isinstance(payload, dict) else ""
    if not isinstance(loaded, dict) or set(loaded) != set(modules):
        raise RuntimeError("Preflight: импортирован не полный набор production-модулей.")
    if preflight._normalized_path(Path(reported_python)) != preflight._normalized_path(python):
        raise RuntimeError("Preflight: import probe запущен не тем CPU Python.")

    renderer_module = str(paths.get("renderer_module") or "")
    master_module = str(paths.get("master_module") or "")
    final_qa_module = str(paths.get("final_qa_module") or "")
    expected_files = {
        master_module: Path(paths["master"]),
        renderer_module: Path(paths["renderer"]),
    }
    for name, raw_path in loaded.items():
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise RuntimeError(f"Preflight: module {name} не сообщил __file__.")
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise RuntimeError(
                f"Preflight: module {name} загружен из отсутствующего файла: {path}"
            )
        if name.startswith("tools.") and not preflight._inside(path, repo):
            raise RuntimeError(
                f"Preflight: project module {name} загружен вне repo: {path}"
            )
        expected = expected_files.get(name)
        if (
            expected is not None
            and preflight._normalized_path(path)
            != preflight._normalized_path(expected)
        ):
            raise RuntimeError(
                f"Preflight: module {name} загружен не из production entrypoint."
            )

    final_qa = Path(loaded[final_qa_module]).resolve()
    if final_qa.name != "__init__.py" or final_qa.parent.name != "final_media_qa":
        raise RuntimeError(
            "Preflight: active final_media_qa compatibility package не загружен."
        )

    return {
        "python_returncode": int(process.returncode),
        "python": reported_python,
        "speech_backend": str(paths.get("speech_backend") or ""),
        "runtime_path_policy": str(paths.get("runtime_path_policy") or ""),
        "environment_policy": str(paths.get("environment_policy") or ""),
        "environment_metadata": paths.get("environment_metadata") or {},
        "loaded_modules": loaded,
        "ffmpeg": str(preflight._signature_executable_path("ffmpeg")),
        "ffprobe": str(preflight._signature_executable_path("ffprobe")),
        "json_transport_policy": POLICY,
        "stdout_noise_detected": bool(stdout_noise),
        "stdout_noise_tail": stdout_noise,
    }


def install() -> None:
    """Install backend path routing and marked transport before production jobs."""
    from tools.voxcpm2 import dub_job_preflight as preflight

    preflight._runtime_paths = runtime_paths
    preflight._probe_imports = probe_imports
    legacy = getattr(preflight, "_legacy", None)
    if legacy is not None:
        legacy._runtime_paths = runtime_paths
        legacy._probe_imports = probe_imports


__all__ = [
    "MARKER",
    "MAX_DIAGNOSTIC_CHARS",
    "POLICY",
    "RUNTIME_PATH_POLICY",
    "decode_payload",
    "encode_payload",
    "install",
    "probe_imports",
    "runtime_paths",
]
