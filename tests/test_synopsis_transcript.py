"""Regression tests for YouTube transcript-backed Synopsis."""
from pathlib import Path

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
