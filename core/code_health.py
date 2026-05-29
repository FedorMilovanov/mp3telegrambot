#!/usr/bin/env python3
"""Static code-health diagnostics for regex/postprocess bloat.

This is an admin/readout tool. It does not change generation behavior. The goal
is to keep the growing deterministic layer honest: useful guardrails are fine,
but we should see where regex/postprocess complexity accumulates.
"""
from __future__ import annotations

import json
import os
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
class CodeHealthDelta:
    total_regex_delta: int = 0
    total_postprocess_delta: int = 0
    files_scanned_delta: int = 0


@dataclass(frozen=True)
class CodeHealthReport:
    files_scanned: int
    total_regex_markers: int
    total_postprocess_markers: int
    top_files: tuple[FileHealthItem, ...]
    source_title_registry_entries: int
    typo_replacements: int
    regex_threshold: int = 300
    structured_block_normalizer: bool = False
    typed_source_cards: bool = False

    @property
    def regex_over_threshold(self) -> bool:
        return self.total_regex_markers > self.regex_threshold

    @property
    def high_regex_files(self) -> tuple[FileHealthItem, ...]:
        return tuple(i for i in self.top_files if i.regex_markers >= 40)


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


def _regex_threshold() -> int:
    try:
        return int(os.getenv("CODE_HEALTH_REGEX_THRESHOLD", "300"))
    except (TypeError, ValueError):
        return 300


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
        regex_threshold=_regex_threshold(),
        structured_block_normalizer=(root / "core" / "structured_blocks.py").exists(),
        typed_source_cards=("class SourceCard" in (source_titles.read_text(encoding="utf-8", errors="replace") if source_titles.exists() else "")),
    )



def code_health_snapshot(report: CodeHealthReport) -> dict:
    """Small stable JSON-serializable snapshot for trend tracking."""
    return {
        "files_scanned": report.files_scanned,
        "total_regex_markers": report.total_regex_markers,
        "total_postprocess_markers": report.total_postprocess_markers,
        "source_title_registry_entries": report.source_title_registry_entries,
        "typo_replacements": report.typo_replacements,
        "structured_block_normalizer": report.structured_block_normalizer,
        "typed_source_cards": report.typed_source_cards,
        "top_files": [
            {
                "path": item.path,
                "regex_markers": item.regex_markers,
                "postprocess_markers": item.postprocess_markers,
                "lines": item.lines,
            }
            for item in report.top_files
        ],
    }


def load_code_health_baseline(root: Path | str = ".", baseline_path: str | Path | None = None) -> dict | None:
    """Load optional baseline snapshot for advisory trend deltas."""
    base = Path(root)
    path = Path(baseline_path) if baseline_path else base / "docs" / "code_health_baseline.json"
    if not path.is_absolute():
        path = base / path
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def compare_code_health(report: CodeHealthReport, baseline: dict | None) -> CodeHealthDelta:
    if not baseline:
        return CodeHealthDelta()
    return CodeHealthDelta(
        total_regex_delta=report.total_regex_markers - int(baseline.get("total_regex_markers") or 0),
        total_postprocess_delta=report.total_postprocess_markers - int(baseline.get("total_postprocess_markers") or 0),
        files_scanned_delta=report.files_scanned - int(baseline.get("files_scanned") or 0),
    )


def write_code_health_baseline(root: Path | str = ".", baseline_path: str | Path | None = None) -> Path:
    """Write current advisory snapshot. Intended for manual owner use, not CI mutation."""
    base = Path(root)
    report = collect_code_health(base)
    path = Path(baseline_path) if baseline_path else base / "docs" / "code_health_baseline.json"
    if not path.is_absolute():
        path = base / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(code_health_snapshot(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _fmt_delta(value: int) -> str:
    if value > 0:
        return f"+{value}"
    return str(value)

def format_code_health_report(root: Path | str = ".") -> str:
    report = collect_code_health(root)
    baseline = load_code_health_baseline(root)
    delta = compare_code_health(report, baseline)
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
        (
            "baseline_delta: "
            f"regex=<code>{_fmt_delta(delta.total_regex_delta)}</code> "
            f"post=<code>{_fmt_delta(delta.total_postprocess_delta)}</code> "
            f"files=<code>{_fmt_delta(delta.files_scanned_delta)}</code>"
            if baseline else
            "baseline_delta: <code>none</code> (create with tools/check_code_health.py --write-baseline)"
        ),
        f"structured_blocks=<code>{'yes' if report.structured_block_normalizer else 'no'}</code> "
        f"typed_source_cards=<code>{'yes' if report.typed_source_cards else 'no'}</code>",
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
        "для источников/авторов и blocks предпочитать typed normalizers, а не новые ad-hoc patterns."
    )
    return "\n".join(lines)
