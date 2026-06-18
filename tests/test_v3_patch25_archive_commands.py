"""Regression tests for v3 patch 25 — archive query commands."""

from pathlib import Path

from core.generated_pages import (
    build_generated_page_record,
    query_generated_pages,
    save_generated_page_record,
)


def _save(tmp_path, *, video_id, title, author, scripture_refs=None, hashtags=None, status="complete"):
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


def test_query_generated_pages_recent_search_author_scripture(tmp_path):
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


def test_archive_commands_are_registered_and_documented():
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


def test_archive_formatter_uses_html_links_without_web_preview():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert "disable_web_page_preview=True" in commands
    assert '<a href="{html_mod.escape(url)}">{label}</a>' in commands
    assert "safe_trim" not in commands.lower()  # formatter has its own conservative truncation


def test_html_pre_message_truncates_before_escaping_entities():
    from handlers.commands import _html_pre_message

    msg = _html_pre_message(("<&> " * 2000), limit=3900)

    assert len(msg) <= 3900
    assert msg.startswith("<pre>") and msg.endswith("</pre>")
    assert "…обрезано" in msg
    assert "<&>" not in msg
    assert msg.count("<pre>") == msg.count("</pre>") == 1
    assert msg.count("&lt;") == msg.count("&gt;")


def test_resetcache_echo_escapes_user_supplied_video_id():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert "safe_video_id = html_mod.escape" in commands
    assert "<code>{safe_video_id}</code>" in commands
    assert "<code>{video_id}</code>" not in commands


def test_archive_formatter_truncates_without_breaking_html_tags():
    from handlers.commands import _archive_format_records

    records = []
    for i in range(40):
        records.append({
            "title": "Очень длинное название <с тегами> & сущностями " * 20,
            "author": "Автор & проверка <HTML>",
            "publication_status": "complete",
            "youtube_url": f"https://youtu.be/video{i}?q=<bad>&x=1",
            "synopsis_url": f"https://telegra.ph/page{i}",
            "study_url": f"https://telegra.ph/study{i}",
            "reflection_url": f"https://telegra.ph/refl{i}",
            "publication_warning": "warning <must escape> & details " * 20,
        })

    text = _archive_format_records(records, title="Архив <опасный> & длинный")

    assert len(text) <= 3900
    assert "…обрезано" in text
    assert text.count("<b>") == text.count("</b>")
    assert text.count("<code>") == text.count("</code>")
    assert text.count("<a href=") == text.count("</a>")
    assert "<с тегами>" not in text
    assert "& сущностями" not in text
