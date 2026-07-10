import inspect
from pathlib import Path

from core.candidate_schema import (
    clips_response_schema,
    extras_response_schema,
    shorts_response_schema,
    structured_json_config_kwargs,
)
from core.globals import make_audio_config, make_text_config_smart


def test_candidate_response_schemas_have_required_top_level_keys():
    shorts = shorts_response_schema()
    clips = clips_response_schema()
    extras = extras_response_schema()
    assert shorts["required"] == ["shorts_candidates"]
    assert clips["required"] == ["clip_candidates"]
    assert "montage_candidates" in extras["properties"]
    assert "highlights" in extras["properties"]


def test_structured_json_config_kwargs_contract():
    schema = shorts_response_schema()
    kwargs = structured_json_config_kwargs(schema)
    assert kwargs["response_mime_type"] == "application/json"
    assert kwargs["response_schema"] is schema
    assert structured_json_config_kwargs(None) == {}


def test_gemini_config_builders_accept_structured_output_kwargs():
    audio_sig = inspect.signature(make_audio_config)
    text_sig = inspect.signature(make_text_config_smart)
    for sig in (audio_sig, text_sig):
        assert "response_mime_type" in sig.parameters
        assert "response_schema" in sig.parameters


def test_candidate_generators_try_structured_output_then_legacy_fallback():
    shorts_source = Path("services/shorts_candidates.py").read_text(encoding="utf-8")
    extras_source = Path("services/render_clips_montage.py").read_text(encoding="utf-8")
    assert "shorts_response_schema()" in shorts_source
    assert "clips_response_schema()" in shorts_source
    # AUDIT R26: legacy-JSON fallback остался, но теперь ГАТИРОВАН классификатором
    # ошибки (только при schema-ошибке, не при quota/overload/timeout).
    assert "retry legacy JSON config" in shorts_source
    assert "classify_gemini_error" in shorts_source
    assert "extras_response_schema()" in extras_source
    assert "retry legacy JSON config" in extras_source
    assert "classify_gemini_error" in extras_source
