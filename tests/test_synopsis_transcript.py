"""Regression tests for YouTube transcript-backed Synopsis."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import services.youtube_transcript as youtube_transcript
from services.youtube_transcript import timed_text_last_second, vtt_to_timed_text


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    if isinstance(node, list):
        return "".join(_flat(c) for c in node)
    return ""


def _find_links(node):
    found = []
    if isinstance(node, dict):
        if node.get("tag") == "a":
            found.append(node)
        for child in node.get("children", []):
            found.extend(_find_links(child))
    elif isinstance(node, list):
        for child in node:
            found.extend(_find_links(child))
    return found


def test_vtt_to_timed_text_parses_and_dedupes_cues():
    raw = """WEBVTT

00:00:01.000 --> 00:00:03.000 align:start position:0%
<v Roger>We need family worship.</v>

00:00:03.000 --> 00:00:05.000
<c>We need family worship.</c>

00:00:06.000 --> 00:00:08.000
Start with Scripture and prayer.
"""
    out = vtt_to_timed_text(raw, chunk_seconds=25)
    assert "[0:01] We need family worship. Start with Scripture and prayer." in out
    assert out.count("We need family worship") == 1


def test_timed_text_last_second_for_coverage_gate():
    assert timed_text_last_second("[0:07] a\n[1:02:03] b") == 3723


def test_vtt_keeps_real_rhetorical_repetition_inside_one_chunk():
    raw = """WEBVTT

00:00:01.000 --> 00:00:03.000
Grace alone.

00:00:04.000 --> 00:00:06.000
Means salvation.

00:00:07.000 --> 00:00:09.000
Grace alone.
"""
    out = vtt_to_timed_text(raw, chunk_seconds=25)
    assert out.lower().count("grace alone") == 2


def test_vtt_keeps_real_repetition_across_chunk_boundary():
    raw = """WEBVTT

00:00:01.000 --> 00:00:03.000
Grace alone.

00:00:20.000 --> 00:00:22.000
Means salvation.

00:00:27.000 --> 00:00:29.000
Grace alone.
"""
    out = vtt_to_timed_text(raw, chunk_seconds=25)
    assert out.lower().count("grace alone") == 2


def test_transcript_prefers_source_language_over_larger_english_vtt(tmp_path, monkeypatch):
    async def fake_run(_cmd, **_kwargs):
        (tmp_path / "yt_transcript_vid.ru.vtt").write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nРусский источник.\n",
            encoding="utf-8",
        )
        (tmp_path / "yt_transcript_vid.en.vtt").write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n"
            + ("English translation is deliberately much larger. " * 20)
            + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(youtube_transcript, "run_cancellable_process", fake_run)
    out = asyncio.run(
        youtube_transcript.download_youtube_transcript_text(
            "https://youtu.be/example",
            tmp_path,
            lang="ru",
        )
    )
    assert "Русский источник" in out
    assert "English translation" not in out


def test_partial_manual_transcript_falls_back_to_full_auto(tmp_path, monkeypatch):
    calls = []

    async def fake_run(cmd, **_kwargs):
        auto = "--write-auto-subs" in cmd
        calls.append("auto" if auto else "manual")
        if auto:
            body = """WEBVTT

00:00:01.000 --> 00:00:03.000
Automatic full start.

00:59:50.000 --> 00:59:55.000
Automatic full end.
"""
        else:
            body = """WEBVTT

00:00:01.000 --> 00:00:03.000
Manual partial start.

00:10:00.000 --> 00:10:05.000
Manual partial end.
"""
        (tmp_path / "yt_transcript_vid.en.vtt").write_text(body, encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(youtube_transcript, "run_cancellable_process", fake_run)
    out = asyncio.run(
        youtube_transcript.download_youtube_transcript_text(
            "https://youtu.be/example",
            tmp_path,
            lang="en",
            expected_duration=3600,
        )
    )
    assert calls == ["manual", "auto"]
    assert "Automatic full start" in out
    assert "Manual partial" not in out


def test_coverage_uses_raw_vtt_timeline_not_max_chars_clip(tmp_path, monkeypatch):
    async def fake_run(_cmd, **_kwargs):
        (tmp_path / "yt_transcript_vid.en.vtt").write_text(
            """WEBVTT

00:00:01.000 --> 00:00:03.000
Opening sentence that is intentionally long enough to hit the prompt clip.

