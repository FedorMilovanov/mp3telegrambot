"""Tests for v3 patch 9 — THIRD_PERSON_BAN in Study + QA title must be a question."""
import re


def test_study_analysis_has_third_person_ban():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    assert "ЗАПРЕТ ОТ ТРЕТЬЕГО ЛИЦА" in STUDY_ANALYSIS_PROMPT


def test_study_analysis_no_raw_placeholders():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    assert not re.findall(r"\{[A-Z_]+\}", STUDY_ANALYSIS_PROMPT)


def test_study_analysis_substantive_but_not_bloated():
    """AUDIT R5: 12К-знаковый статический каталог авторов заменён компактным
    ядром + тематическим {source_pack} — гардируем и низ (содержательность),
    и верх (диета: Gemini 3.x хуже следует раздутым промптам)."""
    from core.prompts import STUDY_ANALYSIS_PROMPT
    assert len(STUDY_ANALYSIS_PROMPT) > 40000
    assert len(STUDY_ANALYSIS_PROMPT) < 60000


def test_qa_title_requires_question_mark():
    from core.prompts import SYNOPSIS_PROMPT_QA
    assert "ОБЯЗАТЕЛЬНО со знаком вопроса (?)" in SYNOPSIS_PROMPT_QA


def test_qa_title_has_bad_good_examples():
    from core.prompts import SYNOPSIS_PROMPT_QA
    assert "❌ Плохо: «Когнитивная польза" in SYNOPSIS_PROMPT_QA
    assert "✅ Хорошо: «Почему вы пишете от руки" in SYNOPSIS_PROMPT_QA


def test_all_prompts_no_shared_placeholders():
    from core.prompts import (SYNOPSIS_PROMPT_V2, SYNOPSIS_PROMPT_QA,
        STUDY_ANALYSIS_PROMPT, REFLECTION_APPLICATION_PROMPT)
    shared = re.compile(
        r"\{(INLINE_TIMESTAMP_RULES|STRICT_BANS|STRICT_BANS_STUDY"
        r"|QA_TIMESTAMP_RULE|AUTHOR_NAMING_RULE|JSON_OUTPUT_RULE"
        r"|SECTION_TITLE_RULE|THIRD_PERSON_BAN|FEW_SHOT_FIRST_SECTION)\}"
    )
    for name, p in [("V2", SYNOPSIS_PROMPT_V2), ("QA", SYNOPSIS_PROMPT_QA),
                    ("STUDY", STUDY_ANALYSIS_PROMPT), ("REFL", REFLECTION_APPLICATION_PROMPT)]:
        found = shared.findall(p)
        assert not found, f"{name}: unresolved {found}"


def test_v2_reflection_study_all_have_third_person_ban():
    from core.prompts import (SYNOPSIS_PROMPT_V2, STUDY_ANALYSIS_PROMPT,
                               REFLECTION_APPLICATION_PROMPT)
    for name, p in [("V2", SYNOPSIS_PROMPT_V2), ("STUDY", STUDY_ANALYSIS_PROMPT),
                    ("REFL", REFLECTION_APPLICATION_PROMPT)]:
        assert "ЗАПРЕТ ОТ ТРЕТЬЕГО ЛИЦА" in p, f"{name} missing THIRD_PERSON_BAN"

