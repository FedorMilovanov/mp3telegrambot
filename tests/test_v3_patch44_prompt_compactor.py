"""Regression tests for v3 patch 44 — conservative runtime prompt compaction."""

from pathlib import Path

from core.prompt_compactor import compact_prompt_for_generation


def test_prompt_compactor_dedupes_only_allowlisted_boilerplate():
    prompt = """
A



Верни только чистый JSON, без ```json и без пояснений.
Keep this instruction.
Верни только чистый JSON, без ```json и без пояснений.
Keep this instruction.
"""
    result = compact_prompt_for_generation(prompt)
    assert result.compacted_chars < result.original_chars
    assert result.removed_lines >= 2
    assert result.text.count("Верни только чистый JSON, без ```json и без пояснений.") == 1
    # Non-allowlisted duplicate content is preserved.
    assert result.text.count("Keep this instruction.") == 2


def test_prompt_compactor_is_wired_into_audio_synopsis_and_expanded_requests():
    assert "compact_prompt_for_generation(prompt)" in Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    assert "compact_prompt_for_generation(prompt)" in Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "compact_prompt_for_generation(prompt)" in Path("services/telegraph_pages.py").read_text(encoding="utf-8")


def test_prompt_compactor_does_not_remove_reasoning_guidance():
    prompt = "РАЗРЕШЕНО И ЖЕЛАТЕЛЬНО — REASONING-FIRST\nПроверенный контекст разрешён.\n"
    result = compact_prompt_for_generation(prompt)
    assert "РАЗРЕШЕНО И ЖЕЛАТЕЛЬНО" in result.text
    assert "Проверенный контекст разрешён" in result.text
