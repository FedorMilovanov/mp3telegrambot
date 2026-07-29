#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed runtime contract and implementation fingerprints for Dub Studio."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

from tools.voxcpm2.direct_max_quality_io import discover_model

POLICY = "clean-runtime-contract-v2"
MAX_THREADS = 64
MAX_STEPS = 64
MAX_CFG = 10.0
MAX_SEED = 2**63 - 1
RETRY_SEED_OFFSET = 100_000
MAX_BASE_SEED = MAX_SEED - RETRY_SEED_OFFSET
_WEIGHT_SAMPLE_BYTES = 1024 * 1024

_RENDER_MODULES = (
    "tools/voxcpm2/clean_source_download.py",
    "tools/voxcpm2/clean_segment_normalizer.py",
    "tools/voxcpm2/clean_production_core.py",
    "tools/voxcpm2/continuous_reference_policy.py",
    "tools/voxcpm2/controlled_reference_gate.py",
    "tools/voxcpm2/expressive_continuity.py",
    "tools/voxcpm2/expressive_translation.py",
    "tools/voxcpm2/strict_translation_payload.py",
    "tools/voxcpm2/generic_short_runtime.py",
    "tools/voxcpm2/generic_project_runtime.py",
    "tools/voxcpm2/generic_direct_runtime.py",
    "tools/voxcpm2/generic_gemini_runtime.py",
    "tools/voxcpm2/generic_clean_gemini_runtime.py",
    "tools/voxcpm2/generic_clean_direct_runtime.py",
    "tools/voxcpm2/generic_clean_custom_runtime.py",
    "tools/voxcpm2/generic_clean_audio_repair_runtime.py",
    "tools/voxcpm2/direct_max_quality_io.py",
    "tools/voxcpm2/direct_timbre_analysis.py",
    "tools/voxcpm2/direct_max_quality_analysis.py",
    "tools/voxcpm2/direct_source_prosody.py",
    "tools/voxcpm2/direct_max_quality_render.py",
    "tools/voxcpm2/direct_max_quality_cli.py",
    "tools/voxcpm2/examples/john_piper_z20py4yqhyq/voxcpm2_cpu_shorts_production.py",
)
_RELEASE_MODULES = (
    "tools/voxcpm2/professional_audio_qa_v45.py",
    "tools/voxcpm2/professional_audio_v45.py",
    "tools/voxcpm2/semantic_tts_guard.py",
    "tools/voxcpm2/semantic_tts_guard_v4.py",
    "tools/voxcpm2/russian_spoken_numbers.py",
    "tools/voxcpm2/final_media_qa.py",
    "tools/voxcpm2/examples/john_piper_z20py4yqhyq/master_constant_mix.py",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sampled_sha256_file(path: Path, *, block_size: int = _WEIGHT_SAMPLE_BYTES) -> str:
    """Hash deterministic beginning/middle/end blocks without rereading huge weights."""
    size = int(path.stat().st_size)
    block = max(4096, int(block_size))
    if size <= block * 3:
        return sha256_file(path)
    positions = (0, max(0, (size - block) // 2), max(0, size - block))
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    with path.open("rb") as handle:
        for position in positions:
            handle.seek(position)
            chunk = handle.read(block)
            digest.update(str(position).encode("ascii"))
            digest.update(len(chunk).to_bytes(8, "big"))
            digest.update(chunk)
    return digest.hexdigest()


def _finite(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"{field} должен быть конечным числом.")
    return result


def _bounded_int(value: Any, *, field: str, low: int, high: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректное значение {field}: {value!r}") from exc
    if not low <= result <= high:
        raise RuntimeError(f"{field}={result} вне диапазона {low}..{high}.")
    return result


def _setting(request: dict[str, Any], key: str, default: Any) -> Any:
    """Use a default only when a setting is absent or explicitly null."""
    return default if key not in request or request[key] is None else request[key]


def normalize_settings(request: dict[str, Any], *, duration: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise RuntimeError("Dub request должен быть JSON-объектом.")
    duration_value = _finite(duration, field="video_duration")
    if duration_value <= 0.0:
        raise RuntimeError("video_duration должен быть > 0.")
    cfg = _finite(_setting(request, "cfg", 1.8), field="cfg")
    if not 0.05 <= cfg <= MAX_CFG:
        raise RuntimeError(f"cfg={cfg} вне диапазона 0.05..{MAX_CFG}.")
    original_level = _finite(
        _setting(request, "original_level", 0.18),
        field="original_level",
    )
    if not 0.0 <= original_level <= 1.0:
        raise RuntimeError("original_level должен быть в диапазоне 0..1.")
    video_id = str(_setting(request, "video_id", "")).strip()
    if not video_id:
        raise RuntimeError("video_id пуст.")
    return {
        "video_id": video_id,
        "duration": duration_value,
        "threads": _bounded_int(
            _setting(request, "threads", 10),
            field="threads",
            low=1,
            high=MAX_THREADS,
        ),
        "steps": _bounded_int(
            _setting(request, "steps", 16),
            field="steps",
            low=1,
            high=MAX_STEPS,
        ),
        "cfg": cfg,
        "base_seed": _bounded_int(
            _setting(request, "base_seed", 2026072800),
            field="base_seed",
            low=0,
            high=MAX_BASE_SEED,
        ),
        "original_level": original_level,
    }


def _module_hashes(repo: Path, names: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in names:
        path = repo / name
        if not path.is_file():
            raise RuntimeError(f"Не найден fingerprint-модуль: {path}")
        result[name] = sha256_file(path)
    return result


def _model_manifest(archive: Path) -> dict[str, Any]:
    model = discover_model(archive)
    artifacts: list[dict[str, Any]] = []
    for path in sorted(model.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.casefold() not in {
            ".json",
            ".safetensors",
            ".bin",
            ".pth",
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


def _voxcpm_runtime(cpu_python: Path) -> dict[str, Any]:
    if not cpu_python.is_file():
        raise RuntimeError(f"CPU Python не найден: {cpu_python}")
    script = r'''
import hashlib
import importlib.metadata
import json
from pathlib import Path
import voxcpm

root = Path(voxcpm.__file__).resolve().parent
files = []
for path in sorted(root.rglob("*.py"), key=lambda item: str(item).casefold()):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    files.append({
        "path": str(path.relative_to(root)).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": digest,
    })
versions = {}
for package in ("voxcpm", "torch", "transformers", "tokenizers", "numpy", "soundfile", "wetext"):
    try:
        versions[package] = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        versions[package] = "missing"
payload = {
    "module": str(Path(voxcpm.__file__).resolve()),
    "package_root": str(root),
    "versions": versions,
    "python_files": files,
}
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
'''
    process = subprocess.run(
        [str(cpu_python), "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(
            "Не удалось fingerprint-нуть установленный voxcpm runtime: "
            + (process.stderr or process.stdout or "")[-1500:]
        )
    try:
        payload = json.loads((process.stdout or "").strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("voxcpm runtime вернул некорректный fingerprint JSON.") from exc
    files = payload.get("python_files") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or not payload.get("module")
        or not isinstance(files, list)
        or not files
    ):
        raise RuntimeError("voxcpm runtime fingerprint неполон.")
    return payload


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_fingerprints(
    *,
    repo: Path,
    archive: Path,
    cpu_python: Path,
) -> dict[str, Any]:
    repo = repo.resolve()
    archive = archive.resolve()
    render_payload = {
        "policy": POLICY,
        "contract_module_sha256": sha256_file(Path(__file__).resolve()),
        "implementation": _module_hashes(repo, _RENDER_MODULES),
        "model": _model_manifest(archive),
        "voxcpm_runtime": _voxcpm_runtime(cpu_python.resolve()),
    }
    release_payload = {
        "policy": POLICY,
        "implementation": _module_hashes(repo, _RELEASE_MODULES),
    }
    return {
        "policy": POLICY,
        "render_contract_sha256": _digest(render_payload),
        "release_contract_sha256": _digest(release_payload),
        "render": render_payload,
        "release": release_payload,
    }


__all__ = [
    "MAX_BASE_SEED",
    "MAX_CFG",
    "MAX_SEED",
    "MAX_STEPS",
    "MAX_THREADS",
    "POLICY",
    "RETRY_SEED_OFFSET",
    "build_fingerprints",
    "normalize_settings",
    "sampled_sha256_file",
    "sha256_file",
]
