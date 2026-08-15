"""Operator-approved quality contract for Synopsis and Study Analysis.

The production prompt is intentionally large and historically fragile.  This module
installs a small, late, idempotent contract before ``services.telegraph_pages``
imports its prompt/schema helpers.  It has three goals:

* preserve Synopsis as a maximally verbatim transcript, including multipart output;
* preserve the established Study-only ❌/✅ orthodoxy pair-card structure;
* replace decorative dictionary cards with verse-first contextual word studies.

No runtime patching is performed and ``SYNOPSIS_PROMPT_V2`` is never rewritten.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_CONTRACT_MARKER = "OPERATOR CONSPECT CONTRACT 2026-07-23"
_DROPPED_LEXICON_SHAPES: set[tuple[str, ...]] = set()


STUDY_OPERATOR_CONTRACT = r"""

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
OPERATOR CONSPECT CONTRACT 2026-07-23 — ПОЗДНЕЕ ПРАВИЛО, ИМЕЕТ ПРИОРИТЕТ
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

A. ГРАНИЦА МЕЖДУ СТРАНИЦАМИ
- «Конспект» уже является максимально дословной стенограммой. НЕ пересказывай его
  на этой странице и НЕ пытайся «улучшать» голос автора.
- Study Analysis добавляет только исследовательскую ценность: определения,
  различения, экзегетические связи, историю доктрины, источники и проверяемые
  языковые наблюдения, реально нужные для ЭТОГО материала.
- Лучше 2 сильных узла, чем 8 общих карточек. Пустой нерелевантный раздел лучше
  искусственно заполненного.

B. КЛЮЧЕВЫЕ ПОНЯТИЯ — НЕ СЛОВАРНЫЙ СПИСОК
- Выбирай 2–5 понятий, только если каждое несёт реальную исследовательскую нагрузку.
- Для каждого понятия последовательно покажи:
  1) на какой богословский вопрос оно отвечает;
  2) что утверждает и что отрицает;
  3) чем отличается от ближайшего похожего, но неверного/неполного понятия;
  4) как именно оно двигает аргумент ЭТОГО материала;
  5) на какой текст Писания, фрагмент речи или таймкод опирается;
  6) что богословски поставлено на кон.
- «Послушание», «смирение», «любовь», «единство» и другие общеизвестные слова
  нельзя превращать в карточки с банальным определением. Включай их только при
  наличии небанального различения, точной формулировки автора или важной связи
  с конкретным текстом.
- Не повторяй одну схему фразами «это подчёркивает...», «это опровергает...»,
  «это показывает...». Каждый блок строится из реальной логики материала.

C. КЛЮЧЕВЫЕ СЛОВА В КОНТЕКСТЕ ПИСАНИЯ — ВМЕСТО СЛОВАРЯ РАДИ СЛОВАРЯ
- Публичный заголовок раздела: «Ключевые слова в контексте Писания».
- Норма: 0–3 блока. Ноль — полноценный и предпочтительный ответ, если точной
  языковой пользы нет.
- Не создавай блок только потому, что в теме встречается богословское понятие.
- Блок допустим лишь когда назван точный стих, известна форма слова именно в
  этом стихе и языковая деталь действительно уточняет понимание текста.
- Не подменяй форму в стихе словарной формой: укажи ОБЕ и различи их.
- Пиши для русскоязычного читателя: обязательно укажи русскую фразу стиха,
  какое русское слово разбирается и как приблизительно прочитать оригинал.
- Строго разделяй четыре уровня: словарное значение; значение в данном стихе;
  использование в аргументе проповеди; применение. Не выдавай применение за
  значение греческого/еврейского слова.
- Обязательно укажи границу вывода: чего одно это слово само по себе НЕ доказывает.
- Если нет точного источника, точного стиха, формы слова, русского контекста или
  полезного смыслового результата — ОПУСТИ блок.

Для structured blocks используй type="word_study" и поля:
scripture_ref, russian_quote, russian_focus, original_form, lemma,
transliteration, russian_pronunciation, grammar, basic_meaning,
meaning_in_context, role_in_argument, limits_of_claim, source,
anchor_timestamp.

D. НЕИЗМЕНЯЕМАЯ СТРУКТУРА SECTION TYPE 6
- Заголовок сохраняй дословно: «Заблуждения и ответ ортодоксии».
- Раздел появляется только при реальном материальном основании, 1–3 пары.
- Каждая пара — ДВА ОТДЕЛЬНЫХ АБЗАЦА, их нельзя склеивать или переименовывать:

**Название богословской проблемы** ❌ **Подмена: название заблуждения.**
Кратко и точно: что подменяется, что учение утверждает и что разрушает.

✅ **Ответ ортодоксальной церкви.**
Конкретный ответ Писания, Собора/Синода или исповедания; при наличии — таймкод.

- Маркеры ❌ и ✅ обязательны именно на Study Analysis. Не переноси этот формат
  в ReflectionApplication и не снимай его со Study Analysis.
- «Ответ ортодоксальной церкви.» — фиксированная формула. Не заменяй её на
  «наша позиция», «редакционный ответ», «правильный взгляд» или иной заголовок.

