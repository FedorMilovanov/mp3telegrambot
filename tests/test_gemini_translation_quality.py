from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "tools" / "voxcpm2" / "generic_short_runtime.py"
EXPRESSIVE = ROOT / "tools" / "voxcpm2" / "expressive_translation.py"
GEMINI_ENTRY = ROOT / "tools" / "voxcpm2" / "generic_clean_gemini_runtime.py"
WIZARD = ROOT / "handlers" / "dub_wizard.py"


def _source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    ast.parse(source)
    return source


def test_current_stable_models_have_separate_roles() -> None:
    wizard = _source(WIZARD)
    assert '"gemini-3.6-flash"' in wizard
    assert '"gemini-3.5-flash-lite"' in wizard
    request_section = wizard[
        wizard.index("def _request_payload"):
        wizard.index("async def _admin")
    ]
    assert '"translation_model": os.getenv("DUB_TRANSLATION_MODEL", "gemini-3.6-flash")' in request_section
    assert '"title_model": os.getenv("DUB_TITLE_MODEL", "gemini-3.5-flash-lite")' in request_section


def test_translation_keeps_high_thinking_in_primary_and_fallback_config() -> None:
    runtime = _source(RUNTIME)
    gemini_section = runtime[
        runtime.index("def gemini_json"):
        runtime.index("def install_runtime_adapters")
    ]
    assert 'thinking_level="high"' in gemini_section
    assert "types.ThinkingConfig" in gemini_section
    assert 'response_mime_type="application/json"' in gemini_section
    assert "max_output_tokens=16000" in gemini_section
    assert "temperature=" not in gemini_section
    assert "top_p=" not in gemini_section
    assert "top_k=" not in gemini_section


def test_expressive_translation_really_runs_three_editorial_passes() -> None:
    source = _source(EXPRESSIVE)
    assert "draft = _validate(_gemini(draft_prompt, model_name), groups)" in source
    assert "faithful = _validate(_gemini(fidelity_prompt, model_name), groups)" in source
    assert "final = _validate(_gemini(performance_prompt, model_name), groups)" in source
    assert "намеренные повторы" in source
    assert "риторические вопросы" in source
    assert "богословский термин" in source
    assert "не выше примерно" in source


def test_clean_gemini_route_uses_expressive_translator_and_key_pool() -> None:
    entry = _source(GEMINI_ENTRY)
    runtime = _source(RUNTIME)
    assert "production.translate_groups_max = expressive_translation.translate_groups" in entry
    assert "hardened.pipeline.gemini_json = hardened.gemini_json" in entry
    assert "GEMINI_CLIENTS" in runtime
    assert "GEMINI_API_KEY_4" in runtime
