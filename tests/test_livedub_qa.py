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


# ── Pro-микс и авто-правка ───────────────────────────────────────

def test_parse_mmss():
    from services.livedub_mix import parse_mmss
    assert parse_mmss("14:32") == 872.0
    assert parse_mmss("1:02:03") == 3723.0
    assert parse_mmss("0:05") == 5.0
    assert parse_mmss("garbage") is None
    assert parse_mmss("") is None


def test_interval_volume_expr():
    from services.livedub_mix import build_interval_volume_expr
    e = build_interval_volume_expr([(10.0, 16.0)], inside=0.15)
    assert "between(t,10.00,16.00)" in e and "0.15" in e
    assert build_interval_volume_expr([], inside=0.15) == "1.0"


def test_extract_fix_intervals_majors_only_and_merge():
    from services.livedub_mix import extract_fix_intervals
    issues = [
        {"time": "1:00", "severity": "major"},
        {"time": "1:03", "severity": "major"},   # пересекается с первым -> merge
        {"time": "5:00", "severity": "minor"},   # игнор
        {"time": "bad",  "severity": "major"},   # мусорный таймкод -> игнор
    ]
    iv = extract_fix_intervals(issues)
    assert len(iv) == 1
    a, b = iv[0]
    assert a <= 60.0 - 0.4 and b >= 63.0


def test_build_mix_filter_contains_delay_duck_and_volumes():
    from services.livedub_mix import build_mix_filter
    fc = build_mix_filter(0.45, 1.3, 600, duck=True)
    assert "adelay=600" in fc
    assert "sidechaincompress" in fc
    assert "volume=0.45" in fc and "volume=1.3" in fc
    fc2 = build_mix_filter(0.45, 1.3, 600, duck=False)
    assert "sidechaincompress" not in fc2 and "amix" in fc2


def test_mix_params_env_defaults_and_clamping(monkeypatch):
    from services import livedub_mix as lm
    monkeypatch.delenv("LIVEDUB_ORIG_VOLUME", raising=False)
    monkeypatch.delenv("LIVEDUB_DELAY_MS", raising=False)
    p = lm.get_mix_params()
    assert p["orig_volume"] == 0.45 and p["delay_ms"] == 600
    monkeypatch.setenv("LIVEDUB_ORIG_VOLUME", "99")   # вне диапазона -> дефолт
    monkeypatch.setenv("LIVEDUB_DELAY_MS", "-5")
    p = lm.get_mix_params()
    assert p["orig_volume"] == 0.45 and p["delay_ms"] == 600


def test_pipeline_wires_pro_mix_and_autofix():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert 'asettings_get("livedub_pro_mix")' in src
    assert 'asettings_get("livedub_autofix")' in src
    assert "build_pro_dub" in src
    assert "apply_qa_audio_fixes" in src


def test_new_settings_registered():
    from core.database import SETTINGS_LABELS
    assert "livedub_pro_mix" in SETTINGS_LABELS
    assert "livedub_autofix" in SETTINGS_LABELS


# ── Loudness-выравнивание (EBU R128) ─────────────────────────────

def test_loudness_gain_db():
    from services.livedub_mix import loudness_gain_db
    assert loudness_gain_db(-16.0) == 0.0
    assert loudness_gain_db(-26.0) == 10.0     # тихую дорожку поднимаем
    assert loudness_gain_db(-6.0) == -10.0     # громкую опускаем
    assert loudness_gain_db(None) == 0.0       # не измерилось — не трогаем
    assert loudness_gain_db(-80.0) == 20.0     # кламп ±20 дБ


def test_build_mix_filter_with_gains_and_limiter():
    from services.livedub_mix import build_mix_filter
    fc = build_mix_filter(0.45, 1.3, 600, duck=True, en_gain_db=4.2, ru_gain_db=-3.0)
    assert "volume=4.2dB" in fc and "volume=-3.0dB" in fc
    assert "alimiter" in fc
    assert "level_sc=1" in fc
    # нулевые поправки не засоряют граф
    fc2 = build_mix_filter(0.45, 1.3, 600, duck=False)
    assert "dB" not in fc2
    assert "alimiter" in fc2
