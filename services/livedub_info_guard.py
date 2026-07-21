#!/usr/bin/env python3
"""Deterministic grounding for lightweight LiveDub publication cards.

The light model is useful for wording, but it must not decide whether a Bible
reference or a long author hashtag is real.  This module samples subtitles from
the whole video and validates high-risk fields against that evidence.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.person_names import known_author_from_text, known_ru_author_from_text
from core.text_utils import normalize_hashtag

_TIME_RE = re.compile(r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})[,.]\d{3}\s*-->")
_REF_RE = re.compile(r"(?<!\d)(?P<c>\d{1,3})\s*:\s*(?P<v>\d{1,3})(?!\d)")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{3,}")

_BOOK_GROUPS = (
    ("быт", "бытие", "genesis"), ("исх", "исход", "exodus"),
    ("цар", "царств", "kings"), ("пар", "паралипоменон", "chronicles"),
    ("пс", "псалом", "псалмов", "psalm"), ("притч", "притчи", "proverbs"),
    ("ис", "исаия", "исайя", "isaiah"), ("иер", "иеремия", "jeremiah"),
    ("матф", "матфея", "matthew"), ("мк", "марка", "mark"),
    ("лк", "луки", "luke"), ("ин", "иоанна", "john"),
    ("деян", "деяния", "acts"), ("рим", "римлянам", "romans"),
    ("кор", "коринфянам", "corinthians"), ("гал", "галатам", "galatians"),
    ("еф", "ефесянам", "ephesians"), ("флп", "филиппийцам", "philippians"),
    ("кол", "колоссянам", "colossians"), ("фес", "фессалоникийцам", "thessalonians"),
    ("тим", "тимофею", "timothy"), ("евр", "евреям", "hebrews"),
    ("иак", "иакова", "james"), ("пет", "петра", "peter"),
    ("иуд", "иуды", "jude"), ("откр", "откровение", "revelation"),
)

_GENERIC_CLAIMS = {
    "современный мир": ("современн", "мир"),
    "объективная истина": ("объектив", "истин"),
    "субъективные чувства": ("субъектив", "чувств"),
    "разум христов": ("разум христ",),
    "библейское мировоззрение": ("библейск", "мировоззрен"),
}


def _read_blocks(path: Path) -> list[tuple[int, str]]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    out: list[tuple[int, str]] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        idx = 1 if lines[0].isdigit() else 0
        match = _TIME_RE.match(lines[idx]) if idx < len(lines) else None
        if not match:
            continue
        seconds = int(match.group("h")) * 3600 + int(match.group("m")) * 60 + int(match.group("s"))
        text = " ".join(lines[idx + 1:]).strip()
        if text:
            out.append((seconds, text))
    return out


def sampled_srt_to_timed_text(path: Path, max_chars: int = 9000) -> str:
    """Sample chronological windows across the entire SRT, not just its beginning."""
    blocks = _read_blocks(Path(path))
    if not blocks:
        return ""
    rendered = [f"[{sec // 60:02d}:{sec % 60:02d}] {text}" for sec, text in blocks]
    if sum(len(item) + 1 for item in rendered) <= max_chars:
        return "\n".join(rendered)

    bucket_count = max(6, min(14, max_chars // 650))
    indexes: list[int] = []
    last = len(rendered) - 1
    for bucket in range(bucket_count):
        center = round(bucket * last / max(1, bucket_count - 1))
        for offset in (-1, 0, 1):
            idx = max(0, min(last, center + offset))
            if idx not in indexes:
                indexes.append(idx)
    indexes.sort()

    result: list[str] = []
    used = 0
    for idx in indexes:
        item = rendered[idx]
        if result and used + len(item) + 1 > max_chars:
            break
        result.append(item)
        used += len(item) + 1
    return "\n".join(result)


def _book_is_evidenced(ref: str, evidence_lower: str) -> bool:
    ref_lower = ref.lower()
    matching = [group for group in _BOOK_GROUPS if any(alias in ref_lower for alias in group)]
    if matching:
        return any(any(alias in evidence_lower for alias in group) for group in matching)
    words = [word.lower() for word in _WORD_RE.findall(ref)]
    return any(word in evidence_lower for word in words)


def _quote_is_evidenced(text: str, evidence: str) -> bool:
    words = list(dict.fromkeys(word.lower() for word in _WORD_RE.findall(text)))
    if len(words) < 5:
        return False
    evidence_words = set(word.lower() for word in _WORD_RE.findall(evidence))
    matched = sum(word in evidence_words for word in words)
    return matched >= max(5, int(len(words) * 0.7))


def _verified_scripture(items: Any, evidence: str) -> list[dict[str, str]]:
    if not isinstance(items, list) or not evidence.strip():
        return []
    compact_evidence = re.sub(r"\s+", "", evidence.lower())
    evidence_lower = evidence.lower()
    out: list[dict[str, str]] = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("ref") or "").strip()[:80]
        text = str(item.get("text_ru") or "").strip()[:500]
        match = _REF_RE.search(ref)
        if not ref or not match:
            continue
        marker = f"{int(match.group('c'))}:{int(match.group('v'))}"
        if marker not in compact_evidence or not _book_is_evidenced(ref, evidence_lower):
            continue
        if any(row["ref"].lower() == ref.lower() for row in out):
            continue
        out.append({"ref": ref, "text_ru": text if _quote_is_evidenced(text, evidence) else ""})
    return out[:5]


def _clean_hashtags(items: Any, title_line: str) -> list[str]:
    raw = items if isinstance(items, list) else []
    out: list[str] = []
    for item in raw[:10]:
        tag = normalize_hashtag(str(item or ""))
        body = tag.lstrip("#")
        if not tag or len(body) > 38 or "blueprintforthinkingwith" in body.lower():
            continue
        if tag not in out:
            out.append(tag)
    author = known_ru_author_from_text(title_line) or known_author_from_text(title_line)
    if author:
        author_tag = normalize_hashtag(author)
        if author_tag:
            out = [tag for tag in out if tag != author_tag]
            out.insert(0, author_tag)
    return out[:8]


def _claim_supported(text: str, evidence_lower: str) -> bool:
    candidate = str(text or "").lower()
    for phrase, roots in _GENERIC_CLAIMS.items():
        if phrase in candidate and not all(root in evidence_lower for root in roots):
            return False
    return True


def sanitize_card(card: dict | None, title_line: str, evidence: str) -> dict | None:
    if not isinstance(card, dict):
        return card
    result = dict(card)
    evidence_lower = evidence.lower()
    result["scripture_references"] = _verified_scripture(result.get("scripture_references"), evidence)
    result["hashtags"] = _clean_hashtags(result.get("hashtags"), title_line)

    if not evidence.strip():
        result["compact_subtitles"] = []
        result["key_theological_terms"] = []
        return result

    compact = result.get("compact_subtitles") or []
    if isinstance(compact, list):
        result["compact_subtitles"] = [
            str(item).strip() for item in compact[:6]
            if str(item).strip() and _claim_supported(str(item), evidence_lower)
        ]
    for field in ("telegram_description", "youtube_description"):
        value = str(result.get(field) or "").strip()
        if value and not _claim_supported(value, evidence_lower):
            result[field] = title_line
    return result


def install_livedub_info_guard() -> None:
    """Install once. The public function signatures remain unchanged."""
    from services import livedub_info as module

    if getattr(module, "_MP3BOT_INFO_GUARD_INSTALLED", False):
        return
    original = module.build_livedub_info_card
    module.srt_to_timed_text = sampled_srt_to_timed_text

    async def guarded_build(title_line, dub_srt_path=None, *, source_url="", force=False):
        card = await original(title_line, dub_srt_path, source_url=source_url, force=force)
        evidence = ""
        if dub_srt_path and Path(dub_srt_path).exists():
            evidence = sampled_srt_to_timed_text(Path(dub_srt_path), max_chars=9000)
        return sanitize_card(card, str(title_line or ""), evidence)

    module.build_livedub_info_card = guarded_build
    module._MP3BOT_INFO_GUARD_INSTALLED = True
