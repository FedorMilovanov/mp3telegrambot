from __future__ import annotations

import json
from pathlib import Path

import pytest

from handlers.dub_audio_repair import parse_segment_selector
from tools.voxcpm2 import dub_worker
from tools.voxcpm2.generic_audio_repair_runtime import prepare_repair_checkpoints
from tools.voxcpm2.semantic_tts_guard_v4 import _GUARD_VERSION


def test_segment_selector_accepts_lists_ranges_and_all() -> None:
    available = [1, 2, 3, 4, 5]
    assert parse_segment_selector("2,4-5", available) == [2, 4, 5]
    assert parse_segment_selector("5-3", available) == [3, 4, 5]
    assert parse_segment_selector("все", available) == available
    with pytest.raises(ValueError, match="нет реплик"):
        parse_segment_selector("6", available)


def _checkpoint(root: Path, segment_id: int, seed: int = 100) -> Path:
    path = root / "checkpoints" / f"segment_{segment_id:02d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"signature": {"base_seed": seed}, "report": {"id": segment_id}}),
        encoding="utf-8",
    )
    for directory in ("segments_clean", "segments_fitted", "attempts"):
        target = root / directory / f"{segment_id:02d}_sample.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"wav")
    return path


def test_partial_repair_retargets_good_checkpoints_and_deletes_selected(tmp_path: Path) -> None:
    for segment_id in (1, 2, 3):
        _checkpoint(tmp_path, segment_id)
    (tmp_path / "semantic_guard.marker.json").write_text(
        json.dumps({"guard_version": _GUARD_VERSION, "base_seed": 100}),
        encoding="utf-8",
    )

    prepare_repair_checkpoints(
        tmp_path,
        all_ids={1, 2, 3},
        selected_ids={2},
        new_base_seed=200,
        repair_all=False,
    )

    for segment_id in (1, 3):
        payload = json.loads((tmp_path / "checkpoints" / f"segment_{segment_id:02d}.json").read_text())
        assert payload["signature"]["base_seed"] == 200
    assert not (tmp_path / "checkpoints" / "segment_02.json").exists()
    assert not list((tmp_path / "attempts").glob("02_*"))


def test_legacy_project_requires_full_quality_upgrade_before_partial_repair(tmp_path: Path) -> None:
    _checkpoint(tmp_path, 1)
    with pytest.raises(RuntimeError, match="полного Quality"):
        prepare_repair_checkpoints(
            tmp_path,
            all_ids={1},
            selected_ids={1},
            new_base_seed=200,
            repair_all=False,
        )


def test_full_repair_invalidates_all_audio_checkpoints(tmp_path: Path) -> None:
    for segment_id in (1, 2):
        _checkpoint(tmp_path, segment_id)
    (tmp_path / "semantic_guard.marker.json").write_text("{}", encoding="utf-8")

    prepare_repair_checkpoints(
        tmp_path,
        all_ids={1, 2},
        selected_ids={1, 2},
        new_base_seed=300,
        repair_all=True,
    )

    assert not list((tmp_path / "checkpoints").glob("segment_*.json"))
    assert not (tmp_path / "semantic_guard.marker.json").exists()


def test_recipe_routes_audio_repair_without_gemini() -> None:
    command, spec = dub_worker.build_command("generic_short_v1", "repair_audio")
    assert spec["module"] == "tools.voxcpm2.generic_audio_repair_runtime"
    assert "tools.voxcpm2.generic_audio_repair_runtime" in " ".join(command)
    source = Path("tools/voxcpm2/generic_audio_repair_runtime.py").read_text(encoding="utf-8")
    assert "translate_groups_max" not in source
    assert "gemini_json" not in source
    assert '"gemini_called": False' in source
