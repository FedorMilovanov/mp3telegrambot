from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.voxcpm2.semantic_tts_guard import (
    _prepare_guarded_segments,
    _retarget_checkpoints,
    compare_spoken_text,
    sanitize_tts_text,
)


def test_tts_sanitizer_removes_continuation_ellipses_without_dropping_words() -> None:
    source = "«...и смеётся она над грядущим временем...»"
    result = sanitize_tts_text(source)
    assert result == "и смеётся она над грядущим временем."
    assert result.split() == ["и", "смеётся", "она", "над", "грядущим", "временем."]


def test_semantic_verifier_rejects_english_reference_leak() -> None:
    result = compare_spoken_text(
        "Бог обещает помогать женщинам всякий раз, когда они нуждаются в Нём.",
        "God promises to help women whenever she needs him.",
        "en",
        0.99,
    )
    assert result["passed"] is False
    assert result["foreign_language"] is True
    assert result["latin_ratio"] > 0.9


def test_semantic_verifier_accepts_normal_whisper_variation() -> None:
    result = compare_spoken_text(
        "Сила и достоинство — одежда её.",
        "Сила и достоинство одежда ее",
        "ru",
        0.99,
    )
    assert result["passed"] is True


def test_guarded_segments_preserve_original_copy(tmp_path: Path) -> None:
    source = tmp_path / "segments.json"
    destination = tmp_path / "guarded.json"
    source.write_text(
        json.dumps([{"id": 1, "start": 0, "end": 2, "text": "...Бог благ..."}], ensure_ascii=False),
        encoding="utf-8",
    )
    result = _prepare_guarded_segments(source, destination)
    assert result[0]["display_text"] == "...Бог благ..."
    assert result[0]["text"] == "Бог благ."


def test_retry_reuses_good_checkpoints_and_drops_bad_ones(tmp_path: Path) -> None:
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    for segment_id in (1, 2):
        (checkpoints / f"segment_{segment_id:02d}.json").write_text(
            json.dumps({"signature": {"base_seed": 100}, "report": {"id": segment_id}}),
            encoding="utf-8",
        )
    _retarget_checkpoints(tmp_path, good_ids=[1], failed_ids=[2], new_base_seed=200)
    good = json.loads((checkpoints / "segment_01.json").read_text(encoding="utf-8"))
    assert good["signature"]["base_seed"] == 200
    assert not (checkpoints / "segment_02.json").exists()


def _model_class(api: str, calls: list[dict[str, Any]]) -> type:
    if api == "old":
        class OldModel:
            def generate(
                self,
                *,
                text,
                reference_wav_path,
                cfg_value,
                normalize,
                denoise,
                retry_badcase=False,
                retry_badcase_max_times=0,
                retry_badcase_ratio_threshold=0,
            ):
                calls.append(
                    {
                        "reference": reference_wav_path,
                        "prompt": None,
                        "retry": retry_badcase,
                    }
                )
                return [0]

        return OldModel

    if api == "legacy_kwargs":
        class LegacyKwargsModel:
            def generate(
                self,
                *,
                text,
                reference_wav_path,
                cfg_value,
                normalize,
                denoise,
                retry_badcase=False,
                **kwargs,
            ):
                assert "prompt_wav_path" not in kwargs
                assert "prompt_text" not in kwargs
                calls.append(
                    {
                        "reference": reference_wav_path,
                        "prompt": None,
                        "retry": retry_badcase,
                    }
                )
                return [0]

        return LegacyKwargsModel

    class NewModel:
        def generate(
            self,
            *,
            text,
            prompt_wav_path,
            prompt_text,
            cfg_value,
            normalize,
            denoise,
            retry_badcase=False,
            retry_badcase_max_times=0,
            retry_badcase_ratio_threshold=0,
        ):
            calls.append(
                {
                    "reference": prompt_wav_path,
                    "prompt": prompt_text,
                    "retry": retry_badcase,
                }
            )
            return [0]

    return NewModel


def _fake_voxcpm_class(model_class: type) -> type:
    class FakeVoxCPM:
        @staticmethod
        def from_pretrained(*args, load_denoiser=False, **kwargs):
            return model_class()

    return FakeVoxCPM


