"""Regression tests for v3 patch 42 — repo safety/prompt health after cumulative changes."""

from pathlib import Path

from core.prompts import build_audio_analysis_prompt
from core.reasoning_guidance import build_reasoning_first_block


def test_segments_command_uses_safe_pre_without_nested_code_tags():
    src = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert "Нажмите кнопку или: /cut {video_id} N" in src
    assert "&lt;code&gt;" not in src[src.find("async def segments_command"):src.find("async def cutseg_command")]
    assert "caption=caption[:1024]" not in src
    assert "safe_trim" not in src.lower()


def test_reasoning_guidance_is_centralized_and_present_in_prompts():
    audio = build_audio_analysis_prompt("T", "A", "55:00", 3300, mode="deep")
    assert audio.find("РАЗРЕШЕНО И ЖЕЛАТЕЛЬНО") < audio.find("БАЗОВЫЕ ПРИНЦИПЫ")
    assert "Проверенный контекст разрешён" in build_reasoning_first_block("study")
    assert "пастырский нерв" in build_reasoning_first_block("reflection")


def test_structured_output_flags_are_documented_in_code():
    assert "AUDIO_ANALYSIS_STRUCTURED" in Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    assert "EXPANDED_PAGES_STRUCTURED" in Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "SYNOPSIS_STRUCTURED" in Path("services/telegraph.py").read_text(encoding="utf-8")


def test_no_known_bad_html_slice_or_nested_segment_code_regression():
    # Caption logic moved to services/segment_render.py
    render_src = Path("services/segment_render.py").read_text(encoding="utf-8")
    assert "caption[:1024]" not in render_src
    assert "len(caption) > 1024" in render_src
    assert "segment.title[:220]" in render_src
    # cutseg_command uses render_and_send_segment
    src = Path("handlers/commands.py").read_text(encoding="utf-8")
    cutseg_block = src[src.find("async def cutseg_command"):]
    assert "render_and_send_segment" in cutseg_block


def test_no_runtime_databases_or_local_git_identity_are_tracked():
    import subprocess

    tracked = subprocess.check_output(["git", "ls-files"], text=True, encoding="utf-8").splitlines()
    forbidden_suffixes = (".db", ".sqlite", ".sqlite3")
    forbidden_names = {".gitconfig", ".git-credentials", "bot.log"}
    offenders = []
    for path in tracked:
        name = Path(path).name
        if name in forbidden_names:
            offenders.append(path)
        if name.startswith("bot_cache.db.bak") or path.endswith(forbidden_suffixes):
            offenders.append(path)
    assert offenders == []
