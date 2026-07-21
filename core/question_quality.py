#!/usr/bin/env python3
"""Deterministic quality helpers for generated reflection/quiz questions.

The filter is deliberately conservative: it rejects clearly reusable question
shells, but leaves substantive theological questions alone.  Prompt guidance
creates depth; this module prevents the most common banal scaffolds from being
published when a model ignores that guidance.
"""
from __future__ import annotations

import re
from typing import Any

_QUESTION_START_RE = re.compile(
    r"^(как|почему|что|где|когда|каким|какая|какой|какие|насколько|что\s+именно|"
    r"какую|какого|кого|чего|в\s+ч[её]м)\b",
    re.IGNORECASE,
)

_GENERIC_QUESTION_RE = re.compile(
    r"^(?:"
    r"как\s+(?:это|этот\s+материал|данный\s+материал|эта\s+истина|этот\s+принцип)\s+"
    r"(?:применить|использовать|воплотить)(?:\s+в\s+(?:моей|нашей|вашей)\s+жизни)?|"
    r"что\s+(?:это|этот\s+материал|данный\s+материал|эта\s+истина)\s+значит\s+для\s+(?:меня|нас|вас)|"
    r"как\s+(?:это|этот\s+материал|данный\s+материал)\s+влияет\s+на\s+(?:мою|вашу|нашу)\s+жизнь|"
    r"какой\s+главный\s+урок\s+(?:мы\s+)?(?:можем\s+)?(?:извлечь|получить)|"
    r"почему\s+это\s+важно\s+для\s+(?:меня|нас|вас)|"
    r"что\s+вы\s+думаете\s+об\s+этом|"
    r"что\s+(?:утверждает|говорит|показывает|объясняет)\s+(?:материал|автор|проповедник)|"
    r"(?:какой|какая|какие|что)\s+(?:ответ|вариант)\s+(?:верен|верный|правилен|правильный|является)|"
    r"чему\s+(?:это|данный\s+материал|этот\s+материал)\s+учит\s+(?:меня|нас|вас)|"
    r"как\s+(?:нам|мне|вам)\s+(?:применить|использовать)\s+(?:эту\s+истину|этот\s+принцип)"
    r")\??$",
    re.IGNORECASE,
)

# Generic spiritual-performance shells.  They sound pious but contain neither a
# truth claim, a concrete resistance, nor a situation from the source material.
_THIN_SPIRITUAL_RE = re.compile(
    r"^(?:"
    r"как\s+(?:мы|нам|мне|вам|вы|я)\s+(?:можем\s+)?(?:лучше|больше|глубже|сильнее|полнее)?\s*"
    r"(?:доверять\s+богу|молиться|читать\s+писани[ея]|служить|любить\s+бога|возрастать\s+в\s+вере)|"
    r"какие\s+(?:конкретные\s+)?(?:практические\s+)?шаги\s+(?:мы\s+)?(?:можем|нужно|следует|стоит)\s+"
    r"(?:предпринять|сделать)|"
    r"что\s+(?:нам|мне|вам)\s+(?:нужно|следует|стоит)\s+делать,?\s+чтобы\s+"
    r"(?:стать\s+лучше|возрастать|укрепить\s+веру|быть\s+ближе\s+к\s+богу)|"
    r"как\s+(?:нам|мне|вам)\s+(?:расти|возрастать)\s+(?:духовно|в\s+вере|в\s+благодати)|"
    r"как\s+(?:эта\s+истина|этот\s+принцип)\s+может\s+изменить\s+(?:мою|нашу|вашу)\s+жизнь"
    r")\??$",
    re.IGNORECASE,
)

# Mechanical prompts that outsource all thinking to the reader instead of
# teaching a distinction or exposing a concrete premise.
_EMPTY_REFLECTION_RE = re.compile(
    r"^(?:"
    r"над\s+чем\s+(?:нам|мне|вам)\s+стоит\s+задуматься|"
    r"что\s+(?:нам|мне|вам)\s+следует\s+переосмыслить|"
    r"какие\s+выводы\s+(?:мы\s+)?можем\s+сделать|"
    r"что\s+(?:в\s+этом|здесь)\s+самое\s+важное"
    r")\??$",
    re.IGNORECASE,
)


def question_key(text: str) -> str:
    return re.sub(r"\W+", "", str(text or "").casefold())


def is_question_like(text: str) -> bool:
    text = str(text or "").strip()
    return text.endswith("?") or bool(_QUESTION_START_RE.match(text))


def ensure_question_mark(text: str) -> str:
    text = str(text or "").strip()
    if text.endswith("?"):
        return text
    if _QUESTION_START_RE.match(text):
        return text.rstrip(".!…") + "?"
    return text


def is_generic_question(text: str) -> bool:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return True
    return bool(
        _GENERIC_QUESTION_RE.match(text)
        or _THIN_SPIRITUAL_RE.match(text)
        or _EMPTY_REFLECTION_RE.match(text)
    )


def normalize_question_text(value: Any, *, max_len: int = 255) -> str:
    text = str(value or "").replace("\x00", "").replace("\r", " ").strip()
    text = re.sub(r"^[🟢🔵]\s*", "", text)
    text = " ".join(x.strip() for x in text.splitlines() if x.strip())
    text = re.sub(r"\s+", " ", text).strip()
    text = ensure_question_mark(text)
    if len(text) > max_len:
        was_question = text.endswith("?")
        suffix = "…?" if was_question and max_len >= 2 else "…"
        cut = text[: max(1, max_len - len(suffix))].rstrip()
        if " " in cut and len(cut) > max_len * 0.65:
            word_cut = cut.rsplit(" ", 1)[0]
            if len(word_cut) >= max_len * 0.40:
                cut = word_cut
        text = cut.rstrip(".,;:—-?") + suffix
    return text


def question_is_usable(value: Any, *, min_len: int = 18) -> bool:
    text = str(value or "").strip()
    return len(text) >= min_len and text.endswith("?") and not is_generic_question(text)
