#!/usr/bin/env python3
"""Shared Telegraph API field limits for every create/edit transport."""
from __future__ import annotations


TELEGRAPH_TITLE_MAX = 256
TELEGRAPH_AUTHOR_NAME_MAX = 128
TELEGRAPH_AUTHOR_URL_MAX = 512


def fit_telegraph_title(value: object) -> str:
    """Return a title that can be sent to Telegraph createPage/editPage."""
    return str(value or "")[:TELEGRAPH_TITLE_MAX]


def compose_telegraph_title(base: object, suffix: object) -> str:
    """Append a structural suffix while preserving it inside Telegraph's limit."""
    base_text = str(base or "")
    suffix_text = str(suffix or "")
    if len(suffix_text) >= TELEGRAPH_TITLE_MAX:
        return suffix_text[:TELEGRAPH_TITLE_MAX]
    return base_text[: TELEGRAPH_TITLE_MAX - len(suffix_text)] + suffix_text


def fit_telegraph_author_name(value: object) -> str:
    return str(value or "")[:TELEGRAPH_AUTHOR_NAME_MAX]


def fit_telegraph_author_url(value: object) -> str:
    return str(value or "")[:TELEGRAPH_AUTHOR_URL_MAX]


__all__ = [
    "TELEGRAPH_AUTHOR_NAME_MAX",
    "TELEGRAPH_AUTHOR_URL_MAX",
    "TELEGRAPH_TITLE_MAX",
    "compose_telegraph_title",
    "fit_telegraph_author_name",
    "fit_telegraph_author_url",
    "fit_telegraph_title",
]
