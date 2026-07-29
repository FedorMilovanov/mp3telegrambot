#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic Russian spoken forms for numeric semantic-QA targets.

VoxCPM/wetext may verbalize digits before synthesis. The release ASR gate must
therefore compare against spoken variants while preserving every numeric value.
Each numeric expression also emits mandatory spoken-anchor alternatives so a
fuzzy text score cannot silently turn ``3`` into ``5`` or change a date.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Final

POLICY: Final = "russian-spoken-numbers-v2"

_ONES = (
    "", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять",
)
_ONES_FEMININE = (
    "", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять",
)
_TEENS = (
    "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать",
    "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать",
)
_TENS = (
    "", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят",
    "семьдесят", "восемьдесят", "девяносто",
)
_HUNDREDS = (
    "", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот",
    "семьсот", "восемьсот", "девятьсот",
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
_MONTHS_GENITIVE = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_ORDINAL_GENITIVE_1_19 = {
    1: "первого", 2: "второго", 3: "третьего", 4: "четвертого",
    5: "пятого", 6: "шестого", 7: "седьмого", 8: "восьмого",
    9: "девятого", 10: "десятого", 11: "одиннадцатого",
    12: "двенадцатого", 13: "тринадцатого", 14: "четырнадцатого",
    15: "пятнадцатого", 16: "шестнадцатого", 17: "семнадцатого",
    18: "восемнадцатого", 19: "девятнадцатого",
}
_ORDINAL_TENS_GENITIVE = {
    20: "двадцатого", 30: "тридцатого", 40: "сорокового",
    50: "пятидесятого", 60: "шестидесятого", 70: "семидесятого",
    80: "восьмидесятого", 90: "девяностого",
}
_ORDINAL_HUNDREDS_GENITIVE = {
    100: "сотого", 200: "двухсотого", 300: "трехсотого",
    400: "четырехсотого", 500: "пятисотого", 600: "шестисотого",
    700: "семисотого", 800: "восьмисотого", 900: "девятисотого",
}
_EXACT_THOUSANDTH_GENITIVE = {
    1000: "тысячного", 2000: "двухтысячного", 3000: "трехтысячного",
    4000: "четырехтысячного", 5000: "пятитысячного",
    6000: "шеститысячного", 7000: "семитысячного",
    8000: "восьмитысячного", 9000: "девятитысячного",
}

AnchorGroups = list[list[str]]


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


def _ordinal_genitive(value: int) -> str:
    number = int(value)
    if number in _ORDINAL_GENITIVE_1_19:
        return _ORDINAL_GENITIVE_1_19[number]
    if number in _ORDINAL_TENS_GENITIVE:
        return _ORDINAL_TENS_GENITIVE[number]
    if number in _ORDINAL_HUNDREDS_GENITIVE:
        return _ORDINAL_HUNDREDS_GENITIVE[number]
    if number in _EXACT_THOUSANDTH_GENITIVE:
        return _EXACT_THOUSANDTH_GENITIVE[number]
    if 20 < number < 100:
        tens = number // 10 * 10
        return f"{_TENS[tens // 10]} {_ordinal_genitive(number % 10)}"
    if 100 < number < 1000:
        hundreds = number // 100
        return f"{_HUNDREDS[hundreds]} {_ordinal_genitive(number % 100)}"
    if 1000 < number < 10_000:
        thousands = number // 1000 * 1000
        remainder = number % 1000
        return f"{integer_to_words(thousands)} {_ordinal_genitive(remainder)}"
    return integer_to_words(number)


def _unit(value: int, forms: tuple[str, str, str]) -> str:
    return forms[_plural_index(value)]


def _date_forms(day: int, month: int, year: int) -> list[str]:
    # Validate impossible dates before producing anchors.
    dt.date(year, month, day)
    month_word = _MONTHS_GENITIVE[month]
    ordinal = f"{_ordinal_genitive(day)} {month_word} {_ordinal_genitive(year)} года"
    cardinal = f"{integer_to_words(day)} {month_word} {integer_to_words(year)} года"
    return [ordinal, ordinal.removesuffix(" года"), cardinal, cardinal.removesuffix(" года")]


def _decimal_phrase(left: int, fraction_raw: str) -> str:
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
    return f"{integer_to_words(left)} {whole} {integer_to_words(fraction)} {fraction_unit}"


def _normalize(value: str, *, collect_anchors: bool) -> tuple[str, AnchorGroups]:
    text = str(value or "")
    groups: AnchorGroups = []
    if not re.search(r"\d|[%№$€£₽]", text):
        return text, groups

    def remember(forms: list[str]) -> str:
        unique = list(dict.fromkeys(form.strip() for form in forms if form.strip()))
        if collect_anchors and unique:
            groups.append(unique)
        return unique[0]

    def date_dmy(match: re.Match[str]) -> str:
        try:
            forms = _date_forms(
                int(match.group("day")), int(match.group("month")), int(match.group("year"))
            )
        except ValueError:
            return match.group(0)
        return remember(forms)

    def date_ymd(match: re.Match[str]) -> str:
        try:
            forms = _date_forms(
                int(match.group("day")), int(match.group("month")), int(match.group("year"))
            )
        except ValueError:
            return match.group(0)
        return remember(forms)

    # Dates must be consumed before decimal expressions.
    text = re.sub(
        r"(?<!\d)(?P<day>0?[1-9]|[12]\d|3[01])[./-](?P<month>0?[1-9]|1[0-2])[./-](?P<year>\d{4})(?!\d)",
        date_dmy,
        text,
    )
    text = re.sub(
        r"(?<!\d)(?P<year>\d{4})-(?P<month>0?[1-9]|1[0-2])-(?P<day>0?[1-9]|[12]\d|3[01])(?!\d)",
        date_ymd,
        text,
    )

    def currency(match: re.Match[str]) -> str:
        symbol = match.group("symbol")
        amount = int(match.group("value"))
        phrase = f"{integer_to_words(amount)} {_unit(amount, _CURRENCIES[symbol])}"
        return remember([phrase])

    text = re.sub(r"(?P<symbol>[$€£₽])\s*(?P<value>\d+)", currency, text)
    text = re.sub(r"(?P<value>\d+)\s*(?P<symbol>[$€£₽])", currency, text)

    def percent(match: re.Match[str]) -> str:
        amount = int(match.group("value"))
        phrase = f"{integer_to_words(amount)} {_unit(amount, ('процент', 'процента', 'процентов'))}"
        return remember([phrase])

    text = re.sub(r"(?P<value>\d+)\s*%", percent, text)

    def number_sign(match: re.Match[str]) -> str:
        phrase = "номер " + integer_to_words(int(match.group("value")))
        return remember([phrase])

    text = re.sub(r"№\s*(?P<value>\d+)", number_sign, text)

    def decimal(match: re.Match[str]) -> str:
        phrase = _decimal_phrase(int(match.group("left")), match.group("fraction"))
        return remember([phrase])

    text = re.sub(r"(?P<left>\d+)[,.](?P<fraction>\d+)", decimal, text)

    def integer(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            phrase = integer_to_words(int(raw))
        except (ValueError, OverflowError):
            return raw
        return remember([phrase])

    text = re.sub(r"(?<![\w])[-+]?\d+(?![\w])", integer, text)
    return re.sub(r"\s+", " ", text).strip(), groups


def normalize_numeric_text(value: str) -> str:
    """Return the canonical spoken variant while preserving numeric values."""
    return _normalize(value, collect_anchors=False)[0]


def numeric_anchor_groups(value: str) -> AnchorGroups:
    """Return mandatory spoken alternatives for each numeric expression."""
    return _normalize(value, collect_anchors=True)[1]


__all__ = [
    "POLICY",
    "integer_to_words",
    "normalize_numeric_text",
    "numeric_anchor_groups",
]
