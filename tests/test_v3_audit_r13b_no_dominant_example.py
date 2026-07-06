"""AUDIT R13b (2026-07-06): структурный guard против few-shot anchoring.

R13 нашёл, что «Спасение господством»/«Лёгкое верие» повторялись 9 раз как
единственный рабочий пример калек — модель pattern-match'ила на самый
частый пример вместо извлечения терминов из конкретной проповеди (см.
AGENTS.md, «No default-vocabulary injection rule»).

Этот тест не проверяет конкретную пару терминов (это делает
test_v3_audit_r13_term_anchoring.py) — он проверяет ОБЩИЙ паттерн: ни одна
Title-Case англоязычная богословская фраза не должна повторяться в
STUDY_ANALYSIS_PROMPT больше 5 раз, чтобы не создать следующий такой же баг.
"""
import re
from collections import Counter

from core.prompts import STUDY_ANALYSIS_PROMPT, REFLECTION_APPLICATION_PROMPT

_TITLE_CASE_PHRASE_RE = re.compile(
    r'\b[A-Z][a-z]+(?:\s(?:of|the|and)?\s?[A-Z][a-z]+){1,3}\b'
)

# Общие служебные ярлыки (не богословские термины) — не считаются.
_NOT_A_TERM = {"Original Title", "Original Author"}

# Известные термины ниже уже проверены вручную в R13: они мех-примеры
# форматирования скобок/пробелов (не «какой термин выбрать»), не «что писать».
_MECHANICAL_EXAMPLE_ALLOWLIST = {"Total Depravity"}


def test_no_theological_term_dominates_study_prompt():
    counts = Counter(
        m for m in _TITLE_CASE_PHRASE_RE.findall(STUDY_ANALYSIS_PROMPT)
        if m not in _NOT_A_TERM
    )
    offenders = {
        term: n for term, n in counts.items()
        if n > 5 and term not in _MECHANICAL_EXAMPLE_ALLOWLIST
    }
    assert not offenders, (
        f"термин(ы) повторяются как доминирующий пример: {offenders} — "
        "риск few-shot anchoring (см. AGENTS.md 'No default-vocabulary injection rule')"
    )


def test_no_theological_term_dominates_reflection_prompt():
    counts = Counter(
        m for m in _TITLE_CASE_PHRASE_RE.findall(REFLECTION_APPLICATION_PROMPT)
        if m not in _NOT_A_TERM
    )
    offenders = {term: n for term, n in counts.items() if n > 5}
    assert not offenders, f"термин(ы) повторяются как доминирующий пример: {offenders}"


def test_agents_md_documents_the_anchoring_rule():
    from pathlib import Path
    agents = Path(__file__).resolve().parents[1].joinpath("AGENTS.md").read_text(encoding="utf-8")
    assert "No default-vocabulary injection rule" in agents
    assert "few-shot anchoring" in agents
