from __future__ import annotations

from pathlib import Path

from tools.voxcpm2 import direct_max_quality_render
from tools.voxcpm2 import semantic_block_runtime as blocks
from tools.voxcpm2.generic_short_production import Cue


ROOT = Path(__file__).resolve().parents[1]


def test_ready_srt_is_rendered_in_balanced_semantic_blocks() -> None:
    cues = [Cue(index * 4.0, (index + 1) * 4.0, f"Фраза номер {index + 1}.") for index in range(4)]
    planned = blocks.group_ready_srt(cues)

    assert [(item["start"], item["end"]) for item in planned] == [
        (0.0, 8.0),
        (8.0, 16.0),
    ]
    assert all(item["semantic_block_policy"] == blocks.POLICY for item in planned)
    assert all(
        blocks.MIN_BLOCK_SECONDS <= float(item["end"]) - float(item["start"]) <= blocks.MAX_BLOCK_SECONDS
        for item in planned
    )
    assert sum(int(item["source_cue_count"]) for item in planned) == len(cues)


def test_one_full_candidate_unit_keeps_original_subtitle_cues() -> None:
    cues = [Cue(index * 4.0, (index + 1) * 4.0, f"Реплика {index + 1}.") for index in range(4)]
    planned = blocks.group_ready_srt(cues)
    segments, subtitles = blocks.build_direct_segments(
        planned,
        delay_ms=420,
        duration=16.0,
    )

    assert len(segments) == 2
    assert all(item["reference_profile"] == "extended" for item in segments)
    assert all(item["semantic_block_policy"] == blocks.POLICY for item in segments)
    assert len(subtitles) == len(cues)
    assert [cue.text for cue in subtitles] == [cue.text for cue in cues]
    assert subtitles[0].start == 0.42


def test_direct_entrypoint_uses_block_runtime_and_not_phrase_runtime() -> None:
    source = (ROOT / "tools" / "voxcpm2" / "generic_clean_direct_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "semantic_block_runtime.group_ready_srt" in source
    assert "clean.build_direct_segments(" in source
    core = (ROOT / "tools" / "voxcpm2" / "clean_production_core" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "semantic_block_runtime.build_direct_segments(" in core


def test_previous_block_is_optional_prompt_context_when_backend_exposes_it(tmp_path: Path) -> None:
    previous = tmp_path / "previous.wav"
    previous.write_bytes(b"wav")

    class FakeModel:
        def generate(self, *, text, reference_wav_path, prompt_wav_path=None, prompt_text=None, **kwargs):
            return {
                "text": text,
                "reference": reference_wav_path,
                "prompt": prompt_wav_path,
                "prompt_text": prompt_text,
            }

    result = direct_max_quality_render._legacy._generate(
        FakeModel(),
        text="Следующий блок.",
        reference=tmp_path / "anchor.wav",
        cfg=1.8,
        steps=16,
        min_len=2,
        max_len=40,
        seed=7,
        continuation_reference=previous,
        continuation_text="Предыдущий блок.",
    )

    assert result["prompt"] == str(previous)
    assert result["prompt_text"] == "Предыдущий блок."


def test_direct_policy_disables_source_prosody_ranking_input() -> None:
    source = (ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "source_prosody_policy.ranking_view(display_segment)" in source
    assert 'match["source_prosody_ranking_enabled"] = False' in source
    assert 'match["source_prosody_policy"] = source_prosody_policy.POLICY' in source
