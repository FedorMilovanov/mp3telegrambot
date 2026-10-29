"""Regression tests for v3 patch 25 — archive query commands."""

from pathlib import Path

from core.generated_pages import (
    build_generated_page_record,
    query_generated_pages,
    save_generated_page_record,
)


def _save(
    tmp_path: Path,
    *,
    video_id: str,
    title: str,
    author: str,
    scripture_refs: list[str] | None = None,
    hashtags: list[str] | None = None,
    status: str = "complete",
) -> None:
    record = build_generated_page_record(
        video_id=video_id,
        source_url=f"https://youtu.be/{video_id}",
        title=title,
        author=author,
        youtube_url=f"https://youtu.be/{video_id}",
        synopsis_url=f"https://telegra.ph/{video_id}-syn",
        study_url=f"https://telegra.ph/{video_id}-study",
        reflection_url=f"https://telegra.ph/{video_id}-refl",
        scripture_refs=scripture_refs or [],
        hashtags=hashtags or [],
        publication_status=status,
    )
    save_generated_page_record(record, base_dir=tmp_path)


def test_query_generated_pages_recent_search_author_scripture(tmp_path: Path) -> None:
    _save(
        tmp_path,
        video_id="a1",
        title="Миссия Церкви",
        author="Том Пеннингтон",
        scripture_refs=["Мф. 28:16–20"],
        hashtags=["МиссияЦеркви"],
    )
    _save(
        tmp_path,
        video_id="b2",
        title="Приводящий в изумление Раб Иеговы",
        author="Джон МакАртур",
        scripture_refs=["Исаия 53"],
        hashtags=["Исаия53"],
        status="partial",
    )

    recent = query_generated_pages(limit=10, base_dir=tmp_path)
    assert len(recent) == 2

    by_query = query_generated_pages(query="миссия", base_dir=tmp_path)
    assert [r["video_id"] for r in by_query] == ["a1"]

    by_author = query_generated_pages(author="МакАртур", base_dir=tmp_path)
    assert [r["video_id"] for r in by_author] == ["b2"]

    by_scripture = query_generated_pages(scripture="Исаия", base_dir=tmp_path)
    assert [r["video_id"] for r in by_scripture] == ["b2"]

    by_status = query_generated_pages(status="partial", base_dir=tmp_path)
    assert [r["video_id"] for r in by_status] == ["b2"]


def test_archive_commands_are_registered_and_documented() -> None:
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")

    assert "async def archive_command" in commands
    assert "async def lastpages_command" in commands
    assert "async def search_archive_command" in commands
    assert "async def author_archive_command" in commands
    assert "async def scripture_archive_command" in commands
    assert "aquery_generated_pages" in commands
    assert "Папка архива" in commands

    assert 'CommandHandler("archive"' in main
    assert 'CommandHandler("lastpages"' in main
    assert 'CommandHandler("search"' in main
    assert 'CommandHandler("author"' in main
    assert 'CommandHandler("scripture"' in main


def test_archive_formatter_uses_html_links_without_web_preview() -> None:
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert "disable_web_page_preview=True" in commands
    assert '<a href="{html_mod.escape(url)}">{label}</a>' in commands
    assert "safe_trim_caption" in commands  # shared UTF-16/HTML-safe final boundary


def test_admin_status_and_disk_reports_use_safe_html_limit() -> None:
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert '_html_message_limit("\\n".join(lines))' in commands
    assert "html_mod.escape(str(LOCAL_BOT_API_URL))" in commands
    assert "html_mod.escape(str(_bak_info))" in commands
    assert "html_mod.escape(str(e))" in commands


def test_html_message_limit_preserves_tags_and_entities() -> None:
    from converters.md_telegraph import visible_length
    from handlers.commands import _html_message_limit

    msg = _html_message_limit("<b>" + ("A &amp; B " * 1000) + "</b><code>tail</code>", limit=3900)

    assert visible_length(msg) <= 3900
    assert "…обрезано" in msg
    assert msg.count("<b>") == msg.count("</b>")
    assert msg.count("<code>") == msg.count("</code>")
    assert "&am…" not in msg


