from __future__ import annotations

import json
from pathlib import Path

from tools.voxcpm2 import semantic_tts_guard_v47 as rescue


def _arabic_failure() -> dict:
    return {
        "failed_segment_ids": [13],
        "segments": [
            {
                "id": 13,
                "passed": False,
                "semantic": {
                    "passed": False,
                    "heard": "أنا",
                    "language": "ar",
                    "foreign_language": True,
                    "token_recall": 0.0,
                    "sequence_similarity": 0.0,
                },
                "timing": {
                    "passed": False,
                    "onset_ms": 0.0,
                    "trailing_ms": 0.0,
                },
            }
        ],
    }


def test_arabic_whisper_output_triggers_semantic_rescue() -> None:
    assert rescue._prompt_leak_ids(_arabic_failure()) == {13}


def test_partial_semantic_rescue_can_resume_nonforeign_followup() -> None:
    report = {
        "failed_segment_ids": [13],
        "segments": [
            {
                "id": 13,
                "semantic": {
                    "passed": False,
                    "heard": "русская фраза распознана не полностью",
                    "token_recall": 0.5,
                    "sequence_similarity": 0.4,
                },
            }
        ],
    }
    marker = {
        "state": "partial_semantic_rescue",
        "failed_segment_ids": [13],
    }
    assert rescue._prompt_leak_ids(report) == set()
    assert rescue._rescue_ids(report, marker) == {13}


def test_rescue_adaptation_preserves_words_and_reserves_tail(tmp_path: Path) -> None:
    path = tmp_path / "segments_guarded.json"
    segments = [
        {
            "id": 13,
            "text": "Она обладает силой и достоинством.",
            "reference_profile": "extended",
            "tail_guard": 0.12,
        },
        {
            "id": 14,
            "text": "Следующая реплика остаётся неизменной.",
            "reference_profile": "extended",
            "tail_guard": 0.18,
        },
    ]
    original_words = segments[0]["text"].split()

    rescue._adapt_rescue_segments(
        path,
        segments,
        {13},
        rescue_round=1,
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved[0]["text"].split() == original_words
    assert saved[0]["reference_profile"] == "composite"
    assert saved[0]["tail_guard"] >= 0.24
    assert "semantic_prompt_transcript" in saved[0]["qa_adaptations"]
    assert "forced_silent_tail" in saved[0]["qa_adaptations"]
    assert saved[1] == segments[1]


def test_resume_seed_prefers_current_checkpoint_signatures(tmp_path: Path) -> None:
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    (checkpoints / "segment_01.json").write_text(
        json.dumps({"signature": {"base_seed": 812345}}),
        encoding="utf-8",
    )
    marker = {"base_seed": 111111}
    command = ["python", "renderer.py", "--base-seed", "222222"]
    assert rescue._resume_seed(tmp_path, marker, command) == 812345


def test_entrypoints_and_rescue_renderer_use_v47_contracts() -> None:
    for path in (
        Path("tools/voxcpm2/generic_audio_repair_runtime_v45.py"),
        Path("tools/voxcpm2/generic_gemini_runtime_v45.py"),
        Path("tools/voxcpm2/generic_direct_checked_runtime_v45.py"),
    ):
        text = path.read_text(encoding="utf-8")
        assert "semantic_tts_guard_v47" in text
        assert "semantic_tts_guard_v47.install()" in text

    wrapper = Path(
        "tools/voxcpm2/voxcpm2_semantic_rescue_v47.py"
    ).read_text(encoding="utf-8")
    compile(wrapper, "voxcpm2_semantic_rescue_v47.py", "exec")
    assert 'values["prompt_text"] = prompt_texts[profile]' in wrapper
    assert 'values["retry_badcase"] = True' in wrapper
    assert "sample_rate * 0.160" in wrapper
