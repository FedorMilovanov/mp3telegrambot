"""Regression tests for v3 patch 43 — prompt health diagnostics."""

from pathlib import Path

from core.prompt_health import analyze_prompt_text, collect_prompt_health, format_prompt_health_report


def test_prompt_health_analyzer_counts_markers_and_ratio():
    item = analyze_prompt_text("x", "РАЗРЕШЕНО делать. НЕ делай плохо. ФИНАЛЬНАЯ ПРОВЕРКА")
    assert item.chars > 0
    assert item.positive_markers >= 1
    assert item.negative_markers >= 1
    assert item.final_check_blocks == 1
    assert item.compacted_chars <= item.chars
    assert item.negative_to_positive_ratio >= 0


def test_prompt_health_collects_main_prompts_and_audio_sample():
    items = collect_prompt_health()
    names = {i.name for i in items}
    assert "SYNOPSIS_PROMPT_V2" in names
    assert "STUDY_ANALYSIS_PROMPT" in names
    assert "REFLECTION_APPLICATION_PROMPT" in names
    assert "AUDIO_ANALYSIS_PROMPT(deep sample)" in names
    assert "QUIZ_PROMPT" in names
    assert all(i.chars > 1000 for i in items)


def test_prompt_health_report_is_html_admin_readout():
    report = format_prompt_health_report()
    assert "Prompt health" in report
    assert "Total chars" in report
    assert "ratio" in report
    assert "compact" in report
    assert "saved" in report
    assert "production guardrails" in report


def test_prompthealth_command_is_registered():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert "from core.prompt_health import format_prompt_health_report" in commands
    assert "async def prompthealth_command" in commands
    assert "format_prompt_health_report" in commands
    assert 'CommandHandler("prompthealth"' in main


def test_prompt_health_detects_leaky_literal_patterns():
    from core.prompt_health import find_leaky_prompt_literals

    text = "PRIVATE: позиция канала. Формат: В работе X автор Y утверждает. Лоусон показывает."
    leaks = find_leaky_prompt_literals(text)
    assert "позиция канала" in leaks
    assert "В работе X автор Y" in leaks
    assert "Лоусон показывает" in leaks


def test_prompt_health_current_prompts_have_no_known_leaky_literals():
    items = collect_prompt_health()
    offenders = {i.name: i.leaky_literals for i in items if i.leaky_literals}
    assert offenders == {}


def test_quiz_prompt_has_final_self_check():
    items = {i.name: i for i in collect_prompt_health()}
    assert items["QUIZ_PROMPT"].final_check_blocks == 1


def test_prompt_health_report_includes_leak_counter():
    report = format_prompt_health_report()
    assert "leaks=" in report
    assert "leaky_literals:" not in report
