#!/usr/bin/env python3
"""Collapse clean_runtime_contract base snapshot/installers into one source owner."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "voxcpm2" / "clean_runtime_contract.py"
BASE = ROOT / "tools" / "voxcpm2" / "_clean_runtime_contract_base.py"

EXTRA_RENDER = (
    "tools/voxcpm2/direct_failure_recovery.py",
    "tools/voxcpm2/direct_final_audit_v3.py",
    "tools/voxcpm2/direct_surgical_guard.py",
    "tools/voxcpm2/direct_surgical_io.py",
    "tools/voxcpm2/direct_surgical_runtime.py",
    "tools/voxcpm2/direct_surgical_polish_v2.py",
    "services/speech_backends/audited_voxcpm2.py",
    "services/speech_backends/base.py",
    "services/speech_backends/control_plane.py",
    "services/speech_backends/execution_plan.py",
    "services/speech_backends/model_profiles.py",
    "services/speech_backends/registry.py",
)


def literal_tuple(text: str, name: str) -> tuple[str, ...]:
    tree = ast.parse(text)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except Exception:
                continue
            if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
                return value
    return ()


def canonical_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for rel in paths:
        path = ROOT / rel
        if not path.is_file():
            continue
        if rel.endswith(("/__init__.py", "/__main__.py")):
            sibling = path.parent.with_suffix(".py")
            if sibling.is_file():
                continue
        if rel not in result:
            result.append(rel)
    return tuple(result)


def replace_assignment(text: str, name: str, value: object) -> str:
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            lines[node.lineno - 1 : (node.end_lineno or node.lineno)] = [f"{name} = {value!r}\n"]
            return "".join(lines)
    raise RuntimeError(f"base assignment not found: {name}")


def strip_final_all(text: str) -> str:
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    for node in reversed(tree.body):
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            del lines[start : (node.end_lineno or node.lineno)]
            return "".join(lines).rstrip() + "\n"
    raise RuntimeError("base __all__ assignment not found")


def main() -> int:
    if not TARGET.is_file() or not BASE.is_file():
        raise RuntimeError("clean runtime contract/base snapshot missing")
    current = TARGET.read_text(encoding="utf-8")
    base = BASE.read_text(encoding="utf-8")

    render = canonical_paths(
        literal_tuple(base, "_RENDER_MODULES")
        + EXTRA_RENDER
        + literal_tuple(current, "_FACADE_RENDER_MODULES")
    )
    retired = set(literal_tuple(current, "_RETIRED_RELEASE_MODULES"))
    release = canonical_paths(
        tuple(item for item in literal_tuple(base, "_RELEASE_MODULES") if item not in retired)
        + literal_tuple(current, "_FACADE_RELEASE_MODULES")
    )
    if not render or not release:
        raise RuntimeError("canonical fingerprint module sets are empty")

    source = strip_final_all(base)
    source = replace_assignment(source, "_RENDER_MODULES", render)
    source = replace_assignment(source, "_RELEASE_MODULES", release)
    source += '''

from services.speech_backends import (
    BACKEND_ENVIRONMENT_POLICY,
    DEFAULT_BACKEND_ID,
    BackendIdentity,
    default_backend,
    get_backend,
    select_production_backend,
)

BACKEND_SELECTION_POLICY = "explicit-request-speech-backend-v1"
_BACKEND = default_backend()
if _BACKEND.backend_id != DEFAULT_BACKEND_ID:
    raise RuntimeError("Default speech backend registry рассинхронизирован.")

# Public model discovery follows the default backend through a normal local binding.
discover_model = _BACKEND.discover_model
_base_normalize_settings = normalize_settings


def normalize_settings(
    request: dict[str, Any],
    *,
    duration: Any,
) -> dict[str, Any]:
    settings = dict(_base_normalize_settings(request, duration=duration))
    selection = select_production_backend(
        request.get("speech_backend"),
        default_backend_id=DEFAULT_BACKEND_ID,
    )
    backend = selection.backend
    required = (
        "build_renderer_command",
        "build_master_command",
        "process_environment",
        "open_session",
    )
    missing = [name for name in required if not callable(getattr(backend, name, None))]
    if missing:
        raise RuntimeError(
            "Speech backend не реализует model-independent production contract: "
            + ", ".join(missing)
        )
    settings["speech_backend"] = selection.backend_id
    settings["speech_backend_policy"] = BACKEND_SELECTION_POLICY
    return settings


def _backend_model_manifest(backend: Any, archive: Path) -> dict[str, Any]:
    model = Path(backend.discover_model(Path(archive))).resolve()
    artifacts: list[dict[str, Any]] = []
    for path in sorted(model.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.casefold() not in {
            ".json", ".safetensors", ".bin", ".pth"
        }:
            continue
        stat = path.stat()
        item: dict[str, Any] = {
            "name": path.name,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
        if path.suffix.casefold() == ".json":
            item["sha256"] = sha256_file(path)
            item["hash_mode"] = "full"
        else:
            item["sha256"] = sampled_sha256_file(path)
            item["hash_mode"] = "sampled-begin-middle-end-v1"
        artifacts.append(item)
    if not artifacts:
        raise RuntimeError(f"Model snapshot не содержит fingerprint-артефактов: {model}")
    return {"path": str(model), "artifacts": artifacts}


def build_fingerprints(
    *,
    repo: Path,
    archive: Path,
    cpu_python: Path,
    backend_id: object | None = None,
) -> dict[str, Any]:
    repo = Path(repo).resolve()
    archive = Path(archive).resolve()
    cpu_python = Path(cpu_python).resolve()
    backend = get_backend(backend_id or DEFAULT_BACKEND_ID)
    render_payload = {
        "policy": POLICY,
        "backend_id": backend.backend_id,
        "contract_module_sha256": sha256_file(Path(__file__).resolve()),
        "implementation": _module_hashes(repo, _RENDER_MODULES),
        "model": _backend_model_manifest(backend, archive),
        "voxcpm_runtime": _voxcpm_runtime(cpu_python),
    }
    release_payload = {
        "policy": POLICY,
        "implementation": _module_hashes(repo, _RELEASE_MODULES),
    }
    try:
        identity_payload = backend.identity(archive).as_dict()
    except RuntimeError:
        model_path = str((render_payload.get("model") or {}).get("path") or "").strip()
        if not model_path:
            raise
        identity_payload = BackendIdentity(
            backend_id=backend.backend_id,
            family="reference-conditioned-generative-tts",
            adapter_policy=str(getattr(backend, "adapter_policy", "")),
            model_path=model_path,
            runtime_module=backend.backend_id,
            parameter_schema=(),
            output_contract="backend-model-manifest-v1",
        ).as_dict()
    backend_payload = {
        "identity": identity_payload,
        "capabilities": backend.capabilities().as_dict(),
        "selection_policy": BACKEND_SELECTION_POLICY,
        "environment_policy": BACKEND_ENVIRONMENT_POLICY,
        "backend_id": backend.backend_id,
    }
    render_payload["speech_backend"] = backend_payload
    return {
        "policy": POLICY,
        "render_contract_sha256": _digest(render_payload),
        "release_contract_sha256": _digest(release_payload),
        "render": render_payload,
        "release": release_payload,
        "speech_backend": backend_payload,
    }


__all__ = [
    "BACKEND_SELECTION_POLICY",
    "DEFAULT_BACKEND_ID",
    "MAX_BASE_SEED",
    "MAX_CFG",
    "MAX_SEED",
    "MAX_STEPS",
    "MAX_THREADS",
    "POLICY",
    "RETRY_SEED_OFFSET",
    "_RENDER_MODULES",
    "_RELEASE_MODULES",
    "build_fingerprints",
    "discover_model",
    "normalize_settings",
    "sampled_sha256_file",
    "sha256_file",
]
'''

    forbidden = (
        "exec(compile(",
        "_clean_runtime_contract_base",
        "install_runtime_fingerprint",
        "globals()[",
        "spec_from_file_location",
        "module_from_spec",
    )
    bad = [token for token in forbidden if token in source]
    if bad:
        raise RuntimeError(f"clean runtime surgery survived: {bad}")
    parsed = ast.parse(source, filename=str(TARGET))
    if any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Attribute) for target in node.targets)
        for node in ast.walk(parsed)
    ):
        raise RuntimeError("clean runtime contract contains attribute assignment")

    # Final contract may include legitimate package __init__.py files, but never a
    # package-shadow path whose same import name is already owned by sibling .py.
    for rel in (*render, *release):
        path = ROOT / rel
        if not path.is_file():
            raise RuntimeError(f"fingerprint path is missing: {rel}")
        if rel.endswith(("/__init__.py", "/__main__.py")) and path.parent.with_suffix(".py").is_file():
            raise RuntimeError(f"fingerprint still points at shadow package: {rel}")

    TARGET.write_text(source, encoding="utf-8")
    BASE.unlink()
    print(f"source-owned clean runtime contract: render={len(render)} release={len(release)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
