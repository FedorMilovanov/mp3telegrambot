"""Regression tests for mixed Greek/Cyrillic token cleanup in Telegraph pages."""

from core.text_utils import find_mixed_greek_cyrillic_tokens, normalize_common_typos


def test_hemera_mixed_cyrillic_letters_are_fixed():
    bad = "• **κυριακὴ ἡμέра** (kyriake hemera) — день Господень. Saturdays-night служения."
    fixed = normalize_common_typos(bad)
    assert "ἡμέρα" in fixed
    assert "ἡμέра" not in fixed
    assert "Saturday-night" in fixed
    assert find_mixed_greek_cyrillic_tokens(fixed) == []
