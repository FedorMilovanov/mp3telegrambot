#!/usr/bin/env python3
"""Archive quality/readout helpers for prompt A/B and cross-run learning.

This is intentionally read-only. It summarizes the durable generated_pages
archive so the owner can compare prompt variants and spot recurring quality
warnings by author/format without digging through logs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.generated_pages import query_generated_pages


@dataclass(frozen=True)
class ArchiveQualitySummary:
    total: int
    status_counts: dict[str, int]
    prompt_variant_counts: dict[str, int]
    warning_counts: dict[str, int]
    warning_by_author: dict[str, int]
    partial_segments: int
    caption_trimmed: int
    timestamp_low: int
    title_topic_warnings: int


@dataclass(frozen=True)
class PromptVariantQualityStats:
    variant: str
    total: int
    warning_total: int
    partial_segments: int
    caption_trimmed: int
    timestamp_low: int
    title_topic_warnings: int
    avg_timestamp_coverage: float
    quality_score: int


@dataclass(frozen=True)
class AuthorQualityProfile:
    author: str
    total: int
    warning_total: int
    partial_segments: int
    formats: tuple[str, ...]
    recommendation: str


def _norm(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    return text or fallback


def _warning_kind(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return "unknown"
    if ":" in text:
        return text.split(":", 1)[0].strip() or "unknown"
    return text[:80]


def collect_archive_quality_summary(*, limit: int = 50, base_dir: Path | None = None) -> ArchiveQualitySummary:
    records = query_generated_pages(limit=max(1, min(int(limit or 50), 50)), base_dir=base_dir)
    status_counts: Counter[str] = Counter()
    prompt_variant_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    warning_by_author: Counter[str] = Counter()
    partial_segments = 0
    caption_trimmed = 0
    timestamp_low = 0
    title_topic_warnings = 0

    for r in records:
        status_counts[_norm(r.get("publication_status"))] += 1
        prompt_variant_counts[_norm(r.get("prompt_variant"), "default")] += 1
        if _norm(r.get("segments_status"), "complete") != "complete":
            partial_segments += 1
        try:
            total = int(r.get("caption_timestamps_total") or 0)
            shown = int(r.get("caption_timestamps_shown") or 0)
        except (TypeError, ValueError):
            total = shown = 0
        if total > shown >= 0:
            caption_trimmed += 1

        warnings = r.get("quality_warnings") or []
        if isinstance(warnings, str):
            warnings = [warnings]
        for w in warnings:
            kind = _warning_kind(str(w))
            warning_counts[kind] += 1
            warning_by_author[_norm(r.get("author"), "Автор не указан")] += 1
            if kind == "timestamp":
                timestamp_low += 1
            if kind == "title-topic":
                title_topic_warnings += 1

    return ArchiveQualitySummary(
        total=len(records),
        status_counts=dict(status_counts),
        prompt_variant_counts=dict(prompt_variant_counts),
        warning_counts=dict(warning_counts),
        warning_by_author=dict(warning_by_author),
        partial_segments=partial_segments,
        caption_trimmed=caption_trimmed,
        timestamp_low=timestamp_low,
        title_topic_warnings=title_topic_warnings,
    )


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _record_warning_kinds(record: dict[str, Any]) -> list[str]:
    warnings = record.get("quality_warnings") or []
    if isinstance(warnings, str):
        warnings = [warnings]
    return [_warning_kind(str(w)) for w in warnings if str(w).strip()]


def _quality_score(
    *,
    total: int,
    warning_total: int,
    partial_segments: int,
    caption_trimmed: int,
    timestamp_low: int,
    title_topic_warnings: int,
    avg_timestamp_coverage: float,
) -> int:
    if total <= 0:
        return 0
    score = 100.0
    score -= (warning_total / total) * 10
    score -= (partial_segments / total) * 14
    score -= (timestamp_low / total) * 10
    score -= (title_topic_warnings / total) * 8
    score -= (caption_trimmed / total) * 2
    if avg_timestamp_coverage and avg_timestamp_coverage < 0.75:
        score -= (0.75 - avg_timestamp_coverage) * 30
    return max(0, min(100, round(score)))


def collect_prompt_variant_quality(*, limit: int = 50, base_dir: Path | None = None) -> list[PromptVariantQualityStats]:
    """Return quality score per prompt variant from recent archive records."""
    records = query_generated_pages(limit=max(1, min(int(limit or 50), 50)), base_dir=base_dir)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        grouped[_norm(r.get("prompt_variant"), "default")].append(r)

    out: list[PromptVariantQualityStats] = []
    for variant, items in grouped.items():
        warning_total = 0
        partial_segments = 0
        caption_trimmed = 0
        timestamp_low = 0
        title_topic_warnings = 0
        coverage_values: list[float] = []
        for r in items:
            kinds = _record_warning_kinds(r)
            warning_total += len(kinds)
            timestamp_low += sum(1 for k in kinds if k == "timestamp")
            title_topic_warnings += sum(1 for k in kinds if k == "title-topic")
            if _norm(r.get("segments_status"), "complete") != "complete":
                partial_segments += 1
            try:
                total_ts = int(r.get("caption_timestamps_total") or 0)
                shown_ts = int(r.get("caption_timestamps_shown") or 0)
            except (TypeError, ValueError):
                total_ts = shown_ts = 0
            if total_ts > shown_ts >= 0:
                caption_trimmed += 1
            cov = _safe_float(r.get("timestamp_coverage_ratio"), 0.0)
            if cov:
                coverage_values.append(cov)
        avg_cov = sum(coverage_values) / len(coverage_values) if coverage_values else 0.0
        total = len(items)
        out.append(PromptVariantQualityStats(
            variant=variant,
            total=total,
            warning_total=warning_total,
            partial_segments=partial_segments,
            caption_trimmed=caption_trimmed,
            timestamp_low=timestamp_low,
            title_topic_warnings=title_topic_warnings,
            avg_timestamp_coverage=avg_cov,
            quality_score=_quality_score(
                total=total,
                warning_total=warning_total,
                partial_segments=partial_segments,
                caption_trimmed=caption_trimmed,
                timestamp_low=timestamp_low,
                title_topic_warnings=title_topic_warnings,
                avg_timestamp_coverage=avg_cov,
            ),
        ))
    return sorted(out, key=lambda x: (-x.quality_score, -x.total, x.variant))


def _author_recommendation(*, warning_total: int, partial_segments: int, formats: set[str]) -> str:
    if partial_segments:
        return "timestamp coverage: inspect/repair before trusting segments"
    if warning_total:
        return "review warning patterns before using as prompt benchmark"
    if "qa" in formats:
        return "stable Q&A candidate for transcript-style A/B runs"
    return "stable candidate for prompt A/B baseline"


def collect_author_quality_profiles(*, limit: int = 50, base_dir: Path | None = None) -> list[AuthorQualityProfile]:
    """Summarize recurring author/profile quality for cross-run learning."""
    records = query_generated_pages(limit=max(1, min(int(limit or 50), 50)), base_dir=base_dir)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        grouped[_norm(r.get("author"), "Автор не указан")].append(r)

    out: list[AuthorQualityProfile] = []
    for author, items in grouped.items():
        warning_total = sum(len(_record_warning_kinds(r)) for r in items)
        partial_segments = sum(1 for r in items if _norm(r.get("segments_status"), "complete") != "complete")
        formats = {_norm(r.get("format"), "unknown") for r in items}
        out.append(AuthorQualityProfile(
            author=author,
            total=len(items),
            warning_total=warning_total,
            partial_segments=partial_segments,
            formats=tuple(sorted(formats)),
            recommendation=_author_recommendation(
                warning_total=warning_total,
                partial_segments=partial_segments,
                formats=formats,
            ),
        ))
    return sorted(out, key=lambda x: (-x.warning_total, -x.partial_segments, -x.total, x.author))


def _fmt_delta(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def _top(mapping: dict[str, int], limit: int = 6) -> list[tuple[str, int]]:
    return sorted(mapping.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


def format_archive_quality_report(*, limit: int = 50, base_dir: Path | None = None) -> str:
    summary = collect_archive_quality_summary(limit=limit, base_dir=base_dir)
    lines = [
        "🧪 <b>Archive quality / prompt A-B</b>",
        "",
        f"records=<code>{summary.total}</code> "
        f"partial_segments=<code>{summary.partial_segments}</code> "
        f"caption_trimmed=<code>{summary.caption_trimmed}</code>",
        f"timestamp_warnings=<code>{summary.timestamp_low}</code> "
        f"title_topic_warnings=<code>{summary.title_topic_warnings}</code>",
        "",
        "<b>Status</b>",
    ]
    if summary.status_counts:
        lines.extend(f"- <code>{k}</code>: <code>{v}</code>" for k, v in _top(summary.status_counts))
    else:
        lines.append("- no archive records")

    lines.append("")
    lines.append("<b>Prompt variants</b>")
    if summary.prompt_variant_counts:
        lines.extend(f"- <code>{k}</code>: <code>{v}</code>" for k, v in _top(summary.prompt_variant_counts))
    else:
        lines.append("- no prompt variant data")

    variant_stats = collect_prompt_variant_quality(limit=limit, base_dir=base_dir)
    lines.append("")
    lines.append("<b>Prompt variant quality score</b>")
    if variant_stats:
        for item in variant_stats[:6]:
            cov = f", cov={item.avg_timestamp_coverage:.0%}" if item.avg_timestamp_coverage else ""
            lines.append(
                f"- <code>{item.variant}</code>: score=<code>{item.quality_score}</code> "
                f"records=<code>{item.total}</code> warnings=<code>{item.warning_total}</code>"
                f" partial=<code>{item.partial_segments}</code>{cov}"
            )
    else:
        lines.append("- no prompt variant data")

    lines.append("")
    lines.append("<b>Quality warning kinds</b>")
    if summary.warning_counts:
        lines.extend(f"- <code>{k}</code>: <code>{v}</code>" for k, v in _top(summary.warning_counts))
    else:
        lines.append("- no quality warnings in sampled archive records")

    lines.append("")
    lines.append("<b>Authors with warnings</b>")
    if summary.warning_by_author:
        lines.extend(f"- {k}: <code>{v}</code>" for k, v in _top(summary.warning_by_author))
    else:
        lines.append("- none")

    author_profiles = collect_author_quality_profiles(limit=limit, base_dir=base_dir)
    lines.append("")
    lines.append("<b>Cross-run author/profile hints</b>")
    if author_profiles:
        for item in author_profiles[:6]:
            fmt = ",".join(item.formats)
            lines.append(
                f"- {item.author}: records=<code>{item.total}</code> "
                f"warnings=<code>{item.warning_total}</code> formats=<code>{fmt}</code> — {item.recommendation}"
            )
    else:
        lines.append("- no author profile data")

    lines.append("")
    lines.append(
        "Use <code>PROMPT_EXPERIMENT_TAG</code> before a run to compare variants here. "
        "This is read-only and does not call Gemini."
    )
    return "\n".join(lines)

@dataclass(frozen=True)
class PromptVariantComparison:
    left: PromptVariantQualityStats | None
    right: PromptVariantQualityStats | None
    winner: str
    score_delta: int
    warning_delta: int
    partial_delta: int
    recommendation: str


def compare_prompt_variants(
    left_variant: str,
    right_variant: str,
    *,
    limit: int = 50,
    base_dir: Path | None = None,
) -> PromptVariantComparison:
    """Compare two prompt variants using read-only archive quality stats."""
    stats = {s.variant: s for s in collect_prompt_variant_quality(limit=limit, base_dir=base_dir)}
    left = stats.get(_norm(left_variant, "default"))
    right = stats.get(_norm(right_variant, "default"))
    if not left or not right:
        missing = []
        if not left:
            missing.append(_norm(left_variant, "default"))
        if not right:
            missing.append(_norm(right_variant, "default"))
        return PromptVariantComparison(
            left=left,
            right=right,
            winner="",
            score_delta=0,
            warning_delta=0,
            partial_delta=0,
            recommendation="missing variant(s): " + ", ".join(missing),
        )

    score_delta = right.quality_score - left.quality_score
    warning_delta = right.warning_total - left.warning_total
    partial_delta = right.partial_segments - left.partial_segments
    if abs(score_delta) < 3:
        winner = "tie"
        recommendation = "scores are effectively tied; inspect page quality manually before changing defaults"
    elif score_delta > 0:
        winner = right.variant
        recommendation = f"{right.variant} looks better in archive warnings; confirm with manual page review"
    else:
        winner = left.variant
        recommendation = f"{left.variant} looks better in archive warnings; keep current default unless manual review disagrees"
    return PromptVariantComparison(
        left=left,
        right=right,
        winner=winner,
        score_delta=score_delta,
        warning_delta=warning_delta,
        partial_delta=partial_delta,
        recommendation=recommendation,
    )


def format_prompt_variant_comparison(
    left_variant: str,
    right_variant: str,
    *,
    limit: int = 50,
    base_dir: Path | None = None,
) -> str:
    cmp = compare_prompt_variants(left_variant, right_variant, limit=limit, base_dir=base_dir)
    lines = [
        "🧪 <b>Prompt variant comparison</b>",
        "",
        f"left=<code>{_norm(left_variant, 'default')}</code> right=<code>{_norm(right_variant, 'default')}</code> records_limit=<code>{limit}</code>",
    ]
    if not cmp.left or not cmp.right:
        lines.append("⚠️ " + cmp.recommendation)
        return "\n".join(lines)

    def row(label: str, item: PromptVariantQualityStats) -> str:
        cov = f" coverage=<code>{item.avg_timestamp_coverage:.0%}</code>" if item.avg_timestamp_coverage else ""
        return (
            f"- <b>{label}</b> <code>{item.variant}</code>: "
            f"score=<code>{item.quality_score}</code> records=<code>{item.total}</code> "
            f"warnings=<code>{item.warning_total}</code> partial=<code>{item.partial_segments}</code>"
            f" timestamp=<code>{item.timestamp_low}</code> title-topic=<code>{item.title_topic_warnings}</code>{cov}"
        )

    lines.append(row("Left", cmp.left))
    lines.append(row("Right", cmp.right))
    lines.append("")
    lines.append(
        f"delta(right-left): score=<code>{_fmt_delta(cmp.score_delta)}</code> "
        f"warnings=<code>{_fmt_delta(cmp.warning_delta)}</code> "
        f"partial=<code>{_fmt_delta(cmp.partial_delta)}</code>"
    )
    lines.append(f"winner=<code>{cmp.winner}</code>")
    lines.append("Recommendation: " + cmp.recommendation)
    lines.append("")
    lines.append("Archive warnings are a filter, not a final judge: always inspect sample pages manually before promoting a variant.")
    return "\n".join(lines)


def archive_quality_payload(*, limit: int = 50, base_dir: Path | None = None) -> dict[str, Any]:
    """Machine-readable archive quality payload for exports/dashboards."""
    summary = collect_archive_quality_summary(limit=limit, base_dir=base_dir)
    variants = collect_prompt_variant_quality(limit=limit, base_dir=base_dir)
    authors = collect_author_quality_profiles(limit=limit, base_dir=base_dir)
    return {
        "limit": limit,
        "summary": {
            "total": summary.total,
            "status_counts": summary.status_counts,
            "prompt_variant_counts": summary.prompt_variant_counts,
            "warning_counts": summary.warning_counts,
            "warning_by_author": summary.warning_by_author,
            "partial_segments": summary.partial_segments,
            "caption_trimmed": summary.caption_trimmed,
            "timestamp_low": summary.timestamp_low,
            "title_topic_warnings": summary.title_topic_warnings,
        },
        "prompt_variants": [item.__dict__ for item in variants],
        "author_profiles": [item.__dict__ for item in authors],
    }


def _markdown_payload(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# Archive quality report",
        "",
        f"Records: {summary.get('total', 0)}",
        f"Partial segments: {summary.get('partial_segments', 0)}",
        f"Caption trimmed: {summary.get('caption_trimmed', 0)}",
        f"Timestamp warnings: {summary.get('timestamp_low', 0)}",
        f"Title-topic warnings: {summary.get('title_topic_warnings', 0)}",
        "",
        "## Prompt variants",
    ]
    variants = payload.get("prompt_variants") or []
    if variants:
        for item in variants:
            lines.append(
                f"- `{item.get('variant')}`: score={item.get('quality_score')} "
                f"records={item.get('total')} warnings={item.get('warning_total')} "
                f"partial={item.get('partial_segments')}"
            )
    else:
        lines.append("- no prompt variant data")

    lines.append("")
    lines.append("## Author/profile hints")
    authors = payload.get("author_profiles") or []
    if authors:
        for item in authors:
            formats = ",".join(item.get("formats") or [])
            lines.append(
                f"- {item.get('author')}: records={item.get('total')} "
                f"warnings={item.get('warning_total')} formats={formats} — {item.get('recommendation')}"
            )
    else:
        lines.append("- no author profile data")

    lines.append("")
    lines.append("## Warning kinds")
    warnings = summary.get("warning_counts") or {}
    if warnings:
        for key, value in sorted(warnings.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- `{key}`: {value}")
    else:
        lines.append("- no warning kinds")
    lines.append("")
    return "\n".join(lines)


def write_archive_quality_exports(
    *,
    limit: int = 50,
    base_dir: Path | None = None,
    export_dir: Path | None = None,
) -> dict[str, str]:
    """Write machine/human archive-quality exports and return paths."""
    import json

    base = Path(base_dir) if base_dir else None
    out_dir = Path(export_dir) if export_dir else ((base or Path("data/generated_pages")) / "quality")
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = archive_quality_payload(limit=limit, base_dir=base)
    json_path = out_dir / "archive_quality.json"
    md_path = out_dir / "archive_quality.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_markdown_payload(payload), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


async def awrite_archive_quality_exports(**kwargs) -> dict[str, str]:
    import asyncio

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: write_archive_quality_exports(**kwargs))

@dataclass(frozen=True)
class ArchiveQualityRecord:
    video_id: str
    title: str
    author: str
    prompt_variant: str
    publication_status: str
    warning_count: int
    warning_kinds: tuple[str, ...]
    segments_status: str
    timestamp_coverage_ratio: float
    caption_timestamps_total: int
    caption_timestamps_shown: int
    updated_at: int
    synopsis_url: str = ""
    study_url: str = ""
    reflection_url: str = ""

    @property
    def needs_attention(self) -> bool:
        return bool(
            self.warning_count
            or self.publication_status != "complete"
            or self.segments_status != "complete"
            or (self.timestamp_coverage_ratio and self.timestamp_coverage_ratio < 0.75)
        )


def collect_quality_records(
    *,
    limit: int = 30,
    warnings_only: bool = True,
    base_dir: Path | None = None,
) -> list[ArchiveQualityRecord]:
    """Return recent records sorted by how urgently they need manual review."""
    records = query_generated_pages(limit=max(1, min(int(limit or 30), 50)), base_dir=base_dir)
    out: list[ArchiveQualityRecord] = []
    for r in records:
        kinds = tuple(_record_warning_kinds(r))
        try:
            total_ts = int(r.get("caption_timestamps_total") or 0)
            shown_ts = int(r.get("caption_timestamps_shown") or 0)
        except (TypeError, ValueError):
            total_ts = shown_ts = 0
        item = ArchiveQualityRecord(
            video_id=_norm(r.get("video_id"), ""),
            title=_norm(r.get("title"), "Без названия"),
            author=_norm(r.get("author"), "Автор не указан"),
            prompt_variant=_norm(r.get("prompt_variant"), "default"),
            publication_status=_norm(r.get("publication_status")),
            warning_count=len(kinds),
            warning_kinds=kinds,
            segments_status=_norm(r.get("segments_status"), "complete"),
            timestamp_coverage_ratio=_safe_float(r.get("timestamp_coverage_ratio"), 0.0),
            caption_timestamps_total=total_ts,
            caption_timestamps_shown=shown_ts,
            updated_at=int(r.get("updated_at") or 0),
            synopsis_url=str(r.get("synopsis_url") or ""),
            study_url=str(r.get("study_url") or ""),
            reflection_url=str(r.get("reflection_url") or ""),
        )
        if warnings_only and not item.needs_attention:
            continue
        out.append(item)

    def severity(item: ArchiveQualityRecord) -> tuple[int, int, float, int]:
        sev = 0
        if item.publication_status != "complete":
            sev += 8
        if item.segments_status != "complete":
            sev += 6
        sev += item.warning_count * 3
        if item.timestamp_coverage_ratio and item.timestamp_coverage_ratio < 0.75:
            sev += int((0.75 - item.timestamp_coverage_ratio) * 20)
        return (-sev, -item.warning_count, item.timestamp_coverage_ratio or 1.0, -item.updated_at)

    return sorted(out, key=severity)


def format_quality_records_report(
    *,
    limit: int = 20,
    warnings_only: bool = True,
    base_dir: Path | None = None,
) -> str:
    records = collect_quality_records(limit=limit, warnings_only=warnings_only, base_dir=base_dir)
    title = "records needing attention" if warnings_only else "recent quality records"
    lines = [
        f"🧯 <b>Archive quality records: {title}</b>",
        "",
        f"limit=<code>{limit}</code> mode=<code>{'warnings' if warnings_only else 'all'}</code>",
        "",
    ]
    if not records:
        lines.append("No matching records in the sampled archive window.")
        return "\n".join(lines)
    for idx, item in enumerate(records[:limit], 1):
        warning_text = ", ".join(item.warning_kinds) if item.warning_kinds else "none"
        cov = f" coverage=<code>{item.timestamp_coverage_ratio:.0%}</code>" if item.timestamp_coverage_ratio else ""
        trim = ""
        if item.caption_timestamps_total > item.caption_timestamps_shown >= 0:
            trim = f" caption_ts=<code>{item.caption_timestamps_shown}/{item.caption_timestamps_total}</code>"
        links = []
        if item.synopsis_url:
            links.append(f'<a href="{item.synopsis_url}">Synopsis</a>')
        if item.study_url:
            links.append(f'<a href="{item.study_url}">Study</a>')
        if item.reflection_url:
            links.append(f'<a href="{item.reflection_url}">Reflection</a>')
        link_text = " · ".join(links)
        lines.append(
            f"{idx}. <b>{item.title}</b> — {item.author}\n"
            f"   video=<code>{item.video_id}</code> variant=<code>{item.prompt_variant}</code> "
            f"status=<code>{item.publication_status}</code> segments=<code>{item.segments_status}</code>{cov}{trim}\n"
            f"   warnings=<code>{warning_text}</code>" + (f"\n   {link_text}" if link_text else "")
        )
    lines.append("")
    lines.append("Use /repairpage for deterministic Telegraph repair, or rerun with a prompt variant after inspecting the linked pages.")
    return "\n".join(lines)


def recommend_prompt_variant(*, limit: int = 50, base_dir: Path | None = None, min_records: int = 2) -> str:
    """Read-only recommendation for prompt variant promotion based on archive warnings."""
    stats = collect_prompt_variant_quality(limit=limit, base_dir=base_dir)
    lines = [
        "🧭 <b>Prompt variant recommendation</b>",
        "",
        f"sample=<code>{limit}</code> min_records=<code>{min_records}</code>",
        "",
    ]
    eligible = [s for s in stats if s.total >= min_records]
    if not stats:
        lines.append("No prompt variant data in archive. Set <code>PROMPT_EXPERIMENT_TAG</code> before runs.")
        return "\n".join(lines)
    if not eligible:
        lines.append("Not enough records per variant for recommendation yet.")
        lines.append("Observed variants:")
        for s in stats[:8]:
            lines.append(f"- <code>{s.variant}</code>: records=<code>{s.total}</code> score=<code>{s.quality_score}</code>")
        return "\n".join(lines)

    best = eligible[0]
    lines.append(
        f"Best current candidate: <code>{best.variant}</code> "
        f"score=<code>{best.quality_score}</code> records=<code>{best.total}</code>"
    )
    lines.append("")
    lines.append("<b>Ranking</b>")
    for s in eligible[:8]:
        cov = f" coverage=<code>{s.avg_timestamp_coverage:.0%}</code>" if s.avg_timestamp_coverage else ""
        lines.append(
            f"- <code>{s.variant}</code>: score=<code>{s.quality_score}</code> "
            f"records=<code>{s.total}</code> warnings=<code>{s.warning_total}</code> "
            f"partial=<code>{s.partial_segments}</code>{cov}"
        )
    lines.append("")
    lines.append("Recommendation is advisory: inspect representative pages before changing defaults.")
    return "\n".join(lines)
