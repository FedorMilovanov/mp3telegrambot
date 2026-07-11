"""Regression tests for R45 — live-screenshot findings on Reflection pages.

  1. Reflection prompt explicitly bans Study-only ❌/✅ pair-cards (twice), but
     a live page showed the model reproducing that exact format anyway
     ("❌ Подмена: ..." / "✅ Ответ ортодоксальной церкви."). Prompt compliance
     alone was unreliable, so a deterministic backstop now strips the emoji
     markers from Reflection content (text preserved) while leaving Study
     Analysis — where the format is legitimate — untouched.
  2. TYPE 4 (Практика) generated an overly rigid/legalistic step that treated
     ordinary rhetorical exaggeration ("сто раз говорил") as the literal "sin
     of lying" requiring immediate public confession. Added guidance so the
     model doesn't generalize a sermon's specific sin onto normal speech
     patterns with a disproportionate reaction.
"""

from pathlib import Path

from core.content_audit import audit_expanded_sections
from core.prompts import REFLECTION_APPLICATION_PROMPT


def test_r45_reflection_strips_forbidden_pair_markers():
    sections = [{
        "title": "Предупреждения и духовные контрасты",
        "content": (
            "Религиозный прагматизм ❌ Подмена: решение вместо возрождения "
            "(decisional regeneration). Представление о том, что произнесение "
            "молитвы гарантирует спасение без изменения сердца.\n\n"
            "• ✅ Ответ ортодоксальной церкви. Спасение совершается Духом Святым."
        ),
    }]
    out_sections, _, issues = audit_expanded_sections(sections, [], label="ReflectionApplication")
    content = out_sections[0]["content"]
    assert "❌" not in content
    assert "✅" not in content
    # text is fully preserved, only the emoji markers are gone
    assert "Подмена: решение вместо возрождения" in content
    assert "Ответ ортодоксальной церкви" in content
    assert any(i.code == "reflection_forbidden_marker_scrubbed" for i in issues)


def test_r45_study_analysis_pair_cards_untouched():
    # The ❌/✅ format is legitimate for Study Analysis SECTION TYPE 6 — must
    # NOT be scrubbed there.
    sections = [{
        "title": "Заблуждения и ответ ортодоксии",
        "content": "❌ **Пелагианство**\nОписание.\n\n✅ **Ответ ортодоксальной церкви.**\nОпровержение.",
    }]
    out_sections, _, issues = audit_expanded_sections(sections, [], label="StudyAnalysis")
    content = out_sections[0]["content"]
    assert "❌ **Пелагианство**" in content
    assert "✅ **Ответ ортодоксальной церкви.**" in content
    assert not any(i.code == "reflection_forbidden_marker_scrubbed" for i in issues)


def test_r45_scrubber_is_noop_without_markers():
    sections = [{"title": "Обычный раздел", "content": "Простой текст без эмодзи-маркеров."}]
    out_sections, _, issues = audit_expanded_sections(sections, [], label="ReflectionApplication")
    assert out_sections[0]["content"] == "Простой текст без эмодзи-маркеров."
    assert not any(i.code == "reflection_forbidden_marker_scrubbed" for i in issues)


def test_r45_type4_warns_against_treating_hyperbole_as_lying():
    assert "гипербола как приём речи" in REFLECTION_APPLICATION_PROMPT
    assert "НЕ ПЕРЕНОСИ ЧУЖОЙ ГРЕХ НА ОБЫЧНУЮ РЕЧЬ" in REFLECTION_APPLICATION_PROMPT
    # anchored inside TYPE 4 (Практика), not some unrelated section
    i_type4 = REFLECTION_APPLICATION_PROMPT.index("TYPE 4 — ПРАКТИКА")
    i_warning = REFLECTION_APPLICATION_PROMPT.index("НЕ ПЕРЕНОСИ ЧУЖОЙ ГРЕХ")
    i_type5 = REFLECTION_APPLICATION_PROMPT.index("TYPE 5 — ОТНОШЕНИЯ")
    assert i_type4 < i_warning < i_type5
