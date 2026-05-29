#!/usr/bin/env python3
"""Structured expanded-page block normalization.

The Gemini schema nudges models toward a small canonical block vocabulary, but
live models still return useful aliases (quote, blockquote, lexical_analysis,
theological_line).  Keep alias handling in one deterministic place so audit and
rendering do not drift.
"""
from __future__ import annotations

import re
from typing import Any


CANONICAL_BLOCK_TYPES: tuple[str, ...] = (
    "paragraph",
    "bullet",
    "scripture",
    "source",
    "lexicon",
    "heading",
    "thesis",
    "argument_spine",
    "pull_quote",
    "application",
)

_BLOCK_TYPE_ALIASES: dict[str, str] = {
    "para": "paragraph",
    "list_item": "bullet",
    "point": "bullet",
    "scripture_quote": "scripture",
    "quote": "pull_quote",
    "blockquote": "pull_quote",
    "block_quote": "pull_quote",
    "source_card": "source",
    "bibliography": "source",
    "term": "lexicon",
    "lexical_analysis": "lexicon",
    "subheading": "heading",
    # These are useful model-invented shapes, but render best as analytical bullets.
    "theological_line": "bullet",
    "historical_line": "bullet",
}

_SCRIPTURE_TEXT_RE = re.compile(
    r"^\s*(?:[•\-]\s*)?\*\*(?P<ref>[^*\n]{2,140})\*\*\s*"
    r"(?P<quote>\*?[«\"].+?[»\"]\*?)?\s*(?P<tail>.*)$",
    re.DOTALL,
)


def canonical_block_type(value: Any) -> str:
    raw = str(value or "paragraph").strip().lower()
    return _BLOCK_TYPE_ALIASES.get(raw, raw if raw in CANONICAL_BLOCK_TYPES else "paragraph")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _strip_quote_markup(value: str) -> str:
    text = _clean(value)
    text = re.sub(r"^\*+|\*+$", "", text).strip()
    if text.startswith("«") and text.endswith("»") and len(text) >= 2:
        return text[1:-1].strip()
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1].strip()
    return text


def _parse_scripture_text(text: str) -> dict[str, str]:
    """Best-effort parse of `**2 Тим 3:16:** *«...»*\n\nExplanation`."""
    src = _clean(text)
    if not src:
        return {}
    first, _, rest = src.partition("\n\n")
    m = _SCRIPTURE_TEXT_RE.match(first.strip())
    if not m:
        return {"text": src}
    ref = (m.group("ref") or "").strip().rstrip(":")
    quote = _strip_quote_markup(m.group("quote") or "")
    tail = _clean((m.group("tail") or "") + ("\n\n" + rest if rest else ""))
    out: dict[str, str] = {}
    if ref:
        out["ref"] = ref
    if quote:
        out["quote"] = quote
    if tail:
        out["role_in_argument"] = tail
    if not out:
        out["text"] = src
    return out


def normalize_structured_block(raw: Any) -> dict[str, Any] | None:
    """Return a normalized block dict or None for unusable input."""
    if not isinstance(raw, dict):
        return None
    block = dict(raw)
    original_type = str(block.get("type") or "paragraph").strip().lower()
    btype = canonical_block_type(original_type)
    block["type"] = btype

    if btype == "pull_quote":
        if not _clean(block.get("quote")) and _clean(block.get("text")):
            block["quote"] = block.get("text")
        return block

    if btype == "lexicon":
        if not _clean(block.get("role_in_argument")) and _clean(block.get("text")):
            block["role_in_argument"] = block.get("text")
        return block

    if btype == "bullet" and original_type in {"theological_line", "historical_line"}:
        title = _clean(block.get("title_original") or block.get("title") or block.get("text"))
        why = _clean(block.get("why_relevant") or block.get("role_in_argument"))
        if title and why:
            block["text"] = f"**{title}** — {why}"
        elif why:
            block["text"] = why
        elif title:
            block["text"] = title
        return block

    if btype == "source":
        if not _clean(block.get("title_original")) and _clean(block.get("title")):
            block["title_original"] = block.get("title")
        return block

    if btype == "scripture":
        if (not _clean(block.get("ref"))) and _clean(block.get("text")):
            parsed = _parse_scripture_text(_clean(block.get("text")))
            block.update({k: v for k, v in parsed.items() if v})
        if not _clean(block.get("role_in_argument")) and _clean(block.get("why_relevant")):
            block["role_in_argument"] = block.get("why_relevant")
        return block

    if btype == "application":
        if not _clean(block.get("anchor_timestamp")) and _clean(block.get("timestamp")):
            block["anchor_timestamp"] = block.get("timestamp")
        return block

    return block


def normalize_structured_blocks(blocks: Any) -> list[dict[str, Any]]:
    if not isinstance(blocks, list):
        return []
    out: list[dict[str, Any]] = []
    for item in blocks:
        block = normalize_structured_block(item)
        if block is not None:
            out.append(block)
    return out
