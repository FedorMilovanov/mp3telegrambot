"""Regression tests for R42 — ENG→RU dubbing hardening (deep re-audit).

Covers the verified fixes:
  * mix: aformat normalization before sidechain/amix; rnnoise path no longer
    double-escaped (Windows denoise); truncated-RU guard in mix_tracks.
  * QA: clean-RU track fed to run_translation_qa (not the bilingual mix);
    clean result without an `issues` key accepted; parse-failure file cleanup;
    HTML-safe report truncation (no mid-tag cut → no silent Telegram drop);
    technical_check flags a silent/near-silent dub.
  * fetch: Node helper rejects a truncated/empty download; get_translation_
    subtitles excludes the independent gemini_subs.srt.
  * pipeline: outer except flushes an already-ready dub instead of cancelling it.
"""

from pathlib import Path

import pytest

from services.livedub_mix import (
    build_mix_filter,
    calculate_tail_pad_ms,
    extract_fix_intervals,
)
from services.livedub_qa import format_qa_report, technical_check
import services.livedub_qa as lq


# ── mix: format normalization + rnnoise escaping ─────────────────────────────
def test_r42_mix_filter_normalizes_sample_rate_and_layout():
    fc = build_mix_filter(0.45, 1.3, 600, duck=True)
    # both EN and RU chains get a deterministic aformat before sidechain/amix
    assert fc.count("aformat=sample_rates=48000:channel_layouts=stereo") == 2
    # sidechain graph still intact
    assert "sidechaincompress" in fc and "[aout]" in fc


def test_r42_rnnoise_path_not_double_escaped(tmp_path):
    model = tmp_path / "cb.rnnn"
    model.write_text("x", encoding="utf-8")
    fc = build_mix_filter(0.45, 1.3, 600, duck=True, rnnoise_model=str(model))
    assert "arnndn=m='" in fc
    # the path is single-quoted, so a colon must NOT be backslash-escaped
    assert "\\:" not in fc


def test_r42_no_colon_escape_in_code():
    # guards the Windows-only bug a Linux path can't reproduce: the rnnoise path
    # is single-quoted, so the model assignment must NOT re-escape the colon.
    src = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert '_m = rnnoise_model.replace("\\\\", "/")\n' in src


def test_r42_calculate_tail_pad_ms():
    # no durations → base = delay + margin
    assert calculate_tail_pad_ms(600, 1000, None, None) == 1600
    # equal durations → still ~base
    assert calculate_tail_pad_ms(600, 1000, 100.0, 100.0) == 1600
    # RU 5s longer than original → needs a bigger tail so last phrase survives
    assert calculate_tail_pad_ms(600, 1000, 100.0, 105.0) == 6600


def test_r42_mix_rejects_truncated_ru_source():
    src = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert "RU-дубляж усечён" in src
    assert "ru_duration < 0.5 * orig_duration" in src


# ── extract_fix_intervals (pure) ─────────────────────────────────────────────
def test_r42_extract_fix_intervals_major_only_and_merge(monkeypatch):
    monkeypatch.setenv("LIVEDUB_DELAY_MS", "600")
    # minor is ignored; only majors become windows
    iv = extract_fix_intervals([
        {"time": "1:00", "severity": "minor"},
        {"time": "2:00", "severity": "major"},
    ])
    assert len(iv) == 1
    # two majors close together merge into one window (windows are ~6.6s wide)
    iv2 = extract_fix_intervals([
        {"time": "1:00", "severity": "major"},
        {"time": "1:03", "severity": "major"},
    ])
    assert len(iv2) == 1
    # malformed timecode is dropped safely
    assert extract_fix_intervals([{"time": "??", "severity": "major"}]) == []


