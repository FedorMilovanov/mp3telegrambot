#!/usr/bin/env python3
"""Static code-health diagnostics for regex/postprocess bloat.

This is an admin/readout tool. It does not change generation behavior. The goal
is to keep the growing deterministic layer honest: useful guardrails are fine,
but we should see where regex/postprocess complexity accumulates.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_SCAN_DIRS = ("core", "converters", "services", "pipelines", "handlers")
_REGEX_MARKERS = ("re.compile", "re.sub", "re.search", "re.match", "re.finditer", "re.findall")
_POSTPROCESS_MARKERS = ("postprocess", "normalize_common_typos", "scrub_third_person", "audit_")


@dataclass(frozen=True)
class FileHealthItem:
    path: str
    regex_markers: int
    postprocess_markers: int
    lines: int


@dataclass(frozen=True)
class CodeHealthReport:
    files_scanned: int
    total_regex_markers: int
    total_postprocess_markers: int
    top_files: tuple[FileHealthItem, ...]
    source_title_registry_entries: int
    typo_replacements: int
    regex_threshold: int = 300

    @property
    def regex_over_threshold(self) -> bool:
        return self.total_regex_markers > self.regex_threshold


def _iter_py_files(root: Path, dirs: Iterable[str] = DEFAULT_SCAN_DIRS) -> list[Path]:
    files: list[Path] = []
    for d in dirs:
        base = root / d
        if not base.exists():
            continue
        files.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def _count_tuple_entries(source: str, marker: str) -> int:
    """Best-effort count for simple constant dict/tuple inventories."""
    start = source.find(marker)
    if start < 0:
        return 0
    chunk = source[start:start + 12000]
    return chunk.count("(") if "tuple" in marker or "REPLACEMENTS" in marker else chunk.count(":")


def collect_code_health(root: Path | str = ".") -> CodeHealthReport:
    root = Path(root)
    items: list[FileHealthItem] = []
    for path in _iter_py_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        regex_count = sum(text.count(m) for m in _REGEX_MARKERS)
        post_count = sum(text.lower().count(m.lower()) for m in _POSTPROCESS_MARKERS)
        if regex_count or post_count:
            items.append(FileHealthItem(
                path=str(path.relative_to(root)),
                regex_markers=regex_count,
                postprocess_markers=post_count,
                lines=text.count("\n") + 1,
            ))
    top = tuple(sorted(items, key=lambda x: (x.regex_markers + x.postprocess_markers, x.regex_markers), reverse=True)[:10])

    source_titles = root / "core" / "source_titles.py"
    source_title_entries = 0
    if source_titles.exists():
        txt = source_titles.read_text(encoding="utf-8", errors="replace")
        source_title_entries = txt.count('": "') + txt.count('"): "')

    text_utils = root / "core" / "text_utils.py"
    typo_replacements = 0
    if text_utils.exists():
        txt = text_utils.read_text(encoding="utf-8", errors="replace")
        start = txt.find("_COMMON_TYPO_REPLACEMENTS")
        end = txt.find("\n)", start)
        if start >= 0 and end > start:
            typo_replacements = txt[start:end].count('("')

    return CodeHealthReport(
        files_scanned=len(_iter_py_files(root)),
        total_regex_markers=sum(i.regex_markers for i in items),
        total_postprocess_markers=sum(i.postprocess_markers for i in items),
        top_files=top,
        source_title_registry_entries=source_title_entries,
        typo_replacements=typo_replacements,
    )


def format_code_health_report(root: Path | str = ".") -> str:
    report = collect_code_health(root)
    lines = [
        "🧰 <b>Code health: regex/postprocess</b>",
        "",
        f"files_scanned=<code>{report.files_scanned}</code> "
        f"regex_markers=<code>{report.total_regex_markers}</code> "
        f"postprocess_markers=<code>{report.total_postprocess_markers}</code>",
        f"source_title_registry_entries=<code>{report.source_title_registry_entries}</code> "
        f"typo_replacements=<code>{report.typo_replacements}</code>",
        (
            f"⚠️ regex_markers above soft threshold "
            f"<code>{report.regex_threshold}</code>: add regression tests for every new regex."
            if report.regex_over_threshold else
            f"regex_markers below soft threshold <code>{report.regex_threshold}</code>"
        ),
        "",
        "<b>Top complexity files</b>",
    ]
    for item in report.top_files:
        warn = " ⚠️" if item.regex_markers >= 40 else ""
        lines.append(
            f"<code>{item.path}</code>{warn}: "
            f"regex=<code>{item.regex_markers}</code> "
            f"post=<code>{item.postprocess_markers}</code> "
            f"lines=<code>{item.lines}</code>"
        )
    lines.append("")
    lines.append(
        "Policy: новые regex добавлять только с regression-тестом; "
        "для источников/авторов предпочитать registry, а не новые ad-hoc patterns."
    )
    return "\n".join(lines)
