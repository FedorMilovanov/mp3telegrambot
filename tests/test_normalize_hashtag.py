"""Tests for core.text_utils.normalize_hashtag — canonical DRY implementation.

V3 minor debt: normalize_hashtag was duplicated in shorts_candidates.py and
json_parser.py.  Moved to core/text_utils as single source of truth.
"""

import ast
import pathlib

import pytest

from core.text_utils import normalize_hashtag


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------

def test_single_lowercase_word_capitalized():
    assert normalize_hashtag("реформация") == "#Реформация"


def test_single_camelcase_preserved():
    """Already-capitalised CamelCase must NOT be lowercased."""
    assert normalize_hashtag("ПолВошер") == "#ПолВошер"


def test_two_russian_words_joined():
    assert normalize_hashtag("реформированный баптист") == "#РеформированныйБаптист"


def test_underscore_separator():
    assert normalize_hashtag("личная_встреча") == "#ЛичнаяВстреча"


def test_hyphen_separator():
    assert normalize_hashtag("новое-творение") == "#НовоеТворение"


def test_leading_hash_stripped():
    assert normalize_hashtag("#Покаяние") == "#Покаяние"


def test_empty_string_returns_empty():
    assert normalize_hashtag("") == ""


def test_hash_only_returns_empty():
    assert normalize_hashtag("#") == ""


def test_english_camelcase_preserved():
    assert normalize_hashtag("MacArthur") == "#MacArthur"


def test_internal_uppercase_not_downcased():
    """Ensure .capitalize() is NOT used — it would destroy inner capitals."""
    result = normalize_hashtag("НовоеТворение")
    assert result == "#НовоеТворение"
    assert "творение" not in result   # no silent downcase


def test_single_english_word():
    assert normalize_hashtag("grace") == "#Grace"


# ---------------------------------------------------------------------------
# Alias contract: shorts_candidates._normalize_hashtag == normalize_hashtag
# ---------------------------------------------------------------------------

def test_shorts_candidates_uses_alias_not_duplicate():
    """_normalize_hashtag in shorts_candidates must be an alias, not a duplicate def."""
    src = pathlib.Path("services/shorts_candidates.py").read_text(encoding="utf-8")
    # No standalone def
    assert "def _normalize_hashtag" not in src, \
        "Duplicate def _normalize_hashtag found — should be alias only"
    # Alias line exists
    assert "_normalize_hashtag = normalize_hashtag" in src, \
        "Alias _normalize_hashtag = normalize_hashtag not found"


def test_json_parser_uses_normalize_hashtag():
    """json_parser must import and use normalize_hashtag, not inline logic."""
    src = pathlib.Path("core/json_parser.py").read_text(encoding="utf-8")
    assert "normalize_hashtag," in src, \
        "normalize_hashtag not imported in json_parser"
    assert "normalize_hashtag(raw_tag)" in src, \
        "normalize_hashtag not called in json_parser"


def test_telegraph_pages_no_inner_re_imports():
    """telegraph_pages must not have bare `import re as _re*` inside functions."""
    src = pathlib.Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "import re as _re" not in src, \
        "Found bare inner 'import re as _re' in telegraph_pages — use module-level re"
