from __future__ import annotations

import hashlib
import json
from pathlib import Path

from services.dub_studio import load_recipe
from tools.voxcpm2 import legacy_segment_migration_v45
from tools.voxcpm2 import professional_audio_qa_v45 as qa
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
        json.dumps(
            {
                "repair_all": True,
                "segment_ids": [1, 2],
                "segments_sha256": digest,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "output" / "manifest.json").write_text(
        json.dumps({"segments": 2}),
        encoding="utf-8",
    )

    assert legacy_segment_migration_v45.migrate(
        tmp_path,
        {"russian_delay_ms": 420},
    )
    migrated = json.loads(segments_path.read_text(encoding="utf-8"))
    assert len(migrated) > len(old)
    assert " ".join(item["text"] for item in old).split() == " ".join(
        item["text"] for item in migrated
    ).split()
    assert max(float(item["end"]) - float(item["start"]) for item in migrated) <= 5.6
    assert all(item["quality_timing"] == "global-delay-v4.5" for item in migrated)
    assert (tmp_path / "segments_ru_final.pre_v45.json").is_file()


def test_recipe_routes_all_modes_through_clean_runtime() -> None:
    recipe = load_recipe("generic_short_v1")
    assert recipe.action("render_gemini")["module"] == (
        "tools.voxcpm2.generic_gemini_runtime"
    )
    assert recipe.action("render_direct")["module"] == (
        "tools.voxcpm2.generic_clean_direct_runtime"
    )
    assert recipe.action("repair_audio")["module"] == (
        "tools.voxcpm2.generic_clean_audio_repair_runtime"
    )


def test_voice_gate_is_fail_closed_and_expression_aware() -> None:
    calm_limits = qa._voice_limits(
        {"expression_tier": "warm", "expression_score": 0.0},
        profile_name="extended",
        reference_median=110.0,
        reference_p90=145.0,
    )
    expressive_limits = qa._voice_limits(
        {"expression_tier": "emphatic", "expression_score": 0.5},
        profile_name="composite",
        reference_median=110.0,
        reference_p90=145.0,
    )
    assert expressive_limits["max_median_ratio"] > calm_limits["max_median_ratio"]
    assert expressive_limits["max_p90_ratio"] > calm_limits["max_p90_ratio"]

    item = {"expression_tier": "warm", "expression_score": 0.0}
    accepted = qa._voice_evaluation(
        item,
        profile_name="extended",
        reference={"voiced_ratio": 0.7, "f0_median": 110.0, "f0_p90": 145.0},
        candidate={"voiced_ratio": 0.6, "f0_median": 114.0, "f0_p90": 150.0},
    )
    missing = qa._voice_evaluation(
        item,
        profile_name="extended",
        reference=None,
        candidate={"voiced_ratio": 0.6, "f0_median": 114.0, "f0_p90": 150.0},
    )
    out_of_range = qa._voice_evaluation(
        item,
        profile_name="extended",
        reference={"voiced_ratio": 0.7, "f0_median": 110.0, "f0_p90": 145.0},
        candidate={"voiced_ratio": 0.6, "f0_median": 190.0, "f0_p90": 245.0},
    )

    assert qa.POLICY == "clean-expression-aware-qa-v3"
    assert qa.VOICE_EVIDENCE_POLICY == "fail-closed-reference-f0-v1"
    assert accepted["passed"] is True
    assert missing["passed"] is False
    assert missing["failure_reason"] == "missing_reference_profile"
    assert out_of_range["passed"] is False
    assert out_of_range["failure_reason"] == "pitch_ratio_out_of_range"


def test_renderer_adapter_keeps_acoustic_rejection_evidence() -> None:
    adapter = Path("tools/voxcpm2/voxcpm2_professional_adapter_v45.py").read_text(
        encoding="utf-8"
    )
    assert "max_internal_gap" in adapter
    assert "f0_median_ratio" in adapter
    assert "cut_risk" in adapter
    assert 'candidate["clipping_ratio"] = max(measured_clipping, 0.000501)' in adapter
    assert 'tail_info"]["suspicious"] = True' not in adapter
