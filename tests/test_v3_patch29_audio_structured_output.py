"""Regression tests for v3 patch 29 — structured output for primary audio analysis."""

import ast
from pathlib import Path

from core.candidate_schema import audio_analysis_response_schema, structured_json_config_kwargs
from core.globals import make_audio_config


def test_audio_analysis_response_schema_contract():
    schema = audio_analysis_response_schema()
    assert schema["type"] == "object"
    required = set(schema["required"])
    for key in [
        "real_author", "real_title", "format", "main_topic", "timestamps",
        "hashtags", "analysis_summary", "argument_arc", "key_categories",
        "questions", "terms_data", "whisper_hints", "hermeneutic_method",
    ]:
        assert key in required
    assert schema["properties"]["timestamps"]["items"]["required"] == ["time", "topic"]
    td = schema["properties"]["terms_data"]
    assert set(td["required"]) == {"concepts", "scripture", "translations", "lexicon_notes"}


def test_audio_schema_works_with_structured_config_kwargs():
    kwargs = structured_json_config_kwargs(audio_analysis_response_schema())
    assert kwargs["response_mime_type"] == "application/json"
    assert kwargs["response_schema"]["properties"]["terms_data"]["type"] == "object"


def test_make_audio_config_accepts_audio_analysis_response_schema():
    cfg = make_audio_config(
        model_name="gemini-3.5-flash",
        response_mime_type="application/json",
        response_schema=audio_analysis_response_schema(),
    )
    assert cfg is not None
    # google-genai GenerateContentConfig is pydantic-like in current SDK.
    assert getattr(cfg, "response_mime_type", None) == "application/json"
    assert getattr(cfg, "response_schema", None) is not None


def test_gemini_analyze_uses_schema_with_legacy_fallback():
    src = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    assert "from core.candidate_schema import audio_analysis_response_schema" in src
    assert "def _audio_structured_output_enabled" in src
    assert "AUDIO_ANALYSIS_STRUCTURED" in src
    assert "response_mime_type=\"application/json\"" in src
    assert "response_schema=audio_analysis_response_schema()" in src
    assert "retry legacy JSON config" in src


def test_gemini_analyze_generate_block_has_quota_overload_passthrough():
    src = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Lightweight structural check: the fallback path must not swallow quota/503.
    assert "if is_quota_error(_schema_err) or is_overload_error(_schema_err):" in src
    assert "raise" in src[src.find("if is_quota_error(_schema_err)"):src.find("logger.warning", src.find("if is_quota_error(_schema_err)"))]