# ── QA report: HTML-safe truncation + clean pass ─────────────────────────────
def _many_issues(n):
    return [
        {
            "time": f"{i:02d}:00",
            "severity": "major" if i % 2 else "minor",
            "heard": "искажённая русская фраза " * 6,
            "should_be": "правильный русский вариант " * 6,
            "problem": "подробное описание искажения смысла " * 6,
        }
        for i in range(n)
    ]


def test_r42_format_qa_report_html_safe_truncation():
    qa = {"score": 55, "verdict": "оценка " * 400, "issues": _many_issues(20)}
    out = format_qa_report(qa, video_url="https://youtu.be/abc")
    assert len(out) <= 4000
    # links are present and every tag is balanced (no mid-tag byte cut)
    assert "<a href=" in out
    assert out.count("<a ") == out.count("</a>")
    assert out.count("<b>") == out.count("</b>")
    # never ends inside a tag
    assert not out.rstrip().endswith("<a") and out.count("<") == out.count(">")


def test_r42_format_qa_report_clean_pass():
    # a clean result (no issues) still renders a publish-ok message
    out = format_qa_report({"score": 98, "verdict": "перевод точный", "issues": []})
    assert "можно публиковать" in out


# ── QA source-level guarantees (async/Gemini-bound paths) ────────────────────
def test_r42_qa_accepts_clean_result_without_issues_key():
    src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    assert 'result.setdefault("issues", [])' in src
    assert '"score" in result or "verdict" in result' in src


def test_r42_qa_cleans_uploaded_on_parse_failure():
    src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    # the parse-failure branch (after last_err assignment) now clears uploaded
    anchor = 'last_err = RuntimeError("ответ модели не распарсился в QA-JSON")'
    assert anchor in src
    after = src[src.index(anchor):src.index(anchor) + 700]
    assert "uploaded.clear()" in after


def test_r42_qa_uses_clean_ru_track():
    qa_src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    assert "dub_audio_path: Optional[Path] = None" in qa_src
    assert "ЧИСТОЙ RU-дорожке" in qa_src
    pipe = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    # both QA call sites pass the clean track from find_pro_tracks
    assert pipe.count("dub_audio_path=_clean_ru") == 2
    assert pipe.count("find_pro_tracks(ld_work)[1]") == 2


# ── technical_check: silent dub detection ────────────────────────────────────
def test_r42_technical_check_flags_silent_dub(monkeypatch):
    fake = {
        "format": {"duration": "600"},
        "streams": [{"codec_type": "audio"}, {"codec_type": "video"}],
    }
    monkeypatch.setattr(lq, "_ffprobe_json", lambda p: fake)
    monkeypatch.setattr(lq, "_mean_volume_db", lambda *a, **k: -72.0)
    warns = technical_check(Path("/nonexistent/dub.mp4"), 600)
    assert any("тишина" in w for w in warns)
    # a normal loudness does not warn
    monkeypatch.setattr(lq, "_mean_volume_db", lambda *a, **k: -20.0)
    warns2 = technical_check(Path("/nonexistent/dub.mp4"), 600)
    assert not any("тишина" in w for w in warns2)


# ── fetch: download integrity + subtitle isolation ───────────────────────────
def test_r42_node_helper_rejects_tiny_download():
    src = Path("vot_helper/vot_live.mjs").read_text(encoding="utf-8")
    assert "buf.length < 4096" in src
    assert "LIVEDUB_NOT_AVAILABLE" in src


def test_r42_get_translation_subtitles_excludes_gemini_subs():
    src = Path("services/yandex_live_dub.py").read_text(encoding="utf-8")
    # the new-files set for the Yandex dub SRT must exclude our own gemini subs
    assert 'f.name != "gemini_subs.srt"' in src


# ── pipeline: outer except flushes a ready dub ───────────────────────────────
def test_r42_outer_except_flushes_ready_dub():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    i_except = src.rindex("except Exception as e:")
    tail = src[i_except:i_except + 1300]
    assert "_send_livedub_result" in tail
    assert "не теряем УЖЕ ГОТОВЫЙ дубляж" in tail
