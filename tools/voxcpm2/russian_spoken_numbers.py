#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic Russian spoken forms for numeric semantic-QA targets.

VoxCPM/wetext may verbalize digits before synthesis. The release ASR gate must
therefore compare against the original text and a deterministic spoken variant,
without deleting numbers or allowing an arbitrary different value to pass.
"""
from __future__ import annotations

import re
from typing import Final

POLICY: Final = "russian-spoken-numbers-v1"

_ONES = (
    "",
    "один",
    "два",
    "три",
    "четыре",
    "пять",
    "шесть",
    "семь",
    "восемь",
    "девять",
)
_ONES_FEMININE = (
    "",
    "одна",
    "две",
    "три",
    "четыре",
    "пять",
    "шесть",
    "семь",
    "восемь",
    "девять",
)
_TEENS = (
    "десять",
    "одиннадцать",
    "двенадцать",
    "тринадцать",
    "четырнадцать",
    "пятнадцать",
    "шестнадцать",
    "семнадцать",
    "восемнадцать",
    "девятнадцать",
)
_TENS = (
    "",
    "",
    "двадцать",
    "тридцать",
    "сорок",
    "пятьдесят",
    "шестьдесят",
    "семьдесят",
    "восемьдесят",
    "девяносто",
)
_HUNDREDS = (
    "",
    "сто",
    "двести",
    "триста",
    "четыреста",
    "пятьсот",
    "шестьсот",
    "семьсот",
    "восемьсот",
    "девятьсот",
)
_SCALES = (
    ("", "", "", False),
    ("тысяча", "тысячи", "тысяч", True),
    ("миллион", "миллиона", "миллионов", False),
    ("миллиард", "миллиарда", "миллиардов", False),
    ("триллион", "триллиона", "триллионов", False),
)
_CURRENCIES = {
    "$": ("доллар", "доллара", "долларов"),
    "€": ("евро", "евро", "евро"),
    "£": ("фунт", "фунта", "фунтов"),
    "₽": ("рубль", "рубля", "рублей"),
}


def _plural_index(value: int) -> int:
    tail100 = abs(value) % 100
    tail10 = abs(value) % 10
    if 11 <= tail100 <= 14:
        return 2
    if tail10 == 1:
        return 0
    if 2 <= tail10 <= 4:
        return 1
    return 2


def _under_thousand(value: int, *, feminine: bool = False) -> list[str]:
    number = abs(int(value)) % 1000
    result: list[str] = []
    hundreds, remainder = divmod(number, 100)
    if hundreds:
        result.append(_HUNDREDS[hundreds])
    if 10 <= remainder <= 19:
        result.append(_TEENS[remainder - 10])
        return result
    tens, ones = divmod(remainder, 10)
    if tens:
        result.append(_TENS[tens])
    if ones:
        result.append((_ONES_FEMININE if feminine else _ONES)[ones])
    return result


def integer_to_words(value: int) -> str:
    number = int(value)
    if number == 0:
        return "ноль"
    if abs(number) >= 1000 ** len(_SCALES):
        return " ".join(str(number))

    result: list[str] = []
    if number < 0:
        result.append("минус")
    remaining = abs(number)
    groups: list[int] = []
    while remaining:
        groups.append(remaining % 1000)
        remaining //= 1000

    for scale_index in range(len(groups) - 1, -1, -1):
        group = groups[scale_index]
        if not group:
            continue
        singular, paucal, plural, feminine = _SCALES[scale_index]
        result.extend(_under_thousand(group, feminine=feminine))
        if scale_index:
            result.append((singular, paucal, plural)[_plural_index(group)])
    return " ".join(result)


def _unit(value: int, forms: tuple[str, str, str]) -> str:
    return forms[_plural_index(value)]


def _decimal_words(match: re.Match[str]) -> str:
    left = int(match.group("left"))
    fraction_raw = match.group("fraction")
    fraction = int(fraction_raw)
    scales = {
        1: ("десятая", "десятых"),
        2: ("сотая", "сотых"),
        3: ("тысячная", "тысячных"),
        4: ("десятитысячная", "десятитысячных"),
    }
    if len(fraction_raw) not in scales:
        return f"{integer_to_words(left)} запятая " + " ".join(
            integer_to_words(int(char)) for char in fraction_raw
        )
    singular, plural = scales[len(fraction_raw)]
    whole = "целая" if abs(left) % 10 == 1 and abs(left) % 100 != 11 else "целых"
    fraction_unit = singular if fraction == 1 else plural
    return (
        f"{integer_to_words(left)} {whole} "
        f"{integer_to_words(fraction)} {fraction_unit}"
    )


def _currency_prefix(match: re.Match[str]) -> str:
    symbol = match.group("symbol")
    value = int(match.group("value"))
    return f"{integer_to_words(value)} {_unit(value, _CURRENCIES[symbol])}"


def _currency_suffix(match: re.Match[str]) -> str:
    value = int(match.group("value"))
    symbol = match.group("symbol")
    return f"{integer_to_words(value)} {_unit(value, _CURRENCIES[symbol])}"


def _percent(match: re.Match[str]) -> str:
    value = int(match.group("value"))
    return f"{integer_to_words(value)} {_unit(value, ('процент', 'процента', 'процентов'))}"


def _plain_integer(match: re.Match[str]) -> str:
    raw = match.group(0)
    try:
        return integer_to_words(int(raw))
    except (ValueError, OverflowError):
        return raw


def normalize_numeric_text(value: str) -> str:
    """Return a Russian spoken variant while preserving every numeric value."""
    text = str(value or "")
    if not re.search(r"\d|[%№$€£₽]", text):
        return text

    text = re.sub(
        r"(?P<symbol>[$€£₽])\s*(?P<value>\d+)",
        _currency_prefix,
        text,
    )
    text = re.sub(
        r"(?P<value>\d+)\s*(?P<symbol>[$€£₽])",
        _currency_suffix,
        text,
    )
    text = re.sub(
        r"(?P<value>\d+)\s*%",
        _percent,
        text,
    )
    text = re.sub(
        r"№\s*(?P<value>\d+)",
        lambda match: "номер " + integer_to_words(int(match.group("value"))),
        text,
    )
    text = re.sub(
        r"(?P<left>\d+)[,.](?P<fraction>\d+)",
        _decimal_words,
        text,
    )
    text = re.sub(r"(?<![\w])[-+]?\d+(?![\w])", _plain_integer, text)
    return re.sub(r"\s+", " ", text).strip()


__all__ = ["POLICY", "integer_to_words", "normalize_numeric_text"]
