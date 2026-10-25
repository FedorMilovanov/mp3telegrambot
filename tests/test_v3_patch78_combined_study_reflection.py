"""Regression tests for v3 patch 78 — optional combined Study+Reflection path."""

import ast
import inspect
from pathlib import Path

from core.candidate_schema import combined_expanded_pages_response_schema
from services.telegraph_pages import (
    _gemini_text_request,
    _parse_combined_expanded_json,
    combined_study_reflection_enabled,
)


def test_combined_expanded_pages_schema_keeps_two_independent_pages():
    schema = combined_expanded_pages_response_schema()
    assert schema["required"] == ["study", "reflection"]
    for key in ("study", "reflection"):
        page = schema["properties"][key]
        assert "outline" in page["properties"]
        assert "sections" in page["properties"]
        section_props = page["properties"]["sections"]["items"]["properties"]
        assert "content" in section_props
        assert "blocks" in section_props


def test_parse_combined_expanded_json_extracts_both_pages():
    raw = """
    ```json
    {
      "study": {
        "outline": [{"title":"Study A","time":""}],
        "sections": [{"title":"Study A","time":"","content":"study body"}]
      },
      "reflection": {
        "outline": [{"title":"Reflection A","time":""}],
        "sections": [{"title":"Reflection A","time":"","content":"reflection body"}]
      }
    }
    ```
    """
    parsed = _parse_combined_expanded_json(raw)
    assert parsed is not None
    study_outline, study_sections = parsed["study"]
    reflection_outline, reflection_sections = parsed["reflection"]
    assert study_outline[0]["title"] == "Study A"
    assert study_sections[0]["content"] == "study body"
    assert reflection_outline[0]["title"] == "Reflection A"
    assert reflection_sections[0]["content"] == "reflection body"


def test_combined_study_reflection_is_opt_in(monkeypatch):
    monkeypatch.delenv("COMBINE_STUDY_REFLECTION", raising=False)
    assert combined_study_reflection_enabled() is False
    monkeypatch.setenv("COMBINE_STUDY_REFLECTION", "1")
    assert combined_study_reflection_enabled() is True


def test_render_stage_combined_path_is_feature_flagged_and_fallbacks_to_separate():
    """Объединённый режим — opt-in, и он откатывается на раздельные вызовы.

    После выноса стадии рендера (P0-3) решение живёт в
    `pipelines/telegraph_render.py`; фича-флаг по-прежнему читает пайплайн,
    поэтому проверяются оба файла.
    """
    pipeline = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "combined_study_reflection_enabled" in pipeline
    assert "render_pages(" in pipeline

    stage = Path("pipelines/telegraph_render.py").read_text(encoding="utf-8")
    assert "COMBINE_STUDY_REFLECTION=1" in stage
    assert "if not study" in stage
    assert "if not reflection" in stage


def test_combined_and_separate_semantic_paths_forbid_model_downgrade():
    parameter = inspect.signature(_gemini_text_request).parameters["allow_model_fallback"]
    assert parameter.default is False

    function_source = inspect.getsource(_gemini_text_request)
    tree = ast.parse(function_source)
    executable_strings = [
        node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]

    # Historical comments may name retired models, but executable policy must not.
    assert not any("gemini-3.1-flash-lite" in value for value in executable_strings)
    assert "model_name = GEMINI_MODEL" in function_source
    assert "gemini_generate(" in function_source
    assert "model_name=model_name" in function_source
    assert "_models.append" not in function_source
    assert "strict semantic quality policy" in function_source

    src = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "allow_model_fallback=False" in src


def test_combined_result_is_typed_dataclass_not_bare_tuple():
    src = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "@dataclass(frozen=True)" in src
    assert "class CombinedStudyReflectionResult" in src
    assert 'study_page_type: str = "study"' in src
    assert 'reflection_page_type: str = "reflection"' in src
    assert "return CombinedStudyReflectionResult" in src
    stage = Path("pipelines/telegraph_render.py").read_text(encoding="utf-8")
    assert "result.study_url" in stage
    assert "result.reflection_url" in stage


def test_structured_block_schema_uses_canonical_type_enum():
    schema = combined_expanded_pages_response_schema()
    block_type = schema["properties"]["study"]["properties"]["sections"]["items"]["properties"]["blocks"]["items"][
        "properties"
    ]["type"]
    assert block_type["enum"][:6] == ["paragraph", "bullet", "scripture", "source", "lexicon", "heading"]
    assert "application" in block_type["enum"]
