"""Tests for v3 minor-debt patch 3:
- clips/montage: InputFile(read_bytes()) thumbnails without writable .name hacks
- montage: highlights cand guard against missing fragments/title
- commands: _do_resetcache_one uses HTML parse_mode (not Markdown)
"""

import pathlib

from telegram import InputFile

from pipelines.clips import _existing_input_file
from pipelines.montage import _existing_input_file as _montage_existing_input_file


def test_clips_thumbnail_uses_inputfile(tmp_path: pathlib.Path) -> None:
    # AUDIT R25: open()+`.name=` падал на py3.13 (BufferedReader.name read-only).
    # InputFile(read_bytes()) keeps bytes in memory and does not retain a handle.
    path = tmp_path / "snapshot.png"
    assert _existing_input_file(path) is None
    path.write_bytes(b"thumbnail")
    thumbnail = _existing_input_file(path)
    assert isinstance(thumbnail, InputFile)
    assert thumbnail.input_file_content == b"thumbnail"
    assert thumbnail.filename == path.name


def test_montage_poster_thumbnail_uses_inputfile(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "poster.jpg"
    assert _montage_existing_input_file(path) is None
    path.write_bytes(b"poster")

    thumbnail = _montage_existing_input_file(path)

    assert isinstance(thumbnail, InputFile)
    assert thumbnail.input_file_content == b"poster"
    assert thumbnail.filename == path.name


def test_montage_snapshot_thumbnail_uses_inputfile(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "snapshot.jpg"
    path.write_bytes(b"snapshot")

    thumbnail = _montage_existing_input_file(path)

    assert isinstance(thumbnail, InputFile)
    assert thumbnail.input_file_content == b"snapshot"
    assert thumbnail.filename == path.name


def test_highlights_cand_guard_present() -> None:
    src = pathlib.Path("pipelines/montage.py").read_text(encoding="utf-8")
    assert 'not cand.get("fragments")' in src, "Highlights must guard against missing fragments field"
    assert 'not cand.get("title")' in src, "Highlights must guard against missing title field"
    assert "данные кандидата повреждены" in src, "Highlights guard must send user message on invalid cand"


def test_resetcache_uses_html_not_markdown() -> None:
    src = pathlib.Path("handlers/commands.py").read_text(encoding="utf-8")
    # The resetcache reply must use HTML parse_mode with escaped <code> tags.
    assert "safe_video_id = html_mod.escape" in src, (
        "_do_resetcache_one must escape user-supplied video_id before HTML output"
    )
    assert "<code>{safe_video_id}</code>" in src, "_do_resetcache_one must use escaped id inside <code> tag (HTML)"
    assert "<code>{video_id}</code>" not in src, "_do_resetcache_one must not echo raw video_id inside HTML"
    # Markdown backtick pattern in resetcache must be gone
    # (backtick is used elsewhere for other purposes, so we check the combo)
    assert (
        'parse_mode="Markdown")' not in src or "_do_resetcache_one" not in src.split('parse_mode="Markdown")')[0][-500:]
    ), "_do_resetcache_one still uses Markdown parse_mode"


def test_resetcache_html_parse_mode_present() -> None:
    src = pathlib.Path("handlers/commands.py").read_text(encoding="utf-8")
    # Verify HTML parse_mode appears inside the resetcache helper.
    idx = src.find("_do_resetcache_one")
    next_fn = src.find("\n\nasync def ", idx + 1)
    block = src[idx:next_fn] if idx != -1 and next_fn != -1 else ""
    assert 'parse_mode="HTML"' in block, '_do_resetcache_one block must contain parse_mode="HTML"'
