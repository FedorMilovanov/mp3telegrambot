"""Tests for v3 minor-debt patch 3:
- clips/montage: open() instead of BytesIO(read_bytes()) for thumbnails
- montage: highlights cand guard against missing fragments/title
- commands: _do_resetcache_one uses HTML parse_mode (not Markdown)
"""
import pathlib


def test_clips_thumbnail_uses_open_not_bytesio():
    src = pathlib.Path("pipelines/clips.py").read_text(encoding="utf-8")
    assert "thumb_buf = open(snap_path" in src, \
        "clips.py must use open() for thumb, not BytesIO(read_bytes())"
    # Old pattern must be gone
    assert "BytesIO(snap_path.read_bytes())" not in src, \
        "clips.py still has BytesIO(snap_path.read_bytes())"


def test_montage_poster_thumbnail_uses_open():
    src = pathlib.Path("pipelines/montage.py").read_text(encoding="utf-8")
    assert 'open(poster_path, "rb")' in src, \
        "montage.py poster must use open(), not BytesIO(read_bytes())"
    assert "BytesIO(poster_path.read_bytes())" not in src, \
        "montage.py still has BytesIO(poster_path.read_bytes())"


def test_montage_snapshot_thumbnail_uses_open():
    src = pathlib.Path("pipelines/montage.py").read_text(encoding="utf-8")
    assert 'open(snapshot_path, "rb")' in src, \
        "montage.py snapshot must use open(), not BytesIO(read_bytes())"
    assert "BytesIO(snapshot_path.read_bytes())" not in src, \
        "montage.py still has BytesIO(snapshot_path.read_bytes())"


def test_highlights_cand_guard_present():
    src = pathlib.Path("pipelines/montage.py").read_text(encoding="utf-8")
    assert 'not cand.get("fragments")' in src, \
        "Highlights must guard against missing fragments field"
    assert 'not cand.get("title")' in src, \
        "Highlights must guard against missing title field"
    assert "данные кандидата повреждены" in src, \
        "Highlights guard must send user message on invalid cand"


def test_resetcache_uses_html_not_markdown():
    src = pathlib.Path("handlers/commands.py").read_text(encoding="utf-8")
    # The resetcache reply must use HTML parse_mode with <code> tags
    assert "<code>{video_id}</code>" in src, \
        "_do_resetcache_one must use <code> tag (HTML)"
    # Markdown backtick pattern in resetcache must be gone
    # (backtick is used elsewhere for other purposes, so we check the combo)
    assert "parse_mode=\"Markdown\")" not in src or \
           "_do_resetcache_one" not in src.split("parse_mode=\"Markdown\")")[0][-500:], \
        "_do_resetcache_one still uses Markdown parse_mode"


def test_resetcache_html_parse_mode_present():
    src = pathlib.Path("handlers/commands.py").read_text(encoding="utf-8")
    # Verify HTML parse_mode appears near the resetcache reply
    idx = src.find("_do_resetcache_one")
    block = src[idx:idx+600] if idx != -1 else ""
    assert 'parse_mode="HTML"' in block, \
        "_do_resetcache_one block must contain parse_mode=\"HTML\""
