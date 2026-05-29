"""Regression tests for v3 patch 87 — archive quality/prompt A-B readout."""

from pathlib import Path

from core.archive_quality import collect_archive_quality_summary, format_archive_quality_report
from core.generated_pages import build_generated_page_record, save_generated_page_record


def test_archive_quality_summary_groups_warnings_and_prompt_variants(tmp_path):
    save_generated_page_record(
        build_generated_page_record(
            video_id="a1",
            source_url="https://youtu.be/a1",
            title="T1",
            author="A",
            synopsis_url="https://telegra.ph/a1",
            publication_status="complete",
            prompt_variant="separate-v1",
            quality_warnings=["timestamp: low coverage", "title-topic: low overlap"],
            segments_status="partial",
            caption_timestamps_total=20,
            caption_timestamps_shown=7,
        ),
        base_dir=tmp_path,
    )
    save_generated_page_record(
        build_generated_page_record(
            video_id="a2",
            source_url="https://youtu.be/a2",
            title="T2",
            author="B",
            synopsis_url="https://telegra.ph/a2",
            publication_status="partial",
            prompt_variant="combined-v1",
            quality_warnings=["custom warning"],
        ),
        base_dir=tmp_path,
    )

    summary = collect_archive_quality_summary(limit=10, base_dir=tmp_path)
    assert summary.total == 2
    assert summary.status_counts["complete"] == 1
    assert summary.status_counts["partial"] == 1
    assert summary.prompt_variant_counts["separate-v1"] == 1
    assert summary.prompt_variant_counts["combined-v1"] == 1
    assert summary.warning_counts["timestamp"] == 1
    assert summary.warning_counts["title-topic"] == 1
    assert summary.partial_segments == 1
    assert summary.caption_trimmed == 1


def test_archive_quality_report_is_admin_html_readout(tmp_path):
    save_generated_page_record(
        build_generated_page_record(
            video_id="b1",
            source_url="https://youtu.be/b1",
            title="T",
            author="A",
            synopsis_url="https://telegra.ph/b1",
            prompt_variant="ab-test",
            quality_warnings=["timestamp: low coverage"],
        ),
        base_dir=tmp_path,
    )
    text = format_archive_quality_report(limit=5, base_dir=tmp_path)
    assert "Archive quality" in text
    assert "Prompt variants" in text
    assert "ab-test" in text
    assert "timestamp" in text
    assert "PROMPT_EXPERIMENT_TAG" in text


def test_archivequality_command_is_registered():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert "from core.archive_quality import format_archive_quality_report" in commands
    assert "async def archivequality_command" in commands
    assert "format_archive_quality_report" in commands
    assert 'CommandHandler("archivequality"' in main


def test_prompt_variant_quality_scores_and_author_profiles(tmp_path):
    from core.archive_quality import collect_author_quality_profiles, collect_prompt_variant_quality

    save_generated_page_record(
        build_generated_page_record(
            video_id="v-good",
            source_url="https://youtu.be/good",
            title="Good",
            author="Stable Author",
            format_name="sermon",
            synopsis_url="https://telegra.ph/good",
            prompt_variant="good-v1",
            timestamp_coverage_ratio=0.95,
        ),
        base_dir=tmp_path,
    )
    save_generated_page_record(
        build_generated_page_record(
            video_id="v-bad",
            source_url="https://youtu.be/bad",
            title="Bad",
            author="Risk Author",
            format_name="lecture",
            synopsis_url="https://telegra.ph/bad",
            prompt_variant="bad-v1",
            quality_warnings=["timestamp: low", "title-topic: low"],
            segments_status="partial",
            timestamp_coverage_ratio=0.50,
        ),
        base_dir=tmp_path,
    )

    variants = collect_prompt_variant_quality(limit=10, base_dir=tmp_path)
    by_variant = {v.variant: v for v in variants}
    assert by_variant["good-v1"].quality_score > by_variant["bad-v1"].quality_score
    assert by_variant["bad-v1"].timestamp_low == 1
    assert by_variant["bad-v1"].title_topic_warnings == 1

    authors = collect_author_quality_profiles(limit=10, base_dir=tmp_path)
    by_author = {a.author: a for a in authors}
    assert "timestamp coverage" in by_author["Risk Author"].recommendation
    assert "stable candidate" in by_author["Stable Author"].recommendation

    report = format_archive_quality_report(limit=10, base_dir=tmp_path)
    assert "Prompt variant quality score" in report
    assert "Cross-run author/profile hints" in report
    assert "good-v1" in report and "bad-v1" in report


