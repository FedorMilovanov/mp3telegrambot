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
        if rel.endswith("/__init__.py"):
            sibling = path.parent.with_suffix(".py")
            if sibling.is_file():
                continue
        if rel.endswith("/__main__.py"):
            sibling = path.parent.with_suffix(".py")
            if sibling.is_file():
                continue
        if rel not in result:
            result.append(rel)
    return tuple(result)


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
    source += f'''\n\n# Source-owned implementation sets. Package-shadow and deleted snapshot paths are\n# intentionally absent; fingerprints cover the files Python can actually execute.\n_RENDER_MODULES = {render!r}\n_RELEASE_MODULES = {release!r}\n\nfrom services.speech_backends import (\n    BACKEND_ENVIRONMENT_POLICY,\n    DEFAULT_BACKEND_ID,\n    BackendIdentity,\n    default_backend,\n    get_backend,\n    select_production_backend,\n)\n\nBACKEND_SELECTION_POLICY = "explicit-request-speech-backend-v1"\n_BACKEND = default_backend()\nif _BACKEND.backend_id != DEFAULT_BACKEND_ID:\n    raise RuntimeError("Default speech backend registry рассинхронизирован.")\n\n# Public model discovery follows the selected default backend without mutating any\n# imported module or temporarily rebinding globals.\ndiscover_model = _BACKEND.discover_model\n_base_normalize_settings = normalize_settings\n\n\ndef normalize_settings(\n    request: dict[str, Any],\n    *,\n    duration: Any,\n) -> dict[str, Any]:\n    settings = dict(_base_normalize_settings(request, duration=duration))\n    selection = select_production_backend(\n        request.get("speech_backend"),\n        default_backend_id=DEFAULT_BACKEND_ID,\n    )\n    backend = selection.backend\n    required = (\n        "build_renderer_command",\n        "build_master_command",\n        "process_environment",\n        "open_session",\n    )\n    missing = [name for name in required if not callable(getattr(backend, name, None))]\n    if missing:\n        raise RuntimeError(\n            "Speech backend не реализует model-independent production contract: "\n            + ", ".join(missing)\n        )\n    settings["speech_backend"] = selection.backend_id\n    settings["speech_backend_policy"] = BACKEND_SELECTION_POLICY\n    return settings\n\n\ndef _backend_model_manifest(backend: Any, archive: Path) -> dict[str, Any]:\n    model = Path(backend.discover_model(Path(archive))).resolve()\n    artifacts: list[dict[str, Any]] = []\n    for path in sorted(model.iterdir(), key=lambda item: item.name.casefold()):\n        if not path.is_file() or path.suffix.casefold() not in {{\n            ".json", ".safetensors", ".bin", ".pth"\n        }}:\n            continue\n        stat = path.stat()\n        item: dict[str, Any] = {{\n            "name": path.name,\n            "size": int(stat.st_size),\n            "mtime_ns": int(stat.st_mtime_ns),\n        }}\n        if path.suffix.casefold() == ".json":\n            item["sha256"] = sha256_file(path)\n            item["hash_mode"] = "full"\n        else:\n            item["sha256"] = sampled_sha256_file(path)\n            item["hash_mode"] = "sampled-begin-middle-end-v1"\n        artifacts.append(item)\n    if not artifacts:\n        raise RuntimeError(f"Model snapshot не содержит fingerprint-артефактов: {{model}}")\n    return {{"path": str(model), "artifacts": artifacts}}\n\n\ndef build_fingerprints(\n    *,\n    repo: Path,\n    archive: Path,\n    cpu_python: Path,\n    backend_id: object | None = None,\n) -> dict[str, Any]:\n    repo = Path(repo).resolve()\n    archive = Path(archive).resolve()\n    cpu_python = Path(cpu_python).resolve()\n    backend = get_backend(backend_id or DEFAULT_BACKEND_ID)\n    render_payload = {{\n        "policy": POLICY,\n        "backend_id": backend.backend_id,\n        "contract_module_sha256": sha256_file(Path(__file__).resolve()),\n        "implementation": _module_hashes(repo, _RENDER_MODULES),\n        "model": _backend_model_manifest(backend, archive),\n        "voxcpm_runtime": _voxcpm_runtime(cpu_python),\n    }}\n    release_payload = {{\n        "policy": POLICY,\n        "implementation": _module_hashes(repo, _RELEASE_MODULES),\n    }}\n    try:\n        identity_payload = backend.identity(archive).as_dict()\n    except RuntimeError:\n        model_path = str((render_payload.get("model") or {{}}).get("path") or "").strip()\n        if not model_path:\n            raise\n        identity_payload = BackendIdentity(\n            backend_id=backend.backend_id,\n            family="reference-conditioned-generative-tts",\n            adapter_policy=str(getattr(backend, "adapter_policy", "")),\n            model_path=model_path,\n            runtime_module=backend.backend_id,\n            parameter_schema=(),\n            output_contract="backend-model-manifest-v1",\n        ).as_dict()\n    backend_payload = {{\n        "identity": identity_payload,\n        "capabilities": backend.capabilities().as_dict(),\n        "selection_policy": BACKEND_SELECTION_POLICY,\n        "environment_policy": BACKEND_ENVIRONMENT_POLICY,\n        "backend_id": backend.backend_id,\n    }}\n    render_payload["speech_backend"] = backend_payload\n    return {{\n        "policy": POLICY,\n        "render_contract_sha256": _digest(render_payload),\n        "release_contract_sha256": _digest(release_payload),\n        "render": render_payload,\n        "release": release_payload,\n        "speech_backend": backend_payload,\n    }}\n\n\n__all__ = [\n    "BACKEND_SELECTION_POLICY",\n    "DEFAULT_BACKEND_ID",\n    "MAX_BASE_SEED",\n    "MAX_CFG",\n    "MAX_SEED",\n    "MAX_STEPS",\n    "MAX_THREADS",\n    "POLICY",\n    "RETRY_SEED_OFFSET",\n    "_RENDER_MODULES",\n    "_RELEASE_MODULES",\n    "build_fingerprints",\n    "discover_model",\n    "normalize_settings",\n    "sampled_sha256_file",\n    "sha256_file",\n]\n'''

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
        # Attribute assignments inside local objects are not categorically forbidden,
        # but this contract should not need any at all.
        raise RuntimeError("clean runtime contract contains attribute assignment")

    TARGET.write_text(source, encoding="utf-8")
    BASE.unlink()
    print(f"source-owned clean runtime contract: render={len(render)} release={len(release)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
