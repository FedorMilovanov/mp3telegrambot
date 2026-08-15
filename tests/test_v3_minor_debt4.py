"""Tests for v3 minor-debt patch 4:
- commands.py: all parse_mode="Markdown" converted to HTML + backtick → <code>
- pipelines/shorts.py: thumbnail delivery uses InputFile(read_bytes())
- core/database.py: busy_timeout=5000 added to key SQLite functions
"""
import pathlib


# ─── commands.py ──────────────────────────────────────────────────────────

def test_commands_no_markdown_parse_mode():
    src = pathlib.Path("handlers/commands.py").read_text(encoding="utf-8")
    assert 'parse_mode="Markdown"' not in src, \
        "handlers/commands.py still has parse_mode=\"Markdown\""


def test_commands_user_id_uses_code_tag():
    src = pathlib.Path("handlers/commands.py").read_text(encoding="utf-8")
    # Every user_id display in admin access-denied messages must use <code>
    import re
    backtick_user = re.findall(r'`\{user_id\}`', src)
    assert not backtick_user, \
        f"commands.py still has Markdown backtick user_id: {backtick_user}"


def test_commands_access_denied_uses_html():
    src = pathlib.Path("handlers/commands.py").read_text(encoding="utf-8")
    assert '<code>{user_id}</code>' in src, \
        "commands.py must use <code>{user_id}</code> for access-denied messages"


# ─── shorts.py thumbnails ─────────────────────────────────────────────────

def test_shorts_poster_thumbnail_uses_inputfile():
    # AUDIT R25: open()+`.name=` падал на py3.13 -> InputFile(read_bytes()).
    src = pathlib.Path("pipelines/shorts.py").read_text(encoding="utf-8")
    assert "poster_path.read_bytes()" in src
    assert "filename=poster_path.name" in src
    assert "thumb_buf.name =" not in src


def test_shorts_snapshot_thumbnail_uses_inputfile():
    src = pathlib.Path("pipelines/shorts.py").read_text(encoding="utf-8")
    assert "snapshot_path.read_bytes()" in src
    assert "filename=snapshot_path.name" in src


# ─── database.py busy_timeout ─────────────────────────────────────────────

def test_database_key_functions_have_busy_timeout():
    src = pathlib.Path("core/database.py").read_text(encoding="utf-8")
    # Count PRAGMA busy_timeout occurrences — should be significantly more than 1
    count = src.count("PRAGMA busy_timeout=5000")
    assert count >= 8, \
        f"Expected ≥8 busy_timeout PRAGMAs in database.py, got {count}"


def test_database_db_save_has_busy_timeout():
    src = pathlib.Path("core/database.py").read_text(encoding="utf-8")
    # Find db_save function and check busy_timeout within it
    idx = src.find("def db_save(")
    block = src[idx:idx+800] if idx != -1 else ""
    assert "busy_timeout" in block, \
        "db_save must include PRAGMA busy_timeout=5000"


def test_database_db_get_has_busy_timeout():
    src = pathlib.Path("core/database.py").read_text(encoding="utf-8")
    idx = src.find("def db_get(")
    block = src[idx:idx+300] if idx != -1 else ""
    assert "busy_timeout" in block, \
        "db_get must include PRAGMA busy_timeout=5000"


def test_database_settings_get_has_busy_timeout():
    src = pathlib.Path("core/database.py").read_text(encoding="utf-8")
    idx = src.find("def settings_get(")
    block = src[idx:idx+300] if idx != -1 else ""
    assert "busy_timeout" in block, \
        "settings_get must include PRAGMA busy_timeout=5000"
