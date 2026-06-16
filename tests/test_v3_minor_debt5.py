"""Tests for v3 minor-debt patch 5:
- core/utils.py: cleanup_stale_downloads() added
- main.py: periodic_maintenance calls cleanup_stale_downloads
- core/prompt_rules.py: shared prompt constants created
- core/prompts.py: SYNOPSIS_PROMPT_V2 uses shared rules via _expand_prompt_rules
"""
import pathlib
import tempfile
import time
import os


# ─── cleanup_stale_downloads ──────────────────────────────────────────────

def test_cleanup_stale_downloads_function_exists():
    from core.utils import cleanup_stale_downloads
    assert callable(cleanup_stale_downloads), \
        "cleanup_stale_downloads must be callable"


def test_cleanup_stale_downloads_removes_old_video(tmp_path, monkeypatch):
    """Old .mp4 files older than threshold must be deleted."""
    from core import utils as u
    monkeypatch.setattr(u, "DOWNLOAD_DIR", tmp_path)

    # Create stale file (mtime 4 hours ago)
    old_file = tmp_path / "abc_video.mp4"
    old_file.write_bytes(b"fake")
    old_time = time.time() - 4 * 3600
    os.utime(old_file, (old_time, old_time))

    # Create fresh file (mtime 1 hour ago — below default 3h threshold)
    new_file = tmp_path / "xyz_video.mp4"
    new_file.write_bytes(b"fresh")
    new_time = time.time() - 3600
    os.utime(new_file, (new_time, new_time))

    deleted = u.cleanup_stale_downloads(max_age_hours=3)
    assert deleted == 1, f"Expected 1 deleted, got {deleted}"
    assert not old_file.exists(), "Old file must be deleted"
    assert new_file.exists(), "Fresh file must survive"


def test_cleanup_stale_downloads_keeps_mp3(tmp_path, monkeypatch):
    """MP3 files must never be deleted by stale cleanup."""
    from core import utils as u
    monkeypatch.setattr(u, "DOWNLOAD_DIR", tmp_path)

    mp3_file = tmp_path / "abc.mp3"
    mp3_file.write_bytes(b"audio")
    old_time = time.time() - 10 * 3600
    os.utime(mp3_file, (old_time, old_time))

    deleted = u.cleanup_stale_downloads(max_age_hours=1)
    assert deleted == 0
    assert mp3_file.exists(), "MP3 must not be deleted"


def test_cleanup_stale_downloads_keeps_jpg(tmp_path, monkeypatch):
    """Thumbnail JPGs must never be deleted by stale cleanup."""
    from core import utils as u
    monkeypatch.setattr(u, "DOWNLOAD_DIR", tmp_path)

    thumb = tmp_path / "abc_thumb.jpg"
    thumb.write_bytes(b"img")
    old_time = time.time() - 10 * 3600
    os.utime(thumb, (old_time, old_time))

    deleted = u.cleanup_stale_downloads(max_age_hours=1)
    assert deleted == 0
    assert thumb.exists(), "Thumbnail must not be deleted"


# ─── main.py periodic_maintenance ────────────────────────────────────────

def test_main_periodic_maintenance_calls_stale_cleanup():
    src = pathlib.Path("main.py").read_text(encoding="utf-8")
    assert "cleanup_stale_downloads" in src, \
        "main.py must import and call cleanup_stale_downloads"
    # 2026-06-10: импорт стал многострочным (добавлены botapi/livedub cleanups) —
    # проверяем наличие обоих имён, а не точную форму строки импорта
    assert "cleanup_nosub_files" in src, \
        "main.py must import cleanup_nosub_files alongside cleanup_stale_downloads"


# ─── prompt_rules.py ─────────────────────────────────────────────────────

def test_prompt_rules_module_exists():
    from core.prompt_rules import (
        INLINE_TIMESTAMP_RULES,
        STRICT_BANS,
        AUTHOR_NAMING_RULE,
        JSON_OUTPUT_RULE,
        SECTION_TITLE_RULE,
    )
    assert INLINE_TIMESTAMP_RULES, "INLINE_TIMESTAMP_RULES must not be empty"
    assert STRICT_BANS, "STRICT_BANS must not be empty"
    assert AUTHOR_NAMING_RULE, "AUTHOR_NAMING_RULE must not be empty"


def test_prompt_rules_inline_timestamp_content():
    from core.prompt_rules import INLINE_TIMESTAMP_RULES
    assert "ПРАВИЛО INLINE-ТАЙМКОДОВ" in INLINE_TIMESTAMP_RULES
    assert "M:SS" in INLINE_TIMESTAMP_RULES
    assert "НИКОГДА не начинай" in INLINE_TIMESTAMP_RULES


def test_prompt_rules_strict_bans_content():
    from core.prompt_rules import STRICT_BANS
    assert "СТРОГИЕ ЗАПРЕТЫ" in STRICT_BANS
    assert "канцелярские вводные" in STRICT_BANS
    assert "автор/проповедник/спикер" in STRICT_BANS
    assert "таким образом" in STRICT_BANS


# ─── prompts.py DRY ──────────────────────────────────────────────────────

def test_synopsis_v2_no_raw_placeholders():
    from core.prompts import SYNOPSIS_PROMPT_V2
    assert "{INLINE_TIMESTAMP_RULES}" not in SYNOPSIS_PROMPT_V2, \
        "SYNOPSIS_PROMPT_V2 must not contain unresolved {INLINE_TIMESTAMP_RULES}"
    assert "{STRICT_BANS}" not in SYNOPSIS_PROMPT_V2, \
        "SYNOPSIS_PROMPT_V2 must not contain unresolved {STRICT_BANS}"


def test_synopsis_v2_contains_shared_rules():
    from core.prompts import SYNOPSIS_PROMPT_V2
    from core.prompt_rules import INLINE_TIMESTAMP_RULES, STRICT_BANS
    # Content of shared rules must be embedded
    assert "ПРАВИЛО INLINE-ТАЙМКОДОВ" in SYNOPSIS_PROMPT_V2
    assert "СТРОГИЕ ЗАПРЕТЫ" in SYNOPSIS_PROMPT_V2


def test_synopsis_v2_format_still_works():
    from core.prompts import SYNOPSIS_PROMPT_V2
    result = SYNOPSIS_PROMPT_V2.format(
        title="Test Sermon",
        duration="54:00",
        hermeneutic_method="expository",
        format_note="sermon",
        syn_sections="8",
        syn_section_len="500",
        syn_total="4000",
    )
    assert "Test Sermon" in result
    assert "{title}" not in result
    assert len(result) > 5000, "Formatted prompt must be substantial"


def test_expand_prompt_rules_helper():
    from core.prompts import _expand_prompt_rules
    sample = "Начало {INLINE_TIMESTAMP_RULES} конец {STRICT_BANS} ещё."
    result = _expand_prompt_rules(sample)
    assert "{INLINE_TIMESTAMP_RULES}" not in result
    assert "{STRICT_BANS}" not in result
    assert "ПРАВИЛО INLINE-ТАЙМКОДОВ" in result
    assert "СТРОГИЕ ЗАПРЕТЫ" in result

