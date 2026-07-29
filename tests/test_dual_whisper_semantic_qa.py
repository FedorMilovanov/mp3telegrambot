from __future__ import annotations

from pathlib import Path

from tools.voxcpm2 import professional_audio_qa_v45


def test_forced_russian_can_rescue_only_nonforeign_auto_failure(monkeypatch) -> None:
    auto = {
        "passed": False,
        "heard": "благочестивая женшина",
        "foreign_language": False,
        "language": "ru",
        "language_probability": 0.41,
        "token_recall": 0.5,
    }
    monkeypatch.setattr(
        professional_audio_qa_v45.semantic_tts_guard_v4.legacy,
        "_transcribe",
        lambda _clip, language=None: (
            "благочестивая женщина",
            "ru",
            1.0,
        ),
    )
    result = professional_audio_qa_v45._forced_russian_fallback(
        Path("segment.wav"),
        "Благочестивая женщина.",
        auto,
    )
    assert result["passed"] is True
    assert result["forced_russian_rescued"] is True
    assert result["confident_foreign_block"] is False
    assert result["heard"] == "благочестивая женщина"


def test_confident_foreign_auto_result_cannot_be_rescued(monkeypatch) -> None:
    auto = {
        "passed": False,
        "heard": "أنا",
        "foreign_language": True,
        "language": "ar",
        "language_probability": 0.99,
        "token_recall": 0.0,
    }
    # Even an artificially perfect forced-Russian pass must not override a
    # confidently foreign auto-language result.
    monkeypatch.setattr(
        professional_audio_qa_v45.semantic_tts_guard_v4.legacy,
        "_transcribe",
        lambda _clip, language=None: (
            "благочестивая женщина",
            "ru",
            1.0,
        ),
    )
    result = professional_audio_qa_v45._forced_russian_fallback(
        Path("segment.wav"),
        "Благочестивая женщина.",
        auto,
    )
    assert result["passed"] is False
    assert result["forced_russian_rescued"] is False
    assert result["confident_foreign_block"] is True
    assert result["heard"] == "أنا"


def test_failed_forced_russian_keeps_segment_rejected(monkeypatch) -> None:
    auto = {
        "passed": False,
        "heard": "неразборчиво",
        "foreign_language": False,
        "language": "ru",
        "language_probability": 0.55,
        "token_recall": 0.0,
    }
    monkeypatch.setattr(
        professional_audio_qa_v45.semantic_tts_guard_v4.legacy,
        "_transcribe",
        lambda _clip, language=None: ("другая фраза", "ru", 1.0),
    )
    result = professional_audio_qa_v45._forced_russian_fallback(
        Path("segment.wav"),
        "Благочестивая женщина.",
        auto,
    )
    assert result["passed"] is False
    assert result["forced_russian_rescued"] is False
