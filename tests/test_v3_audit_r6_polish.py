"""AUDIT R6 (2026-07-05): дочистка после R5.

Covers: content_audit больше не создаёт запрещённые обёртки; DRY правил
таймкодов (единый источник в prompt_rules); схема квиза требует ровно 4
варианта; content = краткий дайджест при blocks (экономия output-токенов).
"""
from core import prompts as P
from core.prompt_rules import INLINE_TIMESTAMP_RULES, QA_TIMESTAMP_RULE


def test_style_fixes_do_not_create_banned_wrappers():
    """Рерайт «В материале говорится» → «Автор говорит» сам создавал
    third-person wrapper, который следом вычищал другой скраббер."""
    from core.content_audit import _scrub_prompt_context_leaks

    out, changed = _scrub_prompt_context_leaks("В материале говорится, что Бог свят.")
    assert changed and "Автор говорит" not in out
    assert out.startswith("Говорится")

    out2, _ = _scrub_prompt_context_leaks("Материал показывает связь покаяния и веры.")
    assert "Автор показывает" not in out2
    assert out2.startswith("Показывается")

    out3, _ = _scrub_prompt_context_leaks("Материал критикует лёгкое верие.")
    assert "Автор критикует" not in out3
    assert out3.startswith("Критикуется")


def test_inline_timestamp_rules_single_source_of_truth():
    """Ручные копии в STUDY/REFLECTION заменены плейсхолдером — правило
    живёт только в prompt_rules и раскрывается при импорте."""
    # обогащённая строка есть в общем правиле…
    assert "НИКОГДА не ставь таймкод в начале строки с тире" in INLINE_TIMESTAMP_RULES
    # …и ровно по одному разу в каждом развёрнутом промпте
    for prompt in (P.STUDY_ANALYSIS_PROMPT, P.REFLECTION_APPLICATION_PROMPT):
        assert prompt.count("ПРАВИЛО INLINE-ТАЙМКОДОВ") == 1
        assert "НИКОГДА не ставь таймкод в начале строки с тире" in prompt
        assert "{INLINE_TIMESTAMP_RULES}" not in prompt


def test_qa_timestamp_rule_wired_into_qa_prompt():
    assert "перепроверь принадлежность" in QA_TIMESTAMP_RULE
    assert "перепроверь принадлежность" in P.SYNOPSIS_PROMPT_QA
    assert "{QA_TIMESTAMP_RULE}" not in P.SYNOPSIS_PROMPT_QA
    assert P.SYNOPSIS_PROMPT_QA.count("ПРАВИЛО ТАЙМКОДОВ ОТНОСИТЕЛЬНО СЕКЦИИ") == 1


def test_quiz_schema_requires_exactly_four_options():
    from services.quiz_generator import quiz_response_schema

    schema = quiz_response_schema()
    opts = schema["items"]["properties"]["options"]
    assert opts["minItems"] == 4 and opts["maxItems"] == 4


def test_study_content_is_short_digest_when_blocks_used():
    """Рендерер игнорирует content при валидных blocks — полное дублирование
    было чистой тратой output-токенов и множителем MAX_TOKENS-обрезаний."""
    assert "ЭКОНОМИЯ ВЫВОДА" in P.STUDY_ANALYSIS_PROMPT
    assert "КОРОТКИЙ дайджест" in P.STUDY_ANALYSIS_PROMPT
