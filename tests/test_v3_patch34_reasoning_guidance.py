"""Regression tests for v3 patch 34 — positive reasoning guidance."""

from pathlib import Path

from core.prompts import build_audio_analysis_prompt, STUDY_ANALYSIS_PROMPT, REFLECTION_APPLICATION_PROMPT
from core.reasoning_guidance import build_reasoning_first_block, build_synopsis_reasoning_note


def test_reasoning_guidance_blocks_are_positive_not_ban_wall():
    block = build_reasoning_first_block("study")
    assert "РАЗРЕШЕНО И ЖЕЛАТЕЛЬНО" in block
    assert "материалу\nраскрыться" in block
    assert "Проверенный контекст разрешён" in block
    assert "функцию\nслова в аргументе" in block
    assert "Лестница исследовательской ценности" in block
    assert block.count("НЕ ") <= 1  # the block is intentionally not a ban wall


def test_audio_prompt_puts_reasoning_before_base_principles():
    prompt = build_audio_analysis_prompt("T", "A", "55:00", 3300, mode="deep")
    idx_reasoning = prompt.find("РАЗРЕШЕНО И ЖЕЛАТЕЛЬНО — REASONING-FIRST")
    idx_base = prompt.find("БАЗОВЫЕ ПРИНЦИПЫ")
    assert idx_reasoning != -1
    assert idx_base != -1
    assert idx_reasoning < idx_base


def test_study_reflection_prompts_accept_reasoning_guidance_via_synopsis_context():
    study_context = build_reasoning_first_block("study")
    study = STUDY_ANALYSIS_PROMPT.format(
        title="T", author="A", duration="50:00", format_name="sermon",
        hermeneutic_method="expository", main_topic="m", analysis_summary="a",
        argument_arc="arc", key_categories="k", timestamps="0:00 t",
        concepts="c", scripture="s", translations="tr", lexicon_notes="l",
        synopsis_context=study_context, source_pack="sources",
    )
    assert "РАЗРЕШЕНО И ЖЕЛАТЕЛЬНО" in study
    assert "исследовательские линзы" in study

    reflection_context = build_reasoning_first_block("reflection")
    reflection = REFLECTION_APPLICATION_PROMPT.format(
        title="T", author="A", duration="50:00", hermeneutic_method="mixed",
        main_topic="m", analysis_summary="a", argument_arc="arc",
        key_categories="k", questions_block="- q", timestamps_block="- 0:00 t",
        synopsis_context=reflection_context,
    )
    assert "пастырский нерв" in reflection
    assert "Лестница пастырского применения" in reflection


def test_synopsis_uses_central_reasoning_note_and_telegraph_pages_injects_blocks():
    telegraph = Path("services/telegraph.py").read_text(encoding="utf-8")
    pages = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "from core.reasoning_guidance import build_synopsis_reasoning_note" in telegraph
    assert "SYNOPSIS_REASONING_FIRST_NOTE = build_synopsis_reasoning_note()" in telegraph
    assert "prompt = SYNOPSIS_REASONING_FIRST_NOTE" in telegraph
    assert "from core.reasoning_guidance import build_reasoning_first_block" in pages
    assert "build_reasoning_first_block(\"study\")" in pages
    assert "build_reasoning_first_block(\"reflection\")" in pages
    assert build_synopsis_reasoning_note().startswith("%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%")
