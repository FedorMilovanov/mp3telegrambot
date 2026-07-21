#!/usr/bin/env python3
"""Conservative runtime prompt compaction.

This is not a semantic prompt rewrite. It removes repeated boilerplate lines that
are safe to dedupe because structured output and audit layers now enforce shape.
The goal is to reduce prompt bloat without deleting accumulated guardrails from
source constants.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_DEDUPE_EXACT_LINES = {
    "Верни только чистый JSON, без ```json и без пояснений.",
    "Верни только чистый JSON — без ```json, без пояснений до или после.",
    "Верни только чистый JSON, без ```json и без текста до/после.",
    "JSON валиден: только ключи outline и sections?",
    "outline совпадает с sections (title + time, без content)?",
    "Нет section без title или с пустым content?",
}


@dataclass(frozen=True)
class PromptCompactionResult:
    text: str
    original_chars: int
    compacted_chars: int
    removed_lines: int

    @property
    def saved_chars(self) -> int:
        return max(0, self.original_chars - self.compacted_chars)


def compact_prompt_for_generation(prompt: str) -> PromptCompactionResult:
    """Conservatively compact a prompt before sending it to Gemini.

    Rules:
    - collapse excessive blank lines;
    - dedupe exact repeated boilerplate lines from a small allowlist;
    - strip trailing spaces.

    No task-specific instructions, examples, source packs or user/audio metadata
    are removed.
    """
    original = str(prompt or "")
    seen: set[str] = set()
    removed = 0
    out: list[str] = []
    blank_run = 0
    for raw_line in original.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            blank_run += 1
            if blank_run <= 2:
                out.append("")
            else:
                removed += 1
            continue
        blank_run = 0
        normalized = line.strip()
        if normalized in _DEDUPE_EXACT_LINES:
            if normalized in seen:
                removed += 1
                continue
            seen.add(normalized)
        out.append(line)
    text = "\n".join(out).strip()
    # Final whitespace safety: no 4+ blank lines after joining.
    text2 = re.sub(r"\n{4,}", "\n\n\n", text)
    if text2 != text:
        removed += text.count("\n") - text2.count("\n")
        text = text2
    return PromptCompactionResult(
        text=text,
        original_chars=len(original),
        compacted_chars=len(text),
        removed_lines=max(0, removed),
    )
