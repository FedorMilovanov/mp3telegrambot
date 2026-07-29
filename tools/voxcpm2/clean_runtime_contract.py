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

POLICY = "clean-runtime-contract-v1"
MAX_THREADS = 64
MAX_STEPS = 64
MAX_CFG = 10.0
MAX_SEED = 2**63 - 1
RETRY_SEED_OFFSET = 100_000
MAX_BASE_SEED = MAX_SEED - RETRY_SEED_OFFSET

_RENDER_MODULES = (
    "tools/voxcpm2/direct_max_quality_io.py",
    "tools/voxcpm2/direct_timbre_analysis.py",
    "tools/voxcpm2/direct_max_quality_analysis.py",
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


def _finite(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
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


def normalize_settings(request: dict[str, Any], *, duration: Any) -> dict[str, Any]:
    duration_value = _finite(duration, field="video_duration")
    if duration_value <= 0.0:
        raise RuntimeError("video_duration должен быть > 0.")
    cfg = _finite(request.get("cfg") or 1.8, field="cfg")
    if not 0.05 <= cfg <= MAX_CFG:
        raise RuntimeError(f"cfg={cfg} вне диапазона 0.05..{MAX_CFG}.")
    original_level = _finite(
        request.get("original_level") if request.get("original_level") is not None else 0.18,
        field="original_level",
    )
    if not 0.0 <= original_level <= 1.0:
        raise RuntimeError("original_level должен быть в диапазоне 0..1.")
    video_id = str(request.get("video_id") or "").strip()
    if not video_id:
        raise RuntimeError("video_id пуст.")
    return {
        "video_id": video_id,
        "duration": duration_value,
        "threads": _bounded_int(
            request.get("threads") or 10,
            field="threads",
            low=1,
            high=MAX_THREADS,
        ),
        "steps": _bounded_int(
            request.get("steps") or 16,
            field="steps",
            low=1,
            high=MAX_STEPS,
        ),
        "cfg": cfg,
        "base_seed": _bounded_int(
            request.get("base_seed") or 2026072800,
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
    artifacts = []
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
        if path.name == "config.json":
            item["sha256"] = sha256_file(path)
        artifacts.append(item)
    if not artifacts:
        raise RuntimeError(f"Model snapshot не содержит fingerprint-артефактов: {model}")
    return {"path": str(model), "artifacts": artifacts}


def _voxcpm_runtime(cpu_python: Path) -> dict[str, Any]:
    if not cpu_python.is_file():
        raise RuntimeError(f"CPU Python не найден: {cpu_python}")
    script = (
        "import importlib.metadata,json,pathlib,voxcpm;"
        "p=pathlib.Path(voxcpm.__file__).resolve();"
        "\ntry:v=importlib.metadata.version('voxcpm')"
        "\nexcept importlib.metadata.PackageNotFoundError:v='unknown'"
        "\ns=p.stat();print(json.dumps({'version':v,'module':str(p),"
        "'size':s.st_size,'mtime_ns':s.st_mtime_ns}))"
    )
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
    if not isinstance(payload, dict) or not payload.get("module"):
        raise RuntimeError("voxcpm runtime fingerprint неполон.")
    return payload


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
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
    "sha256_file",
]
