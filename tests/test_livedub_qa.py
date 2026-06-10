"""Tests for services/livedub_qa.py and the three-mode /mode command."""
from pathlib import Path

from services.livedub_qa import _parse_qa_json, format_qa_report
from handlers.mode_command import VALID_MODES, MODE_LABELS, MODE_DESCRIPTIONS


# ── _parse_qa_json ───────────────────────────────────────────────

def test_parse_plain_json():
    data = _parse_qa_json('{"score": 97, "verdict": "ok", "issues": []}')
    assert data["score"] == 97
    assert data["issues"] == []


def test_parse_json_with_code_fence():
    raw = '```json\n{"score": 80, "verdict": "meh", "issues": []}\n```'
    data = _parse_qa_json(raw)
    assert data is not None
    assert data["score"] == 80


def test_parse_json_with_surrounding_text():
    raw = 'Вот результат:\n{"score": 90, "verdict": "x", "issues": []}\nКонец.'
    data = _parse_qa_json(raw)
    assert data is not None and data["score"] == 90


def test_parse_garbage_returns_none():
    assert _parse_qa_json("") is None
    assert _parse_qa_json("no json here") is None
    assert _parse_qa_json("[1, 2, 3]") is None  # list, not dict


# ── format_qa_report ─────────────────────────────────────────────

def test_format_clean_report():
    text = format_qa_report({"score": 98, "verdict": "Перевод точный.", "issues": []})
    assert "98" in text
    assert "✅" in text
    assert "публиковать" in text


def test_format_report_with_issues_sorts_major_first():
    qa = {
        "score": 85,
        "verdict": "Есть искажения.",
        "issues": [
            {"time": "10:05", "heard": "оправдание делами", "problem": "инверсия смысла",
             "should_be": "оправдание верой", "severity": "major"},
            {"time": "02:30", "heard": "церковь", "problem": "неточность",
             "should_be": "община", "severity": "minor"},
        ],
    }
    text = format_qa_report(qa)
    assert "🔴" in text and "🟡" in text
    assert text.index("🔴") < text.index("🟡")
    assert "10:05" in text and "02:30" in text
    assert "оправдание верой" in text


def test_format_report_escapes_html():
    qa = {"score": 70, "verdict": "<b>bad</b>", "issues": [
        {"time": "1:00", "heard": "<script>", "problem": "x<y", "should_be": "a&b", "severity": "major"},
    ]}
    text = format_qa_report(qa)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_format_report_caps_length():
    issues = [
        {"time": f"{i}:00", "heard": "x" * 200, "problem": "y" * 300,
         "should_be": "z" * 200, "severity": "major"}
        for i in range(50)
    ]
    text = format_qa_report({"score": 10, "verdict": "bad", "issues": issues})
    assert len(text) <= 4000


# ── /mode: три режима ────────────────────────────────────────────

def test_three_modes_defined():
    assert VALID_MODES == ("rus", "eng", "eng_fast")
    for m in VALID_MODES:
        assert m in MODE_LABELS and m in MODE_DESCRIPTIONS


def test_pipeline_handles_eng_fast():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert 'in ("eng", "eng_fast")' in src
    assert 'user_mode == "eng_fast"' in src
    # QA только в Full: сабы и проверка завязаны на user_mode == "eng"
    assert '(user_mode == "eng") and await asettings_get("eng_subtitles")' in src
    assert '(user_mode == "eng") and await asettings_get("livedub_qa")' in src


def test_settings_key_registered():
    from core.database import SETTINGS_LABELS
    assert "livedub_qa" in SETTINGS_LABELS
