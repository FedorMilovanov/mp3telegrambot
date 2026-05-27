"""Regression tests for v3 patch 32 — observability feedback loop."""

from pathlib import Path

from core.adaptive_generation import (
    GeminiTaskHealth,
    adapt_text_generation_params,
)


def test_adaptive_generation_lowers_temperature_on_json_invalid_rate():
    params = adapt_text_generation_params(
        task="telegraph_studyanalysis",
        base_temperature=0.4,
        base_max_tokens=26000,
        health=GeminiTaskHealth(task="telegraph_studyanalysis", total=10, json_invalid=4),
    )
    assert params.temperature == 0.2
    assert params.max_tokens == 26000
    assert "json_invalid_rate=40%" in params.reason


def test_adaptive_generation_increases_max_tokens_on_max_token_hits():
    params = adapt_text_generation_params(
        task="telegraph_reflectionapplication",
        base_temperature=0.4,
        base_max_tokens=22000,
        health=GeminiTaskHealth(task="telegraph_reflectionapplication", total=5, max_tokens=1),
    )
    assert params.temperature == 0.4
    assert params.max_tokens == 33000
    assert "max_tokens_hits=1" in params.reason


def test_adaptive_generation_combines_signals_and_caps_tokens():
    params = adapt_text_generation_params(
        task="telegraph_studyanalysis",
        base_temperature=0.4,
        base_max_tokens=50000,
        health=GeminiTaskHealth(task="telegraph_studyanalysis", total=10, json_invalid=5, max_tokens=2),
    )
    assert params.temperature == 0.2
    assert params.max_tokens == 65000
    assert "json_invalid_rate=50%" in params.reason
    assert "max_tokens_hits=2" in params.reason


def test_telegraph_pages_uses_adaptive_generation_params():
    src = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "from core.adaptive_generation import get_adaptive_text_generation_params" in src
    assert "_adaptive = get_adaptive_text_generation_params" in src
    assert "temperature=_adaptive.temperature" in src
    assert "max_tokens=_adaptive.max_tokens" in src
    assert "adaptive retry escalate" in src
