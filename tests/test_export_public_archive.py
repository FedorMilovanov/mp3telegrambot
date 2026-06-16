import json
import sqlite3
from pathlib import Path

from tools.export_public_archive import extract_records, render_markdown


def _make_cache_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE video_cache (
                video_id TEXT PRIMARY KEY,
                url TEXT DEFAULT '',
                ai_data TEXT DEFAULT '',
                telegraph_url TEXT DEFAULT '',
                study_tg_url TEXT DEFAULT '',
                reflection_tg_url TEXT DEFAULT '',
                questions_tg_url TEXT DEFAULT '',
                created_at INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO video_cache
                (video_id, url, ai_data, telegraph_url, study_tg_url,
                 reflection_tg_url, questions_tg_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "vid1",
                "https://www.youtube.com/watch?v=vid1",
                json.dumps({"real_title": "Тестовый конспект", "real_author": "Автор"}, ensure_ascii=False),
                "https://telegra.ph/main",
                "https://telegra.ph/study",
                "https://telegra.ph/refl",
                "https://telegra.ph/refl",  # legacy duplicate of reflection URL
                1_718_400_000,
            ),
        )
        conn.execute(
            """
            INSERT INTO video_cache (video_id, url, ai_data, telegraph_url, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "private-empty",
                "",
                json.dumps({"real_title": "Без публичных ссылок"}, ensure_ascii=False),
                "",
                1_718_400_001,
            ),
        )
        conn.commit()


def test_export_public_archive_allowlists_links_and_dedupes_urls(tmp_path):
    db = tmp_path / "bot_cache.db"
    _make_cache_db(db)

    records = extract_records(db)
    assert len(records) == 1
    rec = records[0]
    assert rec["video_id"] == "vid1"
    assert rec["title"] == "Тестовый конспект"
    assert rec["author"] == "Автор"
    assert list(rec["links"].values()).count("https://telegra.ph/refl") == 1
    assert "Вопросы" not in rec["links"]

    md = render_markdown(records, source_db=db)
    assert "Тестовый конспект" in md
    assert "https://telegra.ph/study" in md
    assert "ai_data" not in md
    assert "Без публичных ссылок" not in md
