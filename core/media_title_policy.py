#!/usr/bin/env python3
"""Canonical media-title casing and public filename policy.

Pure functions only: callers own when and where the policy is applied.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.person_names import normalize_person_names

RU_SERVICE_WORDS = frozenset({
    "а", "без", "бы", "в", "во", "да", "для", "до", "же", "за", "и",
    "из", "или", "к", "ко", "ли", "между", "на", "над", "не", "ни", "но",
    "о", "об", "от", "по", "под", "при", "про", "с", "со", "у", "через",
})

_PRESERVE_CASE = {
    "esv": "ESV", "kjv": "KJV", "nasb": "NASB", "niv": "NIV",
    "lsb": "LSB", "nlt": "NLT", "csb": "CSB", "nkjv": "NKJV",
    "rsv": "RSV", "net": "NET", "nrsv": "NRSV", "leb": "LEB",
    "asv": "ASV", "lbcf": "LBCF", "lbcf1689": "LBCF1689",
    "wcf": "WCF", "tulip": "TULIP", "q&a": "Q&A", "qa": "QA",
    "youtube": "YouTube", "rutube": "RuTube", "vk": "VK",
    "iphone": "iPhone", "ipad": "iPad", "na28": "NA28",
    "bhs": "BHS", "lxx": "LXX",
}

_EDGE_RE = re.compile(r"^([^А-Яа-яЁёA-Za-z0-9]*)(.*?)([^А-Яа-яЁёA-Za-z0-9]*)$")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_SPACED_DASH_RE = re.compile(r"\s+([—–-])\s+")
_DELIVERY_MARKERS = tuple(sorted({
    " — русский дубляж", " — только русский голос", " — русские субтитры",
    " — исходные субтитры", " — точная расшифровка", " — шаблон перевода",
    " — проверка перевода", " — перевод",
}, key=len, reverse=True))


def _split_edges(token: str) -> tuple[str, str, str]:
    match = _EDGE_RE.match(token)
    return match.groups() if match else ("", token, "")


def _capitalize(word: str) -> str:
    for index, char in enumerate(word):
        if char.isalpha():
            return word[:index] + char.upper() + word[index + 1:]
    return word


def canonical_media_title(value: Any) -> str:
    """Return project Title Case while preserving punctuation and proper case."""
    text = re.sub(r"\s+", " ", str(value or "")).strip(" .—–-")
    text = _SPACED_DASH_RE.sub(lambda match: f" {match.group(1)} ", text)
    if not text or not _CYRILLIC_RE.search(text):
        return text
    result: list[str] = []
    for index, raw in enumerate(text.split()):
        prefix, core, suffix = _split_edges(raw)
        if not core:
            result.append(raw)
            continue
        folded = core.casefold()
        if index > 0 and folded in RU_SERVICE_WORDS:
            normalized = core.lower()
        elif folded in _PRESERVE_CASE:
            normalized = _PRESERVE_CASE[folded]
        elif re.search(r"[а-яё][А-ЯЁ]", core) or re.search(r"-[А-ЯЁ]", core):
            normalized = core
        elif len(core) >= 2 and core.isupper() and core.isalpha():
            normalized = core
        else:
            normalized = _capitalize(core.lower())
        result.append(prefix + normalized + suffix)
    return normalize_person_names(" ".join(result))


def canonical_delivery_filename(value: Any) -> str:
    """Canonicalize only the title portion of a user-facing filename."""
    filename = Path(str(value or "")).name
    if not filename:
        return filename
    suffix = Path(filename).suffix
    stem = filename[:-len(suffix)] if suffix else filename
    if not _CYRILLIC_RE.search(stem):
        return filename
    folded = stem.casefold()
    for marker in _DELIVERY_MARKERS:
        position = folded.find(marker)
        if position > 0:
            return canonical_media_title(stem[:position]) + stem[position:] + suffix
    return canonical_media_title(stem) + suffix


def media_title_policy_contract() -> tuple[bool, str]:
    ok = (
        canonical_media_title("Сила И Достоинство Благочестивой Женщины - Джон Пайпер")
        == "Сила и Достоинство Благочестивой Женщины - Джон Пайпер"
        and canonical_delivery_filename(
            "Сила И Достоинство - Джон Пайпер — русский дубляж.mp4"
        ) == "Сила и Достоинство - Джон Пайпер — русский дубляж.mp4"
    )
    return ok, "source-owned canonical Russian Title Case + delivery filename policy"


__all__ = [
    "RU_SERVICE_WORDS", "canonical_delivery_filename", "canonical_media_title",
    "media_title_policy_contract",
]
