"""Compatibility validator for source-owned Conspect audit rules."""
from __future__ import annotations

from typing import Any


def _normalize_legacy_lexicon(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep the historical helper importable without mutating global functions."""
    from core.structured_blocks import normalize_structured_block

    block = dict(raw or {})
    block["type"] = "lexicon"
    return normalize_structured_block(block) or block


def install_conspect_audit_runtime() -> str:
    from core.content_audit import audit_expanded_sections
    from core.structured_blocks import normalize_structured_block
    from core.text_utils import normalize_common_typos

    if "инструмент" not in normalize_common_typos("Слово Божьего — нструмент"):
        raise RuntimeError("source-owned Conspect typo repair is missing")
    dropped = normalize_structured_block({"type": "word_study"}) or {}
    if not dropped.get("_drop_word_study"):
        raise RuntimeError("source-owned incomplete word-study drop contract is missing")
    if not callable(audit_expanded_sections):
        raise RuntimeError("source-owned content audit is unavailable")
    return "source-owned Conspect typo/block/audit rules; no runtime patching"


__all__ = ["_normalize_legacy_lexicon", "install_conspect_audit_runtime"]
