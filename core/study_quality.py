#!/usr/bin/env python3
"""Pure Study Analysis quality helpers.

No installer and no module mutation lives here.  Structured-block normalization
and content audit call these helpers directly, making Study behavior independent
of import order.
"""
from __future__ import annotations

import re
from typing import Any

DROP_INCOMPLETE_WORD_STUDY = "__DROP_INCOMPLETE_WORD_STUDY__"

_FIELD_LABEL_RE = re.compile(
    r"(?im)^\s*(?:\*\*)?(?:Русская фраза стиха|Базовое значение|В этом стихе|"
    r"Роль в аргументе(?: материала)?|Граница вывода|Источник)(?:\*\*)?\s*:"
)
_CARD_LINE_RE = re.compile(
    r"(?m)^\s*(?:[•\-]\s*)?\*\*[^*\n]{2,120}\**"
    r"(?:\s*\(\*\*[^*\n]{2,100}\*\*\))?\s*[—:]"
)
_BOLD_RE = re.compile(r"\*\*[^*\n]{2,180}\*\*")
_GENERIC_SECTION_RE = re.compile(
    r"^(?:ключевые понятия|ключевые тексты(?: и экзегетические узлы)?|"
    r"языки оригинала(?: и .*)?|ключевые слова в контексте писания|"
    r"переводческие развилки(?: и .*)?|источники|карта источников(?: и .*)?)$",
    re.IGNORECASE,
)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _sentence(value: str) -> str:
    value = _clean(value)
    if not value:
        return ""
    return value if value.endswith((".", "!", "?", "…")) else value + "."


def render_word_study_as_prose(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Render one sufficiently grounded lexical observation as coherent prose."""
    aliases = {
        "scripture_ref": ("scripture_ref", "ref"),
        "russian_quote": ("russian_quote", "quote"),
        "russian_focus": ("russian_focus", "focus"),
        "original_form": ("original_form",),
        "lemma": ("lemma",),
        "transliteration": ("transliteration",),
        "russian_pronunciation": ("russian_pronunciation", "pronunciation_ru"),
        "grammar": ("grammar",),
        "basic_meaning": ("basic_meaning", "dictionary_meaning"),
        "meaning_in_context": ("meaning_in_context", "contextual_meaning"),
        "role_in_argument": ("role_in_argument", "why_relevant"),
        "limits_of_claim": ("limits_of_claim",),
        "source": ("source", "source_label"),
        "anchor_timestamp": ("anchor_timestamp", "timestamp"),
    }

    def value(name: str) -> str:
        for key in aliases[name]:
            found = _clean(raw.get(key))
            if found:
                return found
        return ""

    values = {name: value(name) for name in aliases}
    required = (
        "scripture_ref",
        "russian_focus",
        "original_form",
        "lemma",
        "meaning_in_context",
        "role_in_argument",
    )
    if any(not values[name] for name in required):
        return None

    sentences: list[str] = [
        f"**{values['scripture_ref']} — «{values['russian_focus']}».**"
    ]
    if values["russian_quote"]:
        sentences.append(
            _sentence(
                f"В русской фразе «{values['russian_quote']}» внимание падает на "
                f"слово «{values['russian_focus']}»"
            )
        )

    form = f"В оригинале стоит **{values['original_form']}**, форма от *{values['lemma']}*"
    reading_bits: list[str] = []
    if values["transliteration"]:
        reading_bits.append(f"*{values['transliteration']}*")
    if values["russian_pronunciation"]:
        reading_bits.append(f"примерно «{values['russian_pronunciation']}»")
    if reading_bits:
        form += " (" + ", ".join(reading_bits) + ")"
    if values["grammar"]:
        form += f"; здесь это {values['grammar']}"
    sentences.append(_sentence(form))

    if values["basic_meaning"]:
        sentences.append(
            _sentence(
                f"Обычный смысл слова — {values['basic_meaning']}; в данном контексте "
                f"{values['meaning_in_context']}"
            )
        )
    else:
        sentences.append(_sentence(f"В данном контексте {values['meaning_in_context']}"))
    sentences.append(_sentence(values["role_in_argument"]))
    if values["limits_of_claim"]:
        sentences.append(_sentence(values["limits_of_claim"]))

    tail: list[str] = []
    if values["source"]:
        tail.append(values["source"])
    if values["anchor_timestamp"]:
        tail.append(f"⏱ **{values['anchor_timestamp']}**")
    text = " ".join(part.strip() for part in sentences if part.strip())
    if tail:
        text = text.rstrip(". ") + " (" + "; ".join(tail) + ")."
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    return {"type": "paragraph", "text": text}


def normalize_word_study_or_drop(raw: dict[str, Any]) -> dict[str, Any]:
    """Never let a thin generated word-study resurrect through ``or raw``."""
    rendered = render_word_study_as_prose(raw)
    if rendered is not None:
        return rendered
    return {
        "type": "paragraph",
        "text": DROP_INCOMPLETE_WORD_STUDY,
        "_drop_word_study": True,
    }


def collect_teacherly_study_warnings(sections: list[dict]) -> list[dict[str, str]]:
    """Return audit findings for Study prose without depending on audit classes."""
    findings: list[dict[str, str]] = []
    generic_titles = 0
    for idx, section in enumerate(sections or []):
        if not isinstance(section, dict):
            continue
        title = _clean(section.get("title"))
        content = str(section.get("content") or "")
        location = f"StudyAnalysis.sections[{idx}]"
        if _GENERIC_SECTION_RE.match(title):
            generic_titles += 1

        labels = len(_FIELD_LABEL_RE.findall(content))
        if labels >= 3:
            findings.append({
                "code": "study_checklist_prose_warning",
                "location": f"{location}.content",
                "message": (
                    "visible Study prose answers an internal field checklist; rewrite as "
                    "one coherent teacherly paragraph without field labels"
                ),
                "before": content[:180],
            })

        cards = len(_CARD_LINE_RE.findall(content))
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
        short_bold_cards = sum(
            1
            for paragraph in paragraphs
            if len(paragraph) < 320 and re.match(r"^\s*(?:[•\-]\s*)?\*\*", paragraph)
        )
        if cards >= 4 or (
            short_bold_cards >= 4
            and short_bold_cards * 2 >= max(1, len(paragraphs))
        ):
            findings.append({
                "code": "study_fragmented_cards_warning",
                "location": f"{location}.content",
                "message": (
                    "Study section is fragmented into definition cards; combine them into "
                    "connected explanatory paragraphs with an argument arc"
                ),
                "before": content[:180],
            })

        visible = re.sub(r"\s+", " ", content).strip()
        if len(visible) >= 700 and len(_BOLD_RE.findall(content)) < 2:
            findings.append({
                "code": "study_bold_anchor_missing_warning",
                "location": f"{location}.content",
                "message": (
                    "long Study section lacks semantic bold anchors; emphasize two or more "
                    "real theses or contrasts inside the prose"
                ),
                "before": content[:180],
            })

    if generic_titles >= 3:
        findings.append({
            "code": "study_template_architecture_warning",
            "location": "StudyAnalysis.outline",
            "message": (
                "three or more generic rubric headings survived; choose material-specific "
                "teaching headings and a natural composition"
            ),
            "before": "",
        })
    return findings


__all__ = [
    "DROP_INCOMPLETE_WORD_STUDY",
    "collect_teacherly_study_warnings",
    "normalize_word_study_or_drop",
    "render_word_study_as_prose",
]
