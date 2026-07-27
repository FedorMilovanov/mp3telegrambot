#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Checked entrypoint for Gemini MAX Dub Studio production."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.voxcpm2 import generic_project_runtime as production


def _require_file(path: Path, label: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Gemini MAX не создал обязательный результат: {label} ({path}).")


def validate_completed_outputs(root: Path) -> dict[str, Any]:
    output = root / "output"
    mixed = output / "final_upload.mp4"
    russian_only = output / "russian_only.mp4"
    manifest_path = output / "manifest.json"
    _require_file(mixed, "главный MP4")
    _require_file(russian_only, "версия только с русским голосом")
    _require_file(manifest_path, "manifest")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict) or manifest.get("phase") != "completed":
        raise RuntimeError("Gemini MAX manifest не имеет состояния completed.")
    if manifest.get("translation_mode") != "gemini":
        raise RuntimeError("Gemini MAX manifest содержит неверный translation_mode.")

    telegram_outputs = manifest.get("telegram_outputs")
    if not isinstance(telegram_outputs, list) or not telegram_outputs:
        raise RuntimeError("Gemini MAX manifest не содержит Telegram outputs.")
    primary = [item for item in telegram_outputs if isinstance(item, dict) and item.get("primary")]
    if not primary:
        raise RuntimeError("Gemini MAX manifest не содержит основного видео.")
    primary_path = Path(str(primary[0].get("path") or "")).expanduser()
    _require_file(primary_path, "именованный основной MP4")
    return manifest


def main() -> None:
    production.main()
    root = production.project_root(production.current_project_id())
    validate_completed_outputs(root)
    production.log("=== GEMINI MAX OUTPUT CONTRACT: OK ===")


if __name__ == "__main__":
    main()
