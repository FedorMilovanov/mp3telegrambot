"""AUDIT R13 (2026-07-06, замечание оператора): default-injection доктрин.

7/13 и 9/13 дампов «Разбора материала» из ОДНОГО прогона (разные проповедники,
разные темы — молитва, страх Божий, видение Христа в Откровении) содержали
«Спасение господством» / «Лёгкое верие». Причина: STUDY_ANALYSIS_PROMPT
использовал именно эту пару терминов как ЕДИНСТВЕННЫЙ рабочий пример формата
калек и повторял её 9 раз по всему файлу — модель pattern-match'ила на самый
частый пример вместо того, чтобы извлекать термины из конкретной проповеди.

Фикс: явный анти-дефолт сразу после образца + разнообразие illustративных
примеров, чтобы ни одна пара терминов не доминировала в промте.
"""
from core.prompts import STUDY_ANALYSIS_PROMPT, REFLECTION_APPLICATION_PROMPT


def _count(haystack: str, needle: str) -> int:
    return haystack.count(needle)


def test_anti_default_warning_present_after_calque_example():
    assert "ОБРАЗЦЫ ФОРМАТА, а не чек-лист" in STUDY_ANALYSIS_PROMPT
    assert "ЗАПРЕЩЁН как автовыбор" in STUDY_ANALYSIS_PROMPT


def test_term_pair_only_appears_inside_anti_default_framing():
    """Раньше пара встречалась 9 раз как НЕЙТРАЛЬНЫЙ пример формата — теперь
    каждое упоминание обёрнуто явной оговоркой «не по умолчанию» / «только
    если проповедь предметно спорит» / это часть анти-дефолт-инструкции."""
    lordship_count = _count(STUDY_ANALYSIS_PROMPT, "Lordship Salvation")
    easy_count = _count(STUDY_ANALYSIS_PROMPT, "Easy Believism")
    # осталось немного мест: диверсифицированный список (1) + сама
    # анти-дефолт-инструкция, которая обязана НАЗВАТЬ термин, чтобы его запретить
    assert lordship_count <= 5, f"Lordship Salvation всё ещё повторяется {lordship_count} раз"
    assert easy_count <= 5, f"Easy Believism всё ещё повторяется {easy_count} раз"
    # единственное упоминание вне анти-дефолт-блока обязано быть условным
    assert "берётся ТОЛЬКО если проповедь предметно спорит" in STUDY_ANALYSIS_PROMPT


def test_final_selfcheck_flags_lordship_easy_believism_default():
    assert "Lordship Salvation / Easy Believism" in STUDY_ANALYSIS_PROMPT
    assert "предметно спорит именно об этом" in STUDY_ANALYSIS_PROMPT


def test_diversified_examples_present():
    """Новые альтернативные примеры введены, чтобы разбавить якорь."""
    assert "Cheap Grace" in STUDY_ANALYSIS_PROMPT
    assert "Carnal Christian" in STUDY_ANALYSIS_PROMPT


def test_reflection_prompt_frames_calque_as_conditional():
    assert "не вставляй калькированные" in REFLECTION_APPLICATION_PROMPT
    # старая жёсткая пара как единственный образец убрана
    assert "**«Лёгкое верие»** (**Easy Believism**), **«Спасение господством»**" not in REFLECTION_APPLICATION_PROMPT


def test_counterexample_definition_rule_diversified():
    """❌-пример нарушения «определение через контрпример» больше не
    использует ту же пару терминов, что и калька-пример."""
    assert "Полная испорченность — это против самоуверенности человека" in STUDY_ANALYSIS_PROMPT
