#!/usr/bin/env python3
"""Shared Telegraph API field limits for every create/edit transport."""
from __future__ import annotations


TELEGRAPH_TITLE_MAX = 256
TELEGRAPH_AUTHOR_NAME_MAX = 128
TELEGRAPH_AUTHOR_URL_MAX = 512


def fit_telegraph_title(value: object) -> str:
    """Return a title that can be sent to Telegraph createPage/editPage."""
    return str(value or "")[:TELEGRAPH_TITLE_MAX]


def fit_telegraph_author_name(value: object) -> str:
    return str(value or "")[:TELEGRAPH_AUTHOR_NAME_MAX]


def fit_telegraph_author_url(value: object) -> str:
    return str(value or "")[:TELEGRAPH_AUTHOR_URL_MAX]


__all__ = [
    "TELEGRAPH_AUTHOR_NAME_MAX",
    "TELEGRAPH_AUTHOR_URL_MAX",
    "TELEGRAPH_TITLE_MAX",
    "fit_telegraph_author_name",
    "fit_telegraph_author_url",
    "fit_telegraph_title",
]
