from __future__ import annotations

from pathlib import Path

from tools.voxcpm2 import professional_audio_qa_v45 as qa


TARGET = "Это проверка."


def _patch_forced_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        qa.semantic_tts_guard_v4.legacy,
        "_transcribe",
        lambda _clip, *, language=None: (TARGET, "ru", 0.99),
    )


def test_arabic_auto_text_cannot_be_laundered_by_forced_russian(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _patch_forced_pass(monkeypatch)
    result = qa._forced_russian_fallback(
        tmp_path / "clip.wav",
        TARGET,
        {
            "passed": False,
            "heard": "أنا",
            "language": "ar",
            "language_probability": 0.60,
            "foreign_language": False,
        },
    )
    assert result["forced_russian"]["passed"] is True
    assert result["forced_russian_eligible"] is False
    assert result["forced_russian_rescued"] is False
    assert result["forced_russian_block_reason"] == "foreign_script"
    assert result["auto_script_evidence"]["cyrillic_ratio"] == 0.0


def test_empty_auto_text_with_probable_foreign_language_is_not_rescued(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _patch_forced_pass(monkeypatch)
    result = qa._forced_russian_fallback(
        tmp_path / "clip.wav",
        TARGET,
        {
            "passed": False,
            "heard": "",
            "language": "ar",
            "language_probability": 0.40,
            "foreign_language": False,
        },
    )
    assert result["forced_russian_eligible"] is False
    assert result["forced_russian_block_reason"] == "empty_auto_foreign_language"
    assert result["passed"] is False


def test_empty_auto_text_with_unknown_language_may_use_forced_retry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _patch_forced_pass(monkeypatch)
    result = qa._forced_russian_fallback(
        tmp_path / "clip.wav",
        TARGET,
        {
            "passed": False,
            "heard": "",
            "language": "",
            "language_probability": 0.90,
            "foreign_language": False,
        },
    )
    assert result["forced_russian_eligible"] is True
    assert result["forced_russian_block_reason"] == ""
    assert result["forced_russian_rescued"] is True
    assert result["passed"] is True


def test_empty_auto_text_detected_as_russian_may_use_forced_retry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _patch_forced_pass(monkeypatch)
    result = qa._forced_russian_fallback(
        tmp_path / "clip.wav",
        TARGET,
        {
            "passed": False,
            "heard": "",
            "language": "ru",
            "language_probability": 0.40,
            "foreign_language": False,
        },
    )
    assert result["forced_russian_eligible"] is True
    assert result["forced_russian_rescued"] is True
    assert result["passed"] is True


def test_cyrillic_auto_text_with_uncertain_language_may_be_rescued(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _patch_forced_pass(monkeypatch)
    result = qa._forced_russian_fallback(
        tmp_path / "clip.wav",
        TARGET,
        {
            "passed": False,
            "heard": "эта проверка",
            "language": "et",
            "language_probability": 0.41,
            "foreign_language": False,
        },
    )
    assert result["auto_script_evidence"]["cyrillic_ratio"] == 1.0
    assert result["forced_russian_eligible"] is True
    assert result["forced_russian_rescued"] is True