def test_prompt_variant_comparison_and_payload(tmp_path):
    from core.archive_quality import archive_quality_payload, compare_prompt_variants, format_prompt_variant_comparison

    save_generated_page_record(
        build_generated_page_record(
            video_id="cmp-good",
            source_url="https://youtu.be/cg",
            title="Good",
            author="A",
            synopsis_url="https://telegra.ph/cg",
            prompt_variant="good",
            timestamp_coverage_ratio=0.95,
        ),
        base_dir=tmp_path,
    )
    save_generated_page_record(
        build_generated_page_record(
            video_id="cmp-bad",
            source_url="https://youtu.be/cb",
            title="Bad",
            author="B",
            synopsis_url="https://telegra.ph/cb",
            prompt_variant="bad",
            quality_warnings=["timestamp: low", "title-topic: low"],
            segments_status="partial",
            timestamp_coverage_ratio=0.40,
        ),
        base_dir=tmp_path,
    )

    cmp = compare_prompt_variants("good", "bad", limit=10, base_dir=tmp_path)
    assert cmp.left is not None and cmp.right is not None
    assert cmp.winner == "good"
    assert cmp.score_delta < 0
    text = format_prompt_variant_comparison("good", "bad", limit=10, base_dir=tmp_path)
    assert "Prompt variant comparison" in text
    assert "delta(right-left)" in text
    assert "winner=<code>good</code>" in text

    payload = archive_quality_payload(limit=10, base_dir=tmp_path)
    assert payload["summary"]["total"] == 2
    assert any(item["variant"] == "good" for item in payload["prompt_variants"])
    assert any(item["author"] == "B" for item in payload["author_profiles"])


def test_comparevariants_command_is_registered():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert "format_prompt_variant_comparison" in commands
    assert "async def comparevariants_command" in commands
    assert 'CommandHandler("comparevariants"' in main
    assert 'BotCommand("comparevariants"' in main


def test_archive_quality_exports_markdown_and_json(tmp_path):
    from core.archive_quality import archive_quality_payload, write_archive_quality_exports

    save_generated_page_record(
        build_generated_page_record(
            video_id="export1",
            source_url="https://youtu.be/export1",
            title="Export",
            author="A",
            synopsis_url="https://telegra.ph/export1",
            prompt_variant="export-v1",
            quality_warnings=["timestamp: low"],
        ),
        base_dir=tmp_path,
    )
    paths = write_archive_quality_exports(limit=10, base_dir=tmp_path)
    md = Path(paths["md"])
    js = Path(paths["json"])
    assert md.exists()
    assert js.exists()
    assert "Archive quality report" in md.read_text(encoding="utf-8")
    assert "export-v1" in md.read_text(encoding="utf-8")
    payload = archive_quality_payload(limit=10, base_dir=tmp_path)
    assert payload["summary"]["total"] == 1
    assert "prompt_variants" in js.read_text(encoding="utf-8")


def test_archivequalityfile_command_is_registered():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert "awrite_archive_quality_exports" in commands
    assert "async def archivequalityfile_command" in commands
    assert 'CommandHandler("archivequalityfile"' in main
    assert 'BotCommand("archivequalityfile"' in main


def test_quality_records_and_prompt_recommendation(tmp_path):
    from core.archive_quality import collect_quality_records, format_quality_records_report, recommend_prompt_variant

    save_generated_page_record(
        build_generated_page_record(
            video_id="qr-good1",
            source_url="https://youtu.be/qr-good1",
            title="Good 1",
            author="Stable",
            synopsis_url="https://telegra.ph/qr-good1",
            prompt_variant="stable-v1",
            timestamp_coverage_ratio=0.90,
        ),
        base_dir=tmp_path,
    )
    save_generated_page_record(
        build_generated_page_record(
            video_id="qr-good2",
            source_url="https://youtu.be/qr-good2",
            title="Good 2",
            author="Stable",
            synopsis_url="https://telegra.ph/qr-good2",
            prompt_variant="stable-v1",
            timestamp_coverage_ratio=0.92,
        ),
        base_dir=tmp_path,
    )
    save_generated_page_record(
        build_generated_page_record(
            video_id="qr-bad",
            source_url="https://youtu.be/qr-bad",
            title="Bad",
            author="Risk",
            synopsis_url="https://telegra.ph/qr-bad",
            prompt_variant="risky-v1",
            quality_warnings=["timestamp: low"],
            segments_status="partial",
            timestamp_coverage_ratio=0.40,
        ),
        base_dir=tmp_path,
    )

    records = collect_quality_records(limit=10, base_dir=tmp_path)
    assert records[0].video_id == "qr-bad"
    assert records[0].needs_attention is True
    report = format_quality_records_report(limit=10, base_dir=tmp_path)
    assert "records needing attention" in report
    assert "qr-bad" in report
    assert "timestamp" in report

    recommendation = recommend_prompt_variant(limit=10, base_dir=tmp_path, min_records=2)
    assert "Best current candidate" in recommendation
    assert "stable-v1" in recommendation


def test_qualityrecords_and_promptrecommend_commands_are_registered():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert "format_quality_records_report" in commands
    assert "recommend_prompt_variant" in commands
    assert "async def qualityrecords_command" in commands
    assert "async def promptrecommend_command" in commands
    assert 'CommandHandler("qualityrecords"' in main
    assert 'CommandHandler("promptrecommend"' in main
    assert 'BotCommand("qualityrecords"' in main
    assert 'BotCommand("promptrecommend"' in main