E. ФИНАЛЬНЫЙ ОТБОР
Перед JSON удали любой блок, который:
- можно было бы без изменений вставить в другую проповедь;
- не имеет опоры в материале, Писании, проверяемом источнике или таймкоде;
- имитирует глубину греческим/еврейским словом, но не углубляет чтение стиха;
- повторяет Конспект вместо добавления исследовательского инструмента.
"""


_WORD_STUDY_FIELDS: tuple[str, ...] = (
    "scripture_ref",
    "russian_quote",
    "russian_focus",
    "original_form",
    "lemma",
    "transliteration",
    "russian_pronunciation",
    "grammar",
    "basic_meaning",
    "meaning_in_context",
    "role_in_argument",
    "limits_of_claim",
    "source",
    "anchor_timestamp",
)

_WORD_STUDY_HARD_REQUIRED: tuple[str, ...] = (
    "scripture_ref",
    "russian_quote",
    "russian_focus",
    "original_form",
    "lemma",
    "transliteration",
    "russian_pronunciation",
    "basic_meaning",
    "meaning_in_context",
    "role_in_argument",
    "limits_of_claim",
    "source",
    "anchor_timestamp",
)


def build_hardened_study_prompt(prompt: str) -> str:
    """Return the Study prompt with the late operator contract applied once."""
    text = str(prompt or "")
    if _CONTRACT_MARKER in text:
        return text

    # Remove two quantity pressures that repeatedly caused filler.  The appended
    # contract remains authoritative even if upstream wording changes later.
    text = text.replace(
        "5–10 карточек. Каждая карточка — отдельный микро-блок, НЕ сливать в поток.",
        "2–5 содержательных карточек. Каждая карточка — отдельный микро-блок; не заполняй раздел ради количества.",
    )
    text = text.replace(
        "SECTION TYPE 3 — ЯЗЫКИ ОРИГИНАЛА И ЛЕКСИКО-СЕМАНТИЧЕСКИЕ УЗЛЫ",
        "SECTION TYPE 3 — КЛЮЧЕВЫЕ СЛОВА В КОНТЕКСТЕ ПИСАНИЯ",
    )
    text = text.replace(
        "- Не больше 3–8 слов\n- Только действительно важные слова",
        "- 0–3 блока; отсутствие блока является нормальным результатом\n- Только действительно важные слова, привязанные к точному стиху",
    )
    return text.rstrip() + STUDY_OPERATOR_CONTRACT


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _ensure_period(value: str) -> str:
    value = _clean(value)
    if not value:
        return ""
    return value if value.endswith((".", "!", "?", "…")) else value + "."


def _word_study_value(raw: dict[str, Any], key: str, *aliases: str) -> str:
    for name in (key, *aliases):
        value = _clean(raw.get(name))
        if value:
            return value
    return ""


def normalize_word_study_block(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a complete word study to an ordinary rich paragraph block.

    Incomplete legacy lexicon cards are dropped instead of being published as
    decorative ``lemma — generic sentence`` entries.  This is a deterministic
    repair, not publication blocking: the rest of the Study page remains intact.
    """
    values = {
        "scripture_ref": _word_study_value(raw, "scripture_ref", "ref"),
        "russian_quote": _word_study_value(raw, "russian_quote", "quote"),
        "russian_focus": _word_study_value(raw, "russian_focus", "focus"),
        "original_form": _word_study_value(raw, "original_form"),
        "lemma": _word_study_value(raw, "lemma"),
        "transliteration": _word_study_value(raw, "transliteration"),
        "russian_pronunciation": _word_study_value(raw, "russian_pronunciation", "pronunciation_ru"),
        "grammar": _word_study_value(raw, "grammar"),
        "basic_meaning": _word_study_value(raw, "basic_meaning", "dictionary_meaning"),
        "meaning_in_context": _word_study_value(raw, "meaning_in_context", "contextual_meaning"),
        "role_in_argument": _word_study_value(raw, "role_in_argument", "why_relevant"),
        "limits_of_claim": _word_study_value(raw, "limits_of_claim"),
        "source": _word_study_value(raw, "source", "source_label"),
        "anchor_timestamp": _word_study_value(raw, "anchor_timestamp", "timestamp"),
    }
    missing = tuple(name for name in _WORD_STUDY_HARD_REQUIRED if not values[name])
    if missing:
        shape = tuple(sorted(missing))
        if shape not in _DROPPED_LEXICON_SHAPES:
            _DROPPED_LEXICON_SHAPES.add(shape)
            logger.warning(
                "Study word-study dropped: missing contextual fields=%s; "
                "decorative lexicon cards are not published",
                ",".join(missing),
            )
        return None

    header = f"**{values['scripture_ref']} — «{values['russian_focus']}»**"
    parts = [
        header,
        _ensure_period(f"Русская фраза стиха: «{values['russian_quote']}»"),
    ]
    form_line = (
        f"В тексте стоит **{values['original_form']}**; словарная форма — "
        f"*{values['lemma']}*. Приблизительно читается «{values['russian_pronunciation']}» "
        f"(*{values['transliteration']}*)"
    )
    if values["grammar"]:
        form_line += f"; грамматически: {values['grammar']}"
    parts.append(_ensure_period(form_line))
    parts.append(_ensure_period(f"Базовое значение: {values['basic_meaning']}"))
    parts.append(_ensure_period(f"В этом стихе: {values['meaning_in_context']}"))
    parts.append(_ensure_period(f"Роль в аргументе материала: {values['role_in_argument']}"))
    parts.append(_ensure_period(f"Граница вывода: {values['limits_of_claim']}"))
    parts.append(
        _ensure_period(
            f"Источник: {values['source']} ⏱ {values['anchor_timestamp']}"
        )
    )
    return {"type": "paragraph", "text": "\n\n".join(parts)}



def install_conspect_quality_contract() -> str:
    """Compatibility validator; schema/normalization are source-owned."""
    from core.candidate_schema import expanded_page_response_schema

    block = (
        expanded_page_response_schema()["properties"]["sections"]["items"]
        ["properties"]["blocks"]["items"]
    )
    if "word_study" not in block["properties"]["type"].get("enum", []):
        raise RuntimeError("word_study is missing from the canonical expanded-page schema")
    return "source-owned Study schema/normalization; no runtime patching"
