"""Regression tests for v3 patch 83 — prompt alignment and QA transcript fidelity."""

from core.content_audit import audit_expanded_sections
from core.prompts import SYNOPSIS_PROMPT_QA, STUDY_ANALYSIS_PROMPT


def test_study_prompt_source_cards_match_title_first_renderer_policy():
    assert "title-first" in STUDY_ANALYSIS_PROMPT
    assert "**Умерщвление греха**, Джон Оуэн" in STUDY_ANALYSIS_PROMPT
    assert "**Safe in the Arms of God**, Джон МакАртур" in STUDY_ANALYSIS_PROMPT
    assert "автор и название — ОБЫЧНЫМ текстом" not in STUDY_ANALYSIS_PROMPT
    assert "НЕ жирным" not in STUDY_ANALYSIS_PROMPT.split("Карта источников", 1)[-1][:500]


def test_study_prompt_private_guidance_never_publish_channel_position():
    assert "PRIVATE GUIDANCE — НЕ ДЛЯ ПУБЛИКАЦИИ" in STUDY_ANALYSIS_PROMPT
    assert "Никогда не пересказывай её в публичном тексте" in STUDY_ANALYSIS_PROMPT
    assert "не к каналу" in STUDY_ANALYSIS_PROMPT


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
