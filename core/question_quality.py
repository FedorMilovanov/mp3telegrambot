#!/usr/bin/env python3
"""Deterministic quality helpers for generated reflection/quiz questions."""
from __future__ import annotations

import re
from typing import Any

_QUESTION_START_RE = re.compile(
    r"^(как|почему|что|где|когда|каким|какая|какой|какие|насколько|что\s+именно)\b",
    re.IGNORECASE,
)

_GENERIC_QUESTION_RE = re.compile(
    r"^(?:"
    r"как\s+(?:это|этот\s+материал|данный\s+материал)\s+(?:применить|использовать)|"
    r"что\s+(?:это|этот\s+материал)\s+значит\s+для\s+(?:меня|нас|вас)|"
    r"как\s+(?:это|этот\s+материал)\s+влияет\s+на\s+(?:мою|вашу|нашу)\s+жизнь|"
    r"какой\s+главный\s+урок\s+(?:мы\s+)?(?:можем\s+)?(?:извлечь|получить)|"
    r"почему\s+это\s+важно\s+для\s+(?:меня|нас|вас)|"
    r"что\s+вы\s+думаете\s+об\s+этом"
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
    return bool(_GENERIC_QUESTION_RE.match(text))


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
