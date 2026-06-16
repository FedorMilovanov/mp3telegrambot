"""Regression tests for v3 patch 83 — prompt alignment and QA transcript fidelity."""

from core.content_audit import audit_expanded_sections
from core.prompts import SYNOPSIS_PROMPT_QA, STUDY_ANALYSIS_PROMPT


def test_study_prompt_source_cards_match_title_first_renderer_policy():
    assert "title-first" in STUDY_ANALYSIS_PROMPT
    assert "**Of the Mortification of Sin**, Джон Оуэн" in STUDY_ANALYSIS_PROMPT
    assert "**Safe in the Arms of God**, Джон МакАртур" in STUDY_ANALYSIS_PROMPT
    assert "автор и название — ОБЫЧНЫМ текстом" not in STUDY_ANALYSIS_PROMPT
    assert "НЕ жирным" not in STUDY_ANALYSIS_PROMPT.split("Карта источников", 1)[-1][:500]


def test_study_prompt_private_guidance_never_publish_channel_position():
    assert "PRIVATE GUIDANCE — НЕ ДЛЯ ПУБЛИКАЦИИ" in STUDY_ANALYSIS_PROMPT
    assert "Никогда не пересказывай её в публичном тексте" in STUDY_ANALYSIS_PROMPT
    assert "не к внутренней инструкции" in STUDY_ANALYSIS_PROMPT


def test_qa_synopsis_prompt_is_transcript_like_not_summary():
    rendered = SYNOPSIS_PROMPT_QA.format(
        title="Q&A",
        duration="1:00:00",
        timestamps_block="0:00 Первый вопрос\n10:00 Второй вопрос",
    )
    assert "режим ПОЛНОЙ стенограммы" in rendered
    assert "Не превращай" in rendered and "summary" in rendered
    assert "inline-якорь ⏱ **M:SS**" in rendered
    assert "1–2 короткие дословные фразы автора" in rendered


def test_content_audit_removes_editorial_position_leak_variants():
    sections = [{
        "title": "T",
        "content": "Редакторская позиция канала помогает понять цессационизм. Конфессиональная рамка канала дана для анализа. Основной тезис остаётся.",
    }]
    out, _, issues = audit_expanded_sections(sections, label="StudyAnalysis")
    text = out[0]["content"]
    assert "Редакторская позиция" not in text
    assert "рамка канала" not in text
    assert "Основной тезис" in text
    assert any(i.code == "prompt_context_leak_fixed" for i in issues)


def test_study_prompt_does_not_teach_third_person_wrappers():
    assert "Лоусон показывает" not in STUDY_ANALYSIS_PROMPT
    assert "Бузениц доказывает" not in STUDY_ANALYSIS_PROMPT
    assert "без описания действий автора" in STUDY_ANALYSIS_PROMPT


def test_study_prompt_forbids_unregistered_russian_source_titles():
    assert "если не знаешь устойчивого признанного русского названия" in STUDY_ANALYSIS_PROMPT.lower() or "registry" in STUDY_ANALYSIS_PROMPT
    assert "**Of the Mortification of Sin**, Джон Оуэн" in STUDY_ANALYSIS_PROMPT


def test_study_prompt_source_and_guardrail_wording_avoids_leaky_patterns():
    assert "ПОЗИЦИЯ КАНАЛА" not in STUDY_ANALYSIS_PROMPT
    assert "В работе X автор Y" not in STUDY_ANALYSIS_PROMPT
    assert "Y в «X»" not in STUDY_ANALYSIS_PROMPT
    assert "Русский Автор, *«Русское название»*" not in STUDY_ANALYSIS_PROMPT
    assert "Правильно:   - Джон МакАртур, *Safe in the Arms of God*" not in STUDY_ANALYSIS_PROMPT
    assert "**Safe in the Arms of God**, Джон МакАртур (John MacArthur)" in STUDY_ANALYSIS_PROMPT
    assert "не публиковать как редакционную позицию" in STUDY_ANALYSIS_PROMPT


def test_prompts_do_not_repeat_literal_third_person_bad_examples():
    from core.prompt_rules import THIRD_PERSON_BAN
    assert "МакАртур показывает" not in THIRD_PERSON_BAN
    assert "автор подчеркивает" not in THIRD_PERSON_BAN
    assert "фамилия автора + показывает" in THIRD_PERSON_BAN


def test_study_prompt_removes_literal_channel_position_leaks():
    assert "позиция канала" not in STUDY_ANALYSIS_PROMPT.lower()
    assert "КОНФЕССИОНАЛЬНАЯ РАМКА КАНАЛА" not in STUDY_ANALYSIS_PROMPT
    assert "ВНУТРЕННЯЯ КОНФЕССИОНАЛЬНАЯ РАМКА" in STUDY_ANALYSIS_PROMPT
