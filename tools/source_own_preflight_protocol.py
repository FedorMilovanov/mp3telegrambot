#!/usr/bin/env python3
"""Move backend-aware marked preflight transport into the canonical owner."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "voxcpm2" / "dub_job_preflight.py"


def replace_function(text: str, name: str, source: str) -> str:
    tree = ast.parse(text, filename=str(TARGET))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            end = node.end_lineno or node.lineno
            lines[start:end] = [source.rstrip() + "\n"]
            return "".join(lines)
    raise RuntimeError(f"function not found: {name}")


DECODE = r'''
def _decode_probe_payload(stdout: str) -> tuple[dict[str, Any], str]:
    lines = str(stdout or "").splitlines()
    marker_index: int | None = None
    payload: dict[str, Any] | None = None
    parse_error = ""
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index].strip().lstrip("\ufeff")
        if not line.startswith(PREFLIGHT_JSON_MARKER):
            continue
        marker_index = index
        raw = line[len(PREFLIGHT_JSON_MARKER):].strip()
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
        line for index, line in enumerate(lines)
        if index != marker_index and line.strip()
    ]
    noise = "\n".join(noise_lines)[-PREFLIGHT_MAX_DIAGNOSTIC_CHARS:]
    if payload is None:
        detail = f" Последняя ошибка: {parse_error}." if parse_error else ""
        if noise:
            detail += " stdout tail:\n" + noise
        raise RuntimeError(
            "Preflight: CPU runtime не вернул маркированный JSON." + detail
        )
    return payload, noise
'''

RUNTIME_PATHS = r'''
def _runtime_paths(project: dict[str, Any]) -> dict[str, Any]:
    root = _project_root(project)
    request = generic_project_runtime.load_request(root)
    repo = repo_root().resolve()
    backend = get_backend(request.get("speech_backend") or DEFAULT_BACKEND_ID)
    required = (
        "build_renderer_command",
        "build_master_command",
        "process_environment",
        "open_session",
    )
    missing_methods = [name for name in required if not callable(getattr(backend, name, None))]
    if missing_methods:
        raise RuntimeError(
            "Preflight: выбранный speech backend не реализует production contract: "
            + ", ".join(missing_methods)
        )
    missing = backend.capabilities().missing()
    if missing:
        raise RuntimeError(
            f"Preflight: backend {backend.backend_id} не проходит production capability gate: "
            f"{', '.join(missing)}."
        )
    runtime = backend.runtime_paths(repo, request)
    environment_policy = backend.process_environment(
        request,
        base_environment=os.environ,
    )
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
        "runtime_path_policy": PREFLIGHT_RUNTIME_PATH_POLICY,
        "environment_policy": environment_policy.as_metadata()["environment_policy"],
        "environment_metadata": environment_policy.as_metadata(),
    }
'''

PROBE = r'''
def _probe_imports(paths: dict[str, Any]) -> dict[str, Any]:
    python = Path(paths["cpu_python"]).resolve()
    repo = Path(paths["repo"]).resolve()
    modules = tuple(paths.get("import_modules") or _MODULES)
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
        f"print({PREFLIGHT_JSON_MARKER!r} + json.dumps({{'python': sys.executable, 'loaded': loaded}}, "
        "ensure_ascii=False, sort_keys=True, separators=(',', ':')), flush=True)\n"
    )
    env = clean_production_core._child_python_env(dict(os.environ))
    process = subprocess.run(
        [str(python), "-c", script],
        cwd=str(repo),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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

    payload, stdout_noise = _decode_probe_payload(process.stdout or "")
    loaded = payload.get("loaded") if isinstance(payload, dict) else None
    reported_python = str(payload.get("python") or "") if isinstance(payload, dict) else ""
    if not isinstance(loaded, dict) or set(loaded) != set(modules):
        raise RuntimeError("Preflight: импортирован не полный набор production-модулей.")
    if _normalized_path(Path(reported_python)) != _normalized_path(python):
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
            raise RuntimeError(f"Preflight: module {name} загружен из отсутствующего файла: {path}")
        if name.startswith("tools.") and not _inside(path, repo):
            raise RuntimeError(f"Preflight: project module {name} загружен вне repo: {path}")
        expected = expected_files.get(name)
        if expected is not None and _normalized_path(path) != _normalized_path(expected):
            raise RuntimeError(f"Preflight: module {name} загружен не из production entrypoint.")

    if not final_qa_module or final_qa_module not in loaded:
        raise RuntimeError("Preflight: backend не объявил active final QA module.")
    final_qa = Path(loaded[final_qa_module]).resolve()
    if final_qa_module.startswith("tools."):
        expected_final_qa = repo / (final_qa_module.replace(".", "/") + ".py")
        if not expected_final_qa.is_file():
            raise RuntimeError(
                f"Preflight: canonical final QA source owner не найден: {expected_final_qa}"
            )
        if _normalized_path(final_qa) != _normalized_path(expected_final_qa):
            raise RuntimeError("Preflight: final QA загружен не из canonical source owner.")

    return {
        "python_returncode": int(process.returncode),
        "python": reported_python,
        "speech_backend": str(paths.get("speech_backend") or ""),
        "runtime_path_policy": str(paths.get("runtime_path_policy") or ""),
        "environment_policy": str(paths.get("environment_policy") or ""),
        "environment_metadata": paths.get("environment_metadata") or {},
        "loaded_modules": loaded,
        "ffmpeg": str(_signature_executable_path("ffmpeg")),
        "ffprobe": str(_signature_executable_path("ffprobe")),
        "json_transport_policy": PREFLIGHT_JSON_TRANSPORT_POLICY,
        "stdout_noise_detected": bool(stdout_noise),
        "stdout_noise_tail": stdout_noise,
    }
'''


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    if "from services.speech_backends import DEFAULT_BACKEND_ID, get_backend" not in text:
        anchor = "from services.dub_worker_release import WORKER_RUNTIME\n"
        if anchor not in text:
            raise RuntimeError("dub_job_preflight release import anchor missing")
        text = text.replace(
            anchor,
            anchor + "from services.speech_backends import DEFAULT_BACKEND_ID, get_backend\n",
            1,
        )

    constants_anchor = 'PREFLIGHT_HEARTBEAT_SECONDS = 5.0\n'
    constants = (
        'PREFLIGHT_JSON_TRANSPORT_POLICY = "marked-preflight-json-transport-v2"\n'
        'PREFLIGHT_RUNTIME_PATH_POLICY = "backend-owned-preflight-runtime-paths-v1"\n'
        'PREFLIGHT_JSON_MARKER = "__DUB_PREFLIGHT_JSON_V1__="\n'
        'PREFLIGHT_MAX_DIAGNOSTIC_CHARS = 4000\n'
    )
    if "PREFLIGHT_JSON_TRANSPORT_POLICY" not in text:
        if constants_anchor not in text:
            raise RuntimeError("preflight constants anchor missing")
        text = text.replace(constants_anchor, constants_anchor + constants, 1)

    text = replace_function(text, "_runtime_paths", RUNTIME_PATHS)
    text = replace_function(text, "_probe_imports", PROBE)
    if "def _decode_probe_payload" not in text:
        anchor = "\n\ndef _probe_imports"
        if anchor not in text:
            raise RuntimeError("probe anchor missing")
        text = text.replace(anchor, DECODE.rstrip() + anchor, 1)

    text = text.replace('"modules": list(_MODULES),', '"modules": list(paths.get("import_modules") or _MODULES),')
    for name in (
        "PREFLIGHT_JSON_TRANSPORT_POLICY",
        "PREFLIGHT_RUNTIME_PATH_POLICY",
        "PREFLIGHT_JSON_MARKER",
        "_decode_probe_payload",
    ):
        if f'"{name}"' not in text:
            text = text.replace('__all__ = sorted({\n', f'__all__ = sorted({{\n    "{name}",\n', 1)

    forbidden = (
        "preflight._legacy",
        "_legacy.",
        "sys.modules",
        "spec_from_file_location",
        "module_from_spec",
    )
    bad = [token for token in forbidden if token in text]
    if bad:
        raise RuntimeError(f"preflight protocol source ownership failed: {bad}")
    ast.parse(text, filename=str(TARGET))
    TARGET.write_text(text, encoding="utf-8")
    print("source-owned backend-aware marked preflight protocol")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
