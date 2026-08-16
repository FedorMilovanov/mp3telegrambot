"""Compatibility validator for source-owned Conspect audit rules."""
from __future__ import annotations

from typing import Any


def _normalize_legacy_lexicon(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep the historical helper importable without mutating global functions."""
    from core.structured_blocks import normalize_structured_block

    block = dict(raw or {})
    block["type"] = "lexicon"
    return normalize_structured_block(block) or block


__all__ = ["_normalize_legacy_lexicon"]
