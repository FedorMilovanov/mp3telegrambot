"""Deterministic audit/repair adapters for operator-confirmed conspect defects."""
from __future__ import annotations

import re
from typing import Any, Callable

_DROP_SENTINEL = "__DROP_INCOMPLETE_WORD_STUDY__"
_INSTALLED = False
_TS_RE = re.compile(r"⏱️?\s*\*{0,2}(\d{1,2}:\d{2}(?::\d{2})?)\*{0,2}")


def _patch_typo_normalizer() -> None:
    from core import text_utils

    current = text_utils.normalize_common_typos
    if getattr(current, "_conspect_audit_runtime", False):
        return

    def normalize_with_live_fixes(text: str, *args, **kwargs) -> str:
        out = current(text, *args, **kwargs)
        # Live 2026-07-23 Study page.  These are deliberately anchored phrases,
        # not a dangerous global replacement of every occurrence of “Божьего”.
        out = out.replace(
            "Слово Божьего — нструмент",
            "Слово Божье — инструмент",
        )
        out = out.replace(
            "слово Божьего — нструмент",
            "слово Божье — инструмент",
        )
        out = out.replace(
            "проповедь Слово Божьего",
            "проповедь Слова Божьего",
        )
        return out

    normalize_with_live_fixes._conspect_audit_runtime = True  # type: ignore[attr-defined]
    text_utils.normalize_common_typos = normalize_with_live_fixes


def _patch_structured_normalizer_for_drop() -> Callable[[Any], dict[str, Any] | None]:
    from core import structured_blocks
    from services.conspect_quality_contract import normalize_word_study_block

    current = structured_blocks.normalize_structured_block
    if getattr(current, "_conspect_drop_runtime", False):
        return current

    def normalize_with_drop(raw: Any) -> dict[str, Any] | None:
        if isinstance(raw, dict):
            btype = str(raw.get("type") or "").strip().lower()
            if btype in {"word_study", "wordstudy", "lexicon", "term", "lexical_analysis"}:
                block = normalize_word_study_block(raw)
                if block is not None:
                    return block
                # content_audit historically used ``normalize(...) or raw``;
                # return a marker object rather than None so the raw thin card
                # cannot be resurrected by that fallback.
                return {
                    "type": "paragraph",
                    "text": _DROP_SENTINEL,
                    "_drop_word_study": True,
                }
        return current(raw)

    normalize_with_drop._conspect_drop_runtime = True  # type: ignore[attr-defined]
    structured_blocks.normalize_structured_block = normalize_with_drop
    return normalize_with_drop


def _all_inline_timestamps(section: dict[str, Any]) -> list[str]:
    values: list[str] = [str(section.get("content") or "")]
    for block in section.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for value in block.values():
            if isinstance(value, str):
                values.append(value)
    found: list[str] = []
    for value in values:
        found.extend(m.group(1) for m in _TS_RE.finditer(value))
    return found


def _patch_content_audit(normalizer: Callable[[Any], dict[str, Any] | None]) -> None:
    from core import content_audit
    from core.core_utils import time_to_seconds

    # content_audit imports the function by value; update that local binding too.
    content_audit.normalize_structured_block = normalizer
    content_audit.normalize_common_typos = __import__(
        "core.text_utils", fromlist=["normalize_common_typos"]
    ).normalize_common_typos

    current = content_audit.audit_expanded_sections
    if getattr(current, "_conspect_audit_runtime", False):
        return

    def audit_with_repairs(
        sections: list[dict],
        outline: list[dict] | None = None,
        *,
        label: str = "",
        expected_author: str = "",
    ):
        new_sections, new_outline, issues = current(
            sections,
            outline,
            label=label,
            expected_author=expected_author,
        )

        repaired_sections: list[dict] = []
        dropped_locations: set[str] = set()
        for sidx, section in enumerate(new_sections):
            sec = dict(section)
            blocks = sec.get("blocks")
            if isinstance(blocks, list):
                kept: list[dict] = []
                for bidx, block in enumerate(blocks):
                    if isinstance(block, dict) and block.get("_drop_word_study"):
                        dropped_locations.add(
                            f"{label or 'expanded'}.sections[{sidx}].blocks[{bidx}]"
                        )
                        continue
                    kept.append(block)
                sec["blocks"] = kept

            # A tiny earlier inline timestamp is normally a rounding/section-boundary
            # defect, not an intentional retrospective cross-reference.  Reconcile
            # only differences up to 30 seconds; preserve larger backward links.
            section_time = str(sec.get("time") or "").strip()
            section_sec = time_to_seconds(section_time)
            inline = [
                (ts, time_to_seconds(ts))
                for ts in _all_inline_timestamps(sec)
            ]
            inline = [(ts, val) for ts, val in inline if val is not None]
            if section_sec is not None and inline:
                earliest_ts, earliest_sec = min(inline, key=lambda item: item[1])
                delta = section_sec - earliest_sec
                if 0 < delta <= 30:
                    old_time = section_time
                    sec["time"] = earliest_ts
                    if sidx < len(new_outline) and isinstance(new_outline[sidx], dict):
                        new_outline[sidx]["time"] = earliest_ts
                    issues.append(content_audit.ContentAuditIssue(
                        code="section_time_reconciled",
                        location=f"{label or 'expanded'}.sections[{sidx}].time",
                        message=f"section start moved {delta}s earlier to match inline timestamp",
                        before=old_time,
                        after=earliest_ts,
                    ))
            repaired_sections.append(sec)

        if dropped_locations:
            # Remove warnings produced from the temporary marker block, then record
            # one deterministic repair event per removed card.
            issues = [
                issue for issue in issues
                if not any(issue.location.startswith(loc) for loc in dropped_locations)
            ]
            for location in sorted(dropped_locations):
                issues.append(content_audit.ContentAuditIssue(
                    code="word_study_dropped",
                    location=location,
                    message="incomplete decorative lexicon card removed; page preserved",
                ))

        return repaired_sections, new_outline, issues

    audit_with_repairs._conspect_audit_runtime = True  # type: ignore[attr-defined]
    content_audit.audit_expanded_sections = audit_with_repairs


def install_conspect_audit_runtime() -> str:
    """Install deterministic fixes before Telegraph page modules import them."""
    global _INSTALLED
    if _INSTALLED:
        return "conspect audit runtime already installed"
    _patch_typo_normalizer()
    normalizer = _patch_structured_normalizer_for_drop()
    _patch_content_audit(normalizer)
    _INSTALLED = True
    return "thin word studies dropped; live typos and near-boundary timestamps repaired"
