"""AUDIT R5 (2026-07-05): prompt-engineering + Gemini 3.5 Flash alignment.

Covers: model migration (July 2026 shutdowns), bracketed-timestamp examples,
TYPE-6 leakage into synopsis prompts, author-catalog cut, block-field
contracts, synopsis grounding for Study/Reflection, thinking-overflow retry,
quiz prompt/validator alignment, prompt_health coverage.
"""
from pathlib import Path

from core import prompts as P


def test_no_dying_models_in_default_chains():
    """gemini-3.1-flash-lite-preview выключен 09.07.2026,
    gemini-2.5-flash-lite — ~22.07.2026."""
    for f in ("services/gemini_analyze.py", "services/telegraph.py",
              "services/telegraph_pages.py", "services/livedub_info.py"):
        src = Path(f).read_text(encoding="utf-8")
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "выключа" in line:
                continue
            assert '"gemini-2.5-flash-lite"' not in line, f"{f}: {line.strip()}"
            assert "3.1-flash-lite-preview" not in line, f"{f}: {line.strip()}"


def test_reflection_examples_use_canonical_timestamp_format():
    """Промпт сам учил формату [⏱ M:SS], который тут же запрещал и который
    постпроцессор вынужден был вычищать."""
    assert "[⏱" not in P.REFLECTION_APPLICATION_PROMPT
    assert "⏱ **M:SS**" in P.REFLECTION_APPLICATION_PROMPT


def test_type6_not_leaked_into_synopsis_prompts():
    """TYPE 6 — секция Study; в конспект-промптах она только путала модель."""
    assert "TYPE 6" not in P.SYNOPSIS_PROMPT_V2
    assert "TYPE 6" not in P.SYNOPSIS_PROMPT_QA
    assert "СТРУКТУРА STUDY" not in P.SYNOPSIS_PROMPT_V2
    assert "СТРУКТУРА REFLECTION" not in P.SYNOPSIS_PROMPT_V2


def test_verbatim_prompt_has_inline_timestamp_ordering_rule():
    """Дефолтный промпт конспекта — единственный, где не было правила,
    по которому его вывод аудитится (inline_timestamp_before_section)."""
    assert "по возрастанию" in P.SYNOPSIS_VERBATIM_PROMPT
    assert "РАНЬШЕ времени начала секции" in P.SYNOPSIS_VERBATIM_PROMPT


def test_study_catalog_cut_and_contracts_added():
    src = Path("core/prompts.py").read_text(encoding="utf-8")
    assert "AUTHORS_REFERENCE" not in src, "мёртвый дубликат каталога удалён"
    assert len(P.STUDY_ANALYSIS_PROMPT) < 60000, "каталог уровня 2 сокращён"
    assert "{source_pack}" in P.STUDY_ANALYSIS_PROMPT
    assert "КОНТРАКТ ПОЛЕЙ БЛОКОВ" in P.STUDY_ANALYSIS_PROMPT
    assert "role_in_argument" in P.STUDY_ANALYSIS_PROMPT
    assert "common_misreading" in P.STUDY_ANALYSIS_PROMPT
    assert "why_relevant" in P.STUDY_ANALYSIS_PROMPT
    # Guardrail Израиль/Церковь пережил сокращение
    assert "supersessionism" in P.STUDY_ANALYSIS_PROMPT
    assert "Церковь ≠ Израиль" in P.STUDY_ANALYSIS_PROMPT


def test_reflection_application_contract_added():
    assert "КОНТРАКТ БЛОКА application" in P.REFLECTION_APPLICATION_PROMPT
    assert "anchor_timestamp" in P.REFLECTION_APPLICATION_PROMPT
    assert "concrete_step" in P.REFLECTION_APPLICATION_PROMPT


def test_simple_prompt_shows_valid_json():
    src = Path("services/telegraph.py").read_text(encoding="utf-8")
    idx = src.find("simple_prompt = (")
    assert idx != -1
    block = src[idx:idx + 700]
    assert "'{{" not in block and "}}'" not in block, \
        "не format-строка: двойные скобки показывали модели невалидный JSON"


def test_synopsis_grounding_flows_to_study_and_reflection():
    """Study/Reflection — text-only вызовы; verbatim-стенограмма конспекта
    теперь передаётся как источник цитат (главная защита от выдуманных цитат)."""
    tg = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert '_synopsis_grounding"] = ' in tg.replace("ai_data[\"", '"')
    pages = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert pages.count('_ai.get("_synopsis_grounding")') >= 2, "study + reflection"
    assert "ФРАГМЕНТЫ ДОСЛОВНОЙ СТЕНОГРАММЫ" in pages
    db = Path("core/database.py").read_text(encoding="utf-8")
    assert 'k.startswith("_")' in db, "приватные ключи не персистятся в кэш"


def test_gemini_thinking_overflow_retry():
    """thinking-токены делят бюджет с max_output_tokens: MAX_TOKENS/пустой
    ответ раньше означал ПОЛНУЮ потерю анализа."""
    src = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    assert "_retry_low_thinking" in src
    assert 'thinking_level="low"' in src


def test_quiz_prompt_matches_validator_rules():
    from services.quiz_generator import QUIZ_PROMPT
    assert "минимум из 2 значимых слов" in QUIZ_PROMPT
    assert "8 символов" in QUIZ_PROMPT


def test_prompt_health_monitors_default_synopsis_prompt():
    from core.prompt_health import collect_prompt_health
    items = collect_prompt_health()
    names = [i.name for i in items]
    assert "SYNOPSIS_VERBATIM_PROMPT" in names
    for i in items:
        assert not i.leaky_literals, f"{i.name}: leaky {i.leaky_literals}"


def test_no_named_copyable_examples_left():
    """AGENTS.md: pattern-level wording вместо буквальных примеров с именами."""
    for prompt in (P.SYNOPSIS_PROMPT_V2, P.REFLECTION_APPLICATION_PROMPT):
        assert "Вошер описывает" not in prompt
        assert "о чём говорил Вошер" not in prompt
        assert "как молился Мюллер" not in prompt
    from core.prompt_rules import FEW_SHOT_FIRST_SECTION
    assert "МакАртур рассматривает" not in FEW_SHOT_FIRST_SECTION
