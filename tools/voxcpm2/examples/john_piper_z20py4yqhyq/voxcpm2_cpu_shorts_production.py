#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stable PowerShell/bot CLI for the direct VoxCPM2 max-quality renderer.

The entrypoint maintains a renderer-runtime marker in the work directory. Old
checkpoints are removed when the executable modules, model snapshot, CPU-venv
VoxCPM runtime or cache-length contract changes. This protects both bot and
manual PowerShell launches without patching VoxCPM internals.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.voxcpm2 import clean_runtime_contract
from tools.voxcpm2.direct_max_quality_cli import main

MARKER_POLICY = "direct-cli-runtime-marker-v1"


def _flag(flag: str, *, default: str | None = None) -> str:
    try:
        index = sys.argv.index(flag)
    except ValueError:
        if default is not None:
            return default
        raise RuntimeError(f"В direct CLI отсутствует обязательный параметр {flag}.")
    if index + 1 >= len(sys.argv):
        raise RuntimeError(f"После {flag} отсутствует значение.")
    return str(sys.argv[index + 1])


def _read_marker(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _clear_checkpoint_state(work_dir: Path) -> None:
    for name in ("checkpoints", "segments_clean", "segments_fitted", "attempts"):
        target = work_dir / name
        if target.is_dir():
            shutil.rmtree(target)


def _runtime_contract() -> tuple[Path, dict[str, Any]]:
    work_dir = Path(_flag("--work-dir")).resolve()
    archive = Path(_flag("--archive-root")).resolve()
    try:
        cache_length = int(_flag("--cache-length", default="4096"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Некорректный --cache-length.") from exc
    if cache_length < 2048 or cache_length > 131072:
        raise RuntimeError("--cache-length должен быть в диапазоне 2048..131072.")
    fingerprints = clean_runtime_contract.build_fingerprints(
        repo=REPO_ROOT,
        archive=archive,
        cpu_python=Path(sys.executable).resolve(),
    )
    return work_dir, {
        "schema_version": 1,
        "policy": MARKER_POLICY,
        "render_contract_sha256": fingerprints["render_contract_sha256"],
        "cache_length": cache_length,
        "python_executable": str(Path(sys.executable).resolve()),
    }


def _prepare_runtime_marker() -> tuple[Path, dict[str, Any]]:
    work_dir, expected = _runtime_contract()
    work_dir.mkdir(parents=True, exist_ok=True)
    marker_path = work_dir / "direct_cli_runtime.marker.json"
    current = _read_marker(marker_path)
    checkpoints_exist = any((work_dir / "checkpoints").glob("segment_*.json"))
    if checkpoints_exist and current != expected:
        print(
            "[DIRECT-CLI] renderer/model/runtime fingerprint изменился; "
            "старые checkpoints удалены",
            flush=True,
        )
        _clear_checkpoint_state(work_dir)
    marker_path.unlink(missing_ok=True)
    return marker_path, expected


if __name__ == "__main__":
    marker_path: Path | None = None
    marker_payload: dict[str, Any] | None = None
    try:
        marker_path, marker_payload = _prepare_runtime_marker()
        main()
        marker_path.write_text(
            json.dumps(marker_payload, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
    except KeyboardInterrupt:
        print("Остановлено пользователем.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        import traceback

        if marker_path is not None:
            marker_path.unlink(missing_ok=True)
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
