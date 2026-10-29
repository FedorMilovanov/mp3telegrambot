from __future__ import annotations
import pytest

from core.model_catalog import MODEL_PRIMARY

import os

from services.livedub_quality_runtime import configure_gemini_policy


def test_pre_main_policy_forces_live_only_even_if_environment_enables_tts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEDUB_TTS_FALLBACK", "1")

    status = configure_gemini_policy()

    assert os.environ["LIVEDUB_TTS_FALLBACK"] == "0"
    assert "voice=live-only/no-tts-fallback" in status


def test_pre_main_policy_keeps_gemini_38_high_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEDUB_QUICK_QA_MODEL", "gemini-3.7-flash")
    monkeypatch.setenv("LIVEDUB_LONG_QA_MODEL", "gemini-3.7-flash")
    monkeypatch.setenv("LIVEDUB_QA_VERIFY_MODEL", "gemini-3.7-flash")

    configure_gemini_policy()

    assert os.environ["LIVEDUB_QUICK_QA_MODEL"] == MODEL_PRIMARY
    assert os.environ["LIVEDUB_LONG_QA_MODEL"] == MODEL_PRIMARY
    assert os.environ["LIVEDUB_QA_VERIFY_MODEL"] == MODEL_PRIMARY
    assert os.environ["LIVEDUB_QUICK_QA_THINKING"] == "high"
    assert os.environ["LIVEDUB_LONG_QA_THINKING"] == "high"
    assert os.environ["LIVEDUB_QA_VERIFY_THINKING"] == "high"
