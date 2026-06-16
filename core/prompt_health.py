#!/usr/bin/env python3
"""Prompt health diagnostics.

This is a static/admin tool, not a generation-time constraint. It helps track
prompt bloat and the balance between positive reasoning instructions and
negative bans as the bot evolves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable

from core.prompt_compactor import compact_prompt_for_generation


_NEGATIVE_MARKERS = (
    "НЕ ", "НЕЛЬЗЯ", "ЗАПРЕЩ", "Никогда", "никогда", "Не пиши", "не пиши",
    "не используй", "НЕ используй", "не выдум", "НЕ выдум",
)
_POSITIVE_MARKERS = (
    "РАЗРЕШЕНО", "ЖЕЛАТЕЛЬНО", "ПРАВИЛЬНО", "ХОРОШО", "можно", "Можно",
    "сначала", "Сначала", "покажи", "Покажи", "используй", "Используй",
    "разрешён", "разрешено", "positive", "reasoning",
)

_LEAKY_LITERAL_PATTERNS = (
    "позиция канала",
    "нерв позиции канала",
    "редакции/каналу",
    "наш канал",
    "позицию канала",
    "КОНФЕССИОНАЛЬНАЯ РАМКА КАНАЛА",
    "В работе X автор Y",
    "Y в «X»",
    "Русский Автор, *«Русское название»*",
    "Правильно:   - Джон МакАртур, *Safe in the Arms of God*",
    "Лоусон показывает",
    "Бузениц доказывает",
    "Странный огонь",
    "Младенцы во славе",
    "Спасение младенцев",
    "Чередуй полное имя",
    "проповедник показывает",
    "Джон МакАртур анализирует",
)


def find_leaky_prompt_literals(text: str) -> list[str]:
    """Return literal prompt phrases known to leak into generated pages.

    This is intentionally narrow: these are not general banned concepts, but
    exact strings that should not appear in live prompts because models may copy
    them despite surrounding negative wording.
    """
    src = str(text or "")
    low = src.lower()
    found: list[str] = []
    for pattern in _LEAKY_LITERAL_PATTERNS:
        haystack = low if pattern.islower() else src
        needle = pattern if not pattern.islower() else pattern.lower()
        if needle in haystack and pattern not in found:
            found.append(pattern)
    return found


@dataclass(frozen=True)
class PromptHealthItem:
    name: str
    chars: int
    negative_markers: int
    positive_markers: int
    final_check_blocks: int
    compacted_chars: int = 0
    compaction_saved_chars: int = 0
    compaction_removed_lines: int = 0
    leaky_literals: list[str] = field(default_factory=list)

    @property
    def negative_to_positive_ratio(self) -> float:
        return self.negative_markers / max(self.positive_markers, 1)


def _count_markers(text: str, markers: Iterable[str]) -> int:
    return sum(text.count(m) for m in markers)


def analyze_prompt_text(name: str, text: str) -> PromptHealthItem:
    text = str(text or "")
    compacted = compact_prompt_for_generation(text)
    return PromptHealthItem(
        name=name,
        chars=len(text),
        negative_markers=_count_markers(text, _NEGATIVE_MARKERS),
        positive_markers=_count_markers(text, _POSITIVE_MARKERS),
        final_check_blocks=len(re.findall(r"ФИНАЛЬН[А-ЯЁ\s]+ПРОВЕРК|ФИНАЛЬН[А-ЯЁ\s]+САМОПРОВЕРК", text, flags=re.IGNORECASE)),
        compacted_chars=compacted.compacted_chars,
        compaction_saved_chars=compacted.saved_chars,
        compaction_removed_lines=compacted.removed_lines,
        leaky_literals=find_leaky_prompt_literals(text),
    )


def collect_prompt_health() -> list[PromptHealthItem]:
    from core import prompts

    items = [
        analyze_prompt_text("SYNOPSIS_PROMPT_V2", getattr(prompts, "SYNOPSIS_PROMPT_V2", "")),
        analyze_prompt_text("SYNOPSIS_PROMPT_QA", getattr(prompts, "SYNOPSIS_PROMPT_QA", "")),
        analyze_prompt_text("STUDY_ANALYSIS_PROMPT", getattr(prompts, "STUDY_ANALYSIS_PROMPT", "")),
        analyze_prompt_text("REFLECTION_APPLICATION_PROMPT", getattr(prompts, "REFLECTION_APPLICATION_PROMPT", "")),
    ]
    # Audio prompt is dynamic; use representative long sermon profile.
    try:
        audio = prompts.build_audio_analysis_prompt("T", "A", "55:00", 3300, mode="deep")
        items.append(analyze_prompt_text("AUDIO_ANALYSIS_PROMPT(deep sample)", audio))
    except Exception:
        pass
    return items


def format_prompt_health_report() -> str:
    items = collect_prompt_health()
    total_chars = sum(i.chars for i in items)
    lines = ["🧪 <b>Prompt health</b>", "", f"Total chars: <code>{total_chars}</code>", ""]
    for item in items:
        ratio = item.negative_to_positive_ratio
        warn = " ⚠️" if ratio > 2.5 or item.final_check_blocks > 2 or item.leaky_literals else ""
        lines.append(
            f"<b>{item.name}</b>{warn}\n"
            f"chars=<code>{item.chars}</code> "
            f"neg=<code>{item.negative_markers}</code> "
            f"pos=<code>{item.positive_markers}</code> "
            f"ratio=<code>{ratio:.2f}</code> "
            f"final_checks=<code>{item.final_check_blocks}</code> "
            f"compact=<code>{item.compacted_chars}</code> "
            f"saved=<code>{item.compaction_saved_chars}</code> "
            f"leaks=<code>{len(item.leaky_literals)}</code>"
        )
        if item.leaky_literals:
            lines.append("leaky_literals: <code>" + ", ".join(item.leaky_literals[:5]) + "</code>")
    lines.append("")
    lines.append("Цель: постепенно снижать ratio, дубли финальных проверок и leaky_literals, не удаляя production guardrails без тестов.")
    return "\n".join(lines)