def test_html_message_limit_counts_astral_emoji_as_utf16_units() -> None:
    from converters.md_telegraph import visible_length
    from handlers.commands import _html_message_limit

    msg = _html_message_limit("<b>" + "😀" * 5 + "</b>", limit=9, suffix="…")

    assert visible_length(msg) == 9
    assert msg == "<b>😀😀😀😀</b>…"
    assert msg.count("<b>") == msg.count("</b>") == 1


def test_html_message_limit_preserves_entities_and_closing_tag_budget() -> None:
    from converters.md_telegraph import visible_length
    from handlers.commands import _html_message_limit

    msg = _html_message_limit("<b>" + ("A &amp; 😀 " * 500) + "</b><code>tail</code>", limit=200)

    assert visible_length(msg) <= 200
    assert "…обрезано" in msg
    assert "&am…" not in msg
    assert msg.count("<b>") == msg.count("</b>") == 1
    assert msg.count("<code>") == msg.count("</code>")


def test_html_pre_message_counts_astral_emoji_as_utf16_units() -> None:
    from converters.md_telegraph import visible_length
    from handlers.commands import _html_pre_message

    msg = _html_pre_message("🙏" * 100, limit=32)

    assert visible_length(msg) <= 32
    assert msg.startswith("<pre>") and msg.endswith("</pre>")
    assert "…обрезано" in msg


def test_html_pre_message_truncates_before_escaping_entities() -> None:
    from converters.md_telegraph import visible_length
    from handlers.commands import _html_pre_message

    msg = _html_pre_message(("<&> " * 2000), limit=3900)

    assert visible_length(msg) <= 3900
    assert msg.startswith("<pre>") and msg.endswith("</pre>")
    assert "…обрезано" in msg
    assert "<&>" not in msg
    assert msg.count("<pre>") == msg.count("</pre>") == 1
    assert "&amp;" in msg
    assert "&am\n…" not in msg


def test_resetcache_echo_escapes_user_supplied_video_id() -> None:
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert "safe_video_id = html_mod.escape" in commands
    assert "<code>{safe_video_id}</code>" in commands
    assert "<code>{video_id}</code>" not in commands


def test_archive_formatter_truncates_without_breaking_html_tags() -> None:
    from converters.md_telegraph import visible_length
    from handlers.commands import _archive_format_records

    records = []
    for i in range(40):
        records.append(
            {
                "title": "Очень длинное название <с тегами> & сущностями " * 20,
                "author": "Автор & проверка <HTML>",
                "publication_status": "complete",
                "youtube_url": f"https://youtu.be/video{i}?q=<bad>&x=1",
                "synopsis_url": f"https://telegra.ph/page{i}",
                "study_url": f"https://telegra.ph/study{i}",
                "reflection_url": f"https://telegra.ph/refl{i}",
                "publication_warning": "warning <must escape> & details " * 20,
            }
        )

    text = _archive_format_records(records, title="Архив <опасный> & длинный")

    assert visible_length(text) <= 3900
    assert "…обрезано" in text
    assert text.count("<b>") == text.count("</b>")
    assert text.count("<code>") == text.count("</code>")
    assert text.count("<a href=") == text.count("</a>")
    assert "<с тегами>" not in text
    assert "& сущностями" not in text


def test_archive_formatter_enforces_utf16_limit_for_emoji_records() -> None:
    from converters.md_telegraph import visible_length
    from handlers.commands import _archive_format_records

    records = [
        {
            "title": "😀" * 180,
            "author": "🙏" * 120,
            "publication_status": "complete",
            "youtube_url": f"https://youtu.be/video{i}",
            "synopsis_url": f"https://telegra.ph/page{i}",
            "publication_warning": "⚠️ " + "😀" * 200,
        }
        for i in range(20)
    ]

    text = _archive_format_records(records, title="Поиск 😀 " + "🙏" * 200)

    assert visible_length(text) <= 3900
    assert "…обрезано" in text
    assert text.count("<b>") == text.count("</b>")
    assert text.count("<code>") == text.count("</code>")
    assert text.count("<a href=") == text.count("</a>")
