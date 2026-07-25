from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "voxcpm2" / "production_preflight.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "voxcpm2_production_preflight",
        MODULE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = _load_module()


def test_load_segments_accepts_final_four_block_plan(tmp_path: Path) -> None:
    path = tmp_path / "segments.json"
    path.write_text(
        json.dumps(
            [
                {"id": 1, "start": 0.0, "end": 10.88, "text": "Один."},
                {"id": 2, "start": 10.88, "end": 24.16, "text": "Два."},
                {"id": 3, "start": 24.72, "end": 32.6, "text": "Три."},
                {"id": 4, "start": 33.2, "end": 48.694, "text": "Четыре."},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    segments = preflight.load_segments(path)

    assert len(segments) == 4
    assert segments[-1]["end"] == pytest.approx(48.694)


def test_load_segments_rejects_overlap(tmp_path: Path) -> None:
    path = tmp_path / "segments.json"
    path.write_text(
        json.dumps(
            [
                {"id": 1, "start": 0.0, "end": 5.0, "text": "Один."},
                {"id": 2, "start": 4.9, "end": 8.0, "text": "Два."},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(preflight.PreflightError, match="overlaps"):
        preflight.load_segments(path)


def test_load_segments_rejects_empty_text(tmp_path: Path) -> None:
    path = tmp_path / "segments.json"
    path.write_text(
        json.dumps([{"id": 1, "start": 0.0, "end": 5.0, "text": "  "}]),
        encoding="utf-8",
    )

    with pytest.raises(preflight.PreflightError, match="empty text"):
        preflight.load_segments(path)


def test_model_snapshot_exists_for_safetensors(tmp_path: Path) -> None:
    snapshot = tmp_path / "models--openbmb--VoxCPM2" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "model.safetensors").write_bytes(b"placeholder")

    assert preflight.model_snapshot_exists(tmp_path) is True


def test_model_snapshot_missing_without_weights(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "config.json").write_text("{}", encoding="utf-8")

    assert preflight.model_snapshot_exists(tmp_path) is False
