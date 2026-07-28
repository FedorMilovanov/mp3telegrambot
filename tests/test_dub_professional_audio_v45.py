from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.voxcpm2 import legacy_segment_migration_v45
from tools.voxcpm2 import professional_audio_v45 as policy


def test_global_delay_does_not_remove_time_from_every_segment() -> None:
    groups = [
        {"start": 0.0, "end": 5.0, "english": "one"},
        {"start": 5.0, "end": 10.0, "english": "two"},
    ]
    translations = [{"russian": "один"}, {"russian": "два"}]
    segments, subtitles = policy.build_render_segments_v45(
        groups,
        translations,
        delay_ms=420,
        duration=12.0,
    )
    assert [item["end"] for item in segments] == [5.0, 10.0]
    assert [item["start_delay_ms"] for item in segments] == [420, 420]
    assert subtitles[0].end == 5.42
    assert subtitles[1].start == 5.42


def test_legacy_repair_migration_preserves_every_word(tmp_path: Path) -> None:
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    old = [
        {
            "id": 1,
            "start": 3.0,
            "end": 17.5,
            "source_end": 17.92,
            "start_delay_ms": 420,
            "text": "Первая мысль должна сохраниться полностью. Повтор. Повтор. Затем следует заключительная фраза без потерь.",
        },
        {
            "id": 2,
            "start": 17.92,
            "end": 27.0,
            "source_end": 27.42,
            "start_delay_ms": 420,
            "text": "Вторая длинная реплика также остаётся дословно той же самой.",
        },
    ]
    segments_path = tmp_path / "segments_ru_final.json"
    segments_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    digest = hashlib.sha256(segments_path.read_bytes()).hexdigest()
    (tmp_path / "input" / "audio_repair.json").write_text(
        json.dumps({"repair_all": True, "segment_ids": [1, 2], "segments_sha256": digest}),
        encoding="utf-8",
    )
    (tmp_path / "output" / "manifest.json").write_text(json.dumps({"segments": 2}), encoding="utf-8")

    assert legacy_segment_migration_v45.migrate(tmp_path, {"russian_delay_ms": 420})
    migrated = json.loads(segments_path.read_text(encoding="utf-8"))
    assert len(migrated) > len(old)
    assert " ".join(item["text"] for item in old).split() == " ".join(item["text"] for item in migrated).split()
    assert max(float(item["end"]) - float(item["start"]) for item in migrated) <= 5.6
    assert all(item["quality_timing"] == "global-delay-v4.5" for item in migrated)
    assert (tmp_path / "segments_ru_final.pre_v45.json").is_file()


def test_professional_recipe_and_master_are_selected() -> None:
    recipe = Path("tools/voxcpm2/recipes/generic_short_v1.json").read_text(encoding="utf-8")
    assert "generic_gemini_runtime_v45" in recipe
    assert "generic_direct_checked_runtime_v45" in recipe
    assert "generic_audio_repair_runtime_v45" in recipe
    master = Path("tools/voxcpm2/master_quality_v45.py").read_text(encoding="utf-8")
    assert '_replace("--target-i", "-16.0")' in master
    assert '_replace("--target-tp", "-1.5")' in master


def test_renderer_rejects_pause_cutoff_and_pitch_drift_contracts() -> None:
    adapter = Path("tools/voxcpm2/voxcpm2_professional_adapter_v45.py").read_text(encoding="utf-8")
    assert "max_internal_gap" in adapter
    assert "f0_median_ratio" in adapter
    assert "cut_risk" in adapter
    assert 'candidate.setdefault("tail_info", {})["suspicious"] = True' in adapter
