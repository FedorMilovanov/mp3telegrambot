"""AUDIT R4 (2026-07-05): media/download services regressions.

Covers: vot npx argv split, pdf Popen monkeypatch removal, livedub info
source_url, livedub QA temp-file leak, parse_mmss 3-digit minutes,
shorts workdir reuse, eng_subtitles rc/.part, montage NameError, VK
groups.getById format/token.
"""
import ast
import sys
from pathlib import Path

import pytest


def test_run_subprocess_splits_npx_argv0_with_args():
    """npx-fallback возвращал argv[0]="npx vot-cli-live" одной строкой, а
    сплит срабатывал только при len==1 — все реальные вызовы (с аргументами)
    падали FileNotFoundError."""
    from services.yandex_live_dub import _run_subprocess

    if sys.platform == "win32":
        pytest.skip("echo-путь для POSIX")
    echo = "/bin/echo" if Path("/bin/echo").exists() else "/usr/bin/echo"
    out, err, rc = _run_subprocess([f"{echo} hello", "world"], timeout=10)
    assert rc == 0
    assert "hello world" in out


def test_pdf_generator_no_global_popen_monkeypatch():
    """Глобальный патч subprocess.Popen убивал по таймауту ЧУЖОЙ процесс
    (например, параллельный ffmpeg-рендер шорта)."""
    src = Path("services/pdf_generator.py").read_text(encoding="utf-8")
    assert "_TrackedPopen" not in src
    assert "_subprocess.Popen = " not in src
    assert "communicate(timeout=_WKHTMLTOPDF_TIMEOUT)" in src


def test_livedub_info_card_keeps_source_url_on_success():
    src = Path("services/livedub_info.py").read_text(encoding="utf-8")
    assert "_normalize_card(data, title_line, source_url)" in src


def test_livedub_qa_temp_audio_cleanup_not_dead():
    """Без nonlocal внешняя _temp_original_audio оставалась None и
    {stem}_qa_original.mp3 утекал после каждого Quick-QA."""
    src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    assert "nonlocal client_used, _temp_original_audio" in src


def test_parse_mmss_accepts_three_digit_minutes():
    """srt_to_timed_text даёт [105:22] для видео >=100 минут — QA-отметки
    финала длинных проповедей теряли ссылки и autofix их пропускал."""
    from services.livedub_mix import parse_mmss

    assert parse_mmss("105:22") == 105 * 60 + 22
    assert parse_mmss("1:45:22") == 3600 + 45 * 60 + 22
    assert parse_mmss("7:30") == 450
    assert parse_mmss("абв") is None
    assert parse_mmss("") is None


def test_shorts_workdir_reuse_validates_video_stream():
    src = Path("services/shorts_video.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    implementation = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_unowned_download_video_for_shorts"
    )
    public_wrapper = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "download_video_for_shorts"
    )
    reuse = ast.get_source_segment(src, implementation) or ""
    wrapper = ast.get_source_segment(src, public_wrapper) or ""

    assert "_has_video_stream" in reuse
    assert '".part"' in reuse and '".ytdl"' in reuse
    assert "await_owned_coroutine" in wrapper
    assert "_unowned_download_video_for_shorts" in wrapper


def test_eng_subtitles_checks_ytdlp_exit_code():
    src = Path("services/eng_subtitles.py").read_text(encoding="utf-8")
    assert "proc.returncode != 0" in src
    assert '".part"' in src and '".ytdl"' in src


def test_montage_temp_vars_initialized_before_try():
    """except-хендлер итерирует temp_parts; OSError из mkdir превращался
    в NameError."""
    src = Path("services/render_clips_montage.py").read_text(encoding="utf-8")
    fn = src.split("Склеивает несколько фрагментов", 1)[1]
    init_pos = fn.find("temp_parts: list[Path] = []")
    try_pos = fn.find("try:")
    assert init_pos != -1 and try_pos != -1 and init_pos < try_pos


def test_vk_group_resolve_handles_v5199_and_token():
    src = Path("services/search.py").read_text(encoding="utf-8")
    block = src.split('mapping.get("vk_domain")', 1)[1][:2000]
    assert '_gid_params["access_token"]' in block
    assert '"groups"' in block, "v5.199 возвращает {'response': {'groups': [...]}}"