00:59:50.000 --> 00:59:55.000
Final sentence.
""",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(youtube_transcript, "run_cancellable_process", fake_run)
    out = asyncio.run(
        youtube_transcript.download_youtube_transcript_text(
            "https://youtu.be/example",
            tmp_path,
            lang="en",
            max_chars=32,
            expected_duration=3600,
        )
    )
    assert out


def test_verbatim_synopsis_bare_timestamps_become_youtube_links():
    from converters.md_telegraph import _section_to_nodes_v2
    nodes = _section_to_nodes_v2(
        {"title": "Стенограмма", "time": "0:00", "content": "0:07 Мы откроем книгу.\n\n2:41 Мир сегодня..."},
        yt_url="https://www.youtube.com/watch?v=abc123",
        duration=3600,
    )
    links = _find_links(nodes)
    hrefs = [l.get("attrs", {}).get("href", "") for l in links]
    assert any("t=7" in h for h in hrefs)
    assert any("t=161" in h for h in hrefs)


def test_dot_bold_list_items_are_repaired_before_telegraph_parse():
    from services.telegraph import _md_to_telegraph_nodes
    nodes = _md_to_telegraph_nodes(".**От сухого интеллектуализма к заветной нежности.** Текст")
    flat = _flat(nodes)
    assert flat.startswith("• От сухого интеллектуализма")
    assert "**" not in flat


def test_escaped_markdown_markers_are_not_rendered_raw():
    from converters.md_telegraph import _section_to_nodes_v2
    from services.telegraph import _md_to_telegraph_nodes
    nodes = _md_to_telegraph_nodes(r"• \*\*Семейное поклонение\*\* — практика")
    assert "**" not in _flat(nodes)
    assert any(isinstance(c, dict) and c.get("tag") == "b" for n in nodes for c in n.get("children", []))
    sec_nodes = _section_to_nodes_v2({"title": "T", "content": r"• \\*\\*Directory for Family Worship\\*\\* — источник"})
    assert "**" not in _flat(sec_nodes)
    assert "\\" not in _flat(sec_nodes)


def test_misbolded_bullet_lead_keeps_only_term_bold():
    from converters.md_telegraph import _section_to_nodes_v2
    from services.telegraph import _md_to_telegraph_nodes
    raw = "• **שָׁנַן — Показывает важность интенсивного**, глубокого запечатления."
    nodes = _md_to_telegraph_nodes(raw)
    flat = _flat(nodes).replace("\u200e", "").replace("  ", " ")
    assert flat == "• שָׁנַן — Показывает важность интенсивного, глубокого запечатления."
    p = nodes[0]
    b = next(c for c in p["children"] if isinstance(c, dict) and c.get("tag") == "b")
    assert _flat(b).replace("\u200e", "") == "שָׁנַן"
    src = "• **Directory for Family Worship. — Исторический документ**, содержащий указания."
    sec_nodes = _section_to_nodes_v2({"title": "T", "content": src})
    assert "Directory for Family Worship — Исторический документ" in _flat(sec_nodes)


def test_synopsis_wires_youtube_transcript_into_prompt():
    src = Path("services/telegraph.py").read_text(encoding="utf-8")
    ytt = Path("services/youtube_transcript.py").read_text(encoding="utf-8")
    prompts = Path("core/prompts.py").read_text(encoding="utf-8")
    assert "SYNOPSIS_VERBATIM_PROMPT" in prompts
    assert "Не summary. Не статья. Не анализ. Не пересказ." in prompts
    # eb893df: timestamps are inline ⏱ **M:SS** anchors before the period,
    # not paragraph-start prefixes.
    assert "Внутри content ставь inline-якоря ⏱ **M:SS**" in prompts
    assert "всегда ВНУТРИ предложения ПЕРЕД точкой" in prompts
    assert "Не используй source cards" in prompts
    assert "_synopsis_verbatim_prompt_enabled" in src
    assert "SYNOPSIS_VERBATIM_PROMPT" in src
    assert "prompt=%s" in src
    assert "ОПОРНЫЕ ТАЙМКОДЫ ДЛЯ ПОКРЫТИЯ (не summary)" in src
    assert "пиши по transcript/audio" in src
    assert "download_youtube_transcript_text" in src
    assert "source_lang: str = \"\"" in src
    assert "lang=source_lang or \"en\"" in src
    assert "expected_duration=_duration" in src
    assert "synopsis_transcript_" in src
    assert "--write-subs" in ytt and "--write-auto-subs" in ytt
    assert "SYNOPSIS_YT_TRANSCRIPT_MIN_COVERAGE" in ytt
    assert "ОРИГИНАЛЬНАЯ АНГЛИЙСКАЯ СТЕНОГРАММА" in src
    assert "главный текстовый скелет речи" in src
    assert "SYNOPSIS_YT_TRANSCRIPT_MAX_CHARS" in src
    assert "transcript-backed mode" in src
    assert "use_schema=not _transcript_attached" in src
    assert "density retry uses transcript-only text path" in src
    assert "use_schema=False" in src  # density retry should not be schema-compressed


def test_pipeline_passes_youtube_language_to_synopsis_transcript():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "source_lang=source_lang" in src


def test_transcript_language_globs_are_deduped_in_source():
    src = Path("services/youtube_transcript.py").read_text(encoding="utf-8")
    assert "prefs = [f\"{lang_root}.*\", lang_root, \"en.*\", \"en\"]" in src
    assert "seen: set[str]" in src


def test_transcript_env_documented():
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "SYNOPSIS_YT_TRANSCRIPT=1" in env
    assert "SYNOPSIS_YT_TRANSCRIPT_MAX_CHARS" in env
    assert "SYNOPSIS_YT_TRANSCRIPT_MIN_COVERAGE" in env
    assert "SYNOPSIS_VERBATIM_PROMPT=1" in env
    assert "SYNOPSIS_STRUCTURED=0" in env
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "timed transcript" in readme
    assert "структурированной почти-дословной стенограммой" in readme
    assert "SYNOPSIS_STRUCTURED=0" in readme
