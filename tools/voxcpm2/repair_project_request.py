#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repair and document one Dub Studio TTS request safely.

This utility removes conflicting flat TTS keys, writes the canonical nested
speech profile structure, validates it through the production loader, and
restores the original request automatically if validation fails.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_CPU_VENV = Path(r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv")
DEFAULT_VOX_ARCHIVE = Path(r"C:\AI-Archive\VoxCPM2-paused-RTX3060")
DEFAULT_PROFILE_ID = "voxcpm2-production-v1"
DEFAULT_BACKEND_ID = "voxcpm2"

MODEL_OPTION_KEYS = (
    "threads",
    "steps",
    "cfg",
    "cache_length",
    "base_seed",
)
BACKEND_CONFIG_KEYS = (
    "vox_archive",
    "cpu_venv",
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Не удалось прочитать JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"request.json должен содержать JSON-объект: {path}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def canonicalize_request(
    payload: dict[str, Any],
    *,
    threads: int,
    steps: int,
    cfg: float,
    cache_length: int,
    base_seed: int,
    cpu_venv: Path,
    vox_archive: Path,
    original_level: float,
    russian_delay_ms: int,
) -> dict[str, Any]:
    """Return a canonical request without conflicting flat TTS duplicates."""
    result = copy.deepcopy(payload)

    for key in (*MODEL_OPTION_KEYS, *BACKEND_CONFIG_KEYS):
        result.pop(key, None)
    result.pop("speech_profile_fingerprint", None)

    result["speech_backend"] = DEFAULT_BACKEND_ID
    result["speech_model_profile"] = DEFAULT_PROFILE_ID
    result["speech_options"] = {
        "threads": int(threads),
        "steps": int(steps),
        "cfg": float(cfg),
        "cache_length": int(cache_length),
        "base_seed": int(base_seed),
    }
    result["speech_backend_config"] = {
        "vox_archive": str(Path(vox_archive)),
        "cpu_venv": str(Path(cpu_venv)),
    }
    result["original_level"] = float(original_level)
    result["russian_delay_ms"] = int(russian_delay_ms)
    return result


def _validate_arguments(args: argparse.Namespace) -> None:
    if not 1 <= args.threads <= 64:
        raise RuntimeError("threads должен быть в диапазоне 1..64.")
    if not 1 <= args.steps <= 256:
        raise RuntimeError("steps должен быть в диапазоне 1..256.")
    if not 0.1 <= args.cfg <= 10.0:
        raise RuntimeError("cfg должен быть в диапазоне 0.1..10.0.")
    if not 2048 <= args.cache_length <= 131072:
        raise RuntimeError("cache-length должен быть в диапазоне 2048..131072.")
    if not 0 <= args.base_seed <= 2147483647:
        raise RuntimeError("base-seed должен быть в диапазоне 0..2147483647.")
    if not 0.0 <= args.original_level <= 1.0:
        raise RuntimeError("original-level должен быть в диапазоне 0..1.")
    if not 0 <= args.russian_delay_ms <= 1500:
        raise RuntimeError("russian-delay-ms должен быть в диапазоне 0..1500.")


def _backup_path(request_path: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return request_path.with_name(f"request.before_tts_repair_{stamp}.json")


def validate_with_production_loader(project_root: Path) -> dict[str, Any]:
    """Validate through the exact loader used by Dub Studio production."""
    from tools.voxcpm2.generic_project_runtime import load_request

    return load_request(project_root)


def write_project_notes(project_root: Path, request: dict[str, Any]) -> Path:
    project_id = project_root.name
    options = dict(request.get("speech_options") or {})
    backend_config = dict(request.get("speech_backend_config") or {})
    timestamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    path = project_root / "PROJECT_TTS_OPERATIONS.md"
    text = f"""# TTS operations — {project_id}

Последняя нормализация: `{timestamp}`

## Активная production-конфигурация

```text
Project ID:      {project_id}
Backend:         {request.get('speech_backend', '')}
Model profile:   {request.get('speech_model_profile', '')}
Model archive:   {backend_config.get('vox_archive', '')}
CPU venv:        {backend_config.get('cpu_venv', '')}
Threads:         {options.get('threads', '')}
Base steps:      {options.get('steps', '')}
CFG:             {options.get('cfg', '')}
Cache length:    {options.get('cache_length', '')}
Base seed:       {options.get('base_seed', '')}
Original level:  {request.get('original_level', '')}
Russian delay:   {request.get('russian_delay_ms', '')} ms
GPU:             disabled for this CPU profile
```

## Нельзя делать

- Не создавать плоские `request.steps`, `request.cfg`, `request.threads`, если есть `speech_options`.
- Не устанавливать экспериментальные TTS-пакеты в Python бота или VoxCPM2 venv.
- Не удалять весь Hugging Face cache; очистка только по точному allowlist.
- Не переустанавливать VoxCPM2 из-за ошибки request.json или неверного пути.
- Не называть MOSS-TTS 8B 1.0 версией 1.5.
- Не запускать неизвестную GPU-конфигурацию: `CUDA_VISIBLE_DEVICES=-1` остаётся обязательным.

## Разрешённая очистка перед полным повторным рендером

```text
segment_work/
master_work/
references/
audio/
```

Сохранять `request.json`, `input/`, `source/`, `output/`, модель VoxCPM2 и CPU venv.

## Проверка и ремонт

```powershell
py -3.13 -m tools.voxcpm2.repair_project_request `
  --project-root "{project_root}" `
  --write `
  --write-notes
```

Подробный runbook: `docs/voxcpm2_cpu_operations.md` в репозитории бота.
"""
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely repair one Dub Studio VoxCPM2 request.json."
    )
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--threads", type=int, default=10)
    parser.add_argument("--steps", type=int, default=22)
    parser.add_argument("--cfg", type=float, default=1.8)
    parser.add_argument("--cache-length", type=int, default=4096)
    parser.add_argument("--base-seed", type=int, default=2026080322)
    parser.add_argument("--cpu-venv", type=Path, default=DEFAULT_CPU_VENV)
    parser.add_argument("--vox-archive", type=Path, default=DEFAULT_VOX_ARCHIVE)
    parser.add_argument("--original-level", type=float, default=0.18)
    parser.add_argument("--russian-delay-ms", type=int, default=520)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the repaired request. Without this flag the command is a dry run.",
    )
    parser.add_argument(
        "--write-notes",
        action="store_true",
        help="Write PROJECT_TTS_OPERATIONS.md after successful validation.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _validate_arguments(args)

    project_root = args.project_root.expanduser().resolve()
    request_path = project_root / "request.json"
    if not project_root.is_dir():
        raise RuntimeError(f"Не найден project root: {project_root}")
    if not request_path.is_file():
        raise RuntimeError(f"Не найден request.json: {request_path}")

    current = _read_json(request_path)
    repaired = canonicalize_request(
        current,
        threads=args.threads,
        steps=args.steps,
        cfg=args.cfg,
        cache_length=args.cache_length,
        base_seed=args.base_seed,
        cpu_venv=args.cpu_venv,
        vox_archive=args.vox_archive,
        original_level=args.original_level,
        russian_delay_ms=args.russian_delay_ms,
    )

    print("=== VOXCPM2 REQUEST REPAIR ===")
    print(f"Project: {project_root}")
    print(json.dumps({
        "speech_backend": repaired["speech_backend"],
        "speech_model_profile": repaired["speech_model_profile"],
        "speech_options": repaired["speech_options"],
        "speech_backend_config": repaired["speech_backend_config"],
        "original_level": repaired["original_level"],
        "russian_delay_ms": repaired["russian_delay_ms"],
    }, ensure_ascii=False, indent=2))

    if not args.write:
        print("DRY RUN: request.json не изменён. Добавьте --write для применения.")
        return

    backup = _backup_path(request_path)
    shutil.copy2(request_path, backup)
    print(f"Backup: {backup}")

    try:
        _write_json_atomic(request_path, repaired)
        validated = validate_with_production_loader(project_root)
    except Exception:
        shutil.copy2(backup, request_path)
        print("VALIDATION FAILED: исходный request.json восстановлен.")
        raise

    print("CONFIG VALID")
    print(f"backend = {validated['speech_backend']}")
    print(f"profile = {validated['speech_model_profile']}")
    print(f"threads = {validated['threads']}")
    print(f"steps = {validated['steps']}")
    print(f"cfg = {validated['cfg']}")
    print(f"cache_length = {validated['cache_length']}")
    print(f"base_seed = {validated['base_seed']}")

    if args.write_notes:
        notes_path = write_project_notes(project_root, repaired)
        print(f"Project notes: {notes_path}")


if __name__ == "__main__":
    main()
