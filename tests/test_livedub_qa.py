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


# ── SRT перевода как вход QA ─────────────────────────────────────

def test_srt_to_timed_text(tmp_path):
    from services.livedub_qa import srt_to_timed_text
    srt = tmp_path / "x.srt"
    srt.write_text(
        "1\n00:00:05,000 --> 00:00:08,000\nПривет, мир\n\n"
        "2\n00:14:32,500 --> 00:14:36,000\nОправдание делами\nи ещё строка\n\n",
        encoding="utf-8",
    )
    out = srt_to_timed_text(srt)
    assert "[00:05] Привет, мир" in out
    assert "[14:32] Оправдание делами и ещё строка" in out


def test_srt_to_timed_text_handles_garbage(tmp_path):
    from services.livedub_qa import srt_to_timed_text
    srt = tmp_path / "bad.srt"
    srt.write_text("not srt at all\n\nstill not", encoding="utf-8")
    assert srt_to_timed_text(srt) == ""
    assert srt_to_timed_text(tmp_path / "missing.srt") == ""


def test_srt_to_timed_text_caps_size(tmp_path):
    from services.livedub_qa import srt_to_timed_text
    blocks = []
    for i in range(2000):
        mm = i // 60
        ss = i % 60
        blocks.append(f"{i+1}\n00:{mm:02d}:{ss:02d},000 --> 00:{mm:02d}:{ss:02d},900\n{'слово ' * 20}\n")
    srt = tmp_path / "big.srt"
    srt.write_text("\n".join(blocks), encoding="utf-8")
    out = srt_to_timed_text(srt, max_chars=12000)
    assert len(out) < 13500  # cap + последний блок


def test_pipeline_wires_dub_srt():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "get_translation_subtitles" in src
    assert "dub_srt_path=_dub_srt" in src


# ── Заход 2: reasoning-first + JSON-mime ─────────────────────────

def test_qa_prompt_has_reasoning_first_and_no_guess_rule():
    from services.livedub_qa import _QA_PROMPT
    assert "reasoning" in _QA_PROMPT
    # порядок полей: reasoning раньше score (reasoning-first повышает точность)
    assert _QA_PROMPT.index('"reasoning"') < _QA_PROMPT.index('"score"')
    assert "НЕ включай" in _QA_PROMPT  # правило «не уверен — пропусти»


def test_parse_qa_json_with_reasoning_field():
    from services.livedub_qa import _parse_qa_json
    data = _parse_qa_json(
        '{"reasoning": "сравнил тексты", "score": 95, "verdict": "ok", "issues": []}'
    )
    assert data is not None and data["score"] == 95


def test_format_qa_report_ignores_reasoning():
    from services.livedub_qa import format_qa_report
    text = format_qa_report({"reasoning": "internal", "score": 97,
                             "verdict": "Точный.", "issues": []})
    assert "internal" not in text  # reasoning — служебное, юзеру не показываем
    assert "97" in text


def test_qa_uses_native_json_mime():
    src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    assert 'response_mime_type": "application/json"' in src or "response_mime_type" in src
    assert "audio_timestamp" in src


def test_download_original_video_reuses_existing():
    src = Path("services/eng_subtitles.py").read_text(encoding="utf-8")
    assert "Реюз" in src and 'glob("original_video.*")' in src


def test_qa_has_global_deadline():
    src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    assert "_qa_deadline" in src  # бюджет на все ключи, а не 420с × каждый


# ── Заход 3: file://-отправка, voice-EQ, кэш LUFS, delay-aware fix ──

def test_send_video_uses_path_not_handle():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "video=livedub_path" in src   # Path → file:// при local_mode
    assert "video=fixed" in src
    assert 'open(livedub_path, "rb")' not in src


def test_voice_eq_toggle():
    from services.livedub_mix import build_mix_filter
    fc = build_mix_filter(0.45, 1.3, 600, duck=True, voice_eq=True)
    assert "highpass=f=70" in fc
    fc2 = build_mix_filter(0.45, 1.3, 600, duck=True, voice_eq=False)
    assert "highpass" not in fc2


def test_loudness_cache_exists():
    from services import livedub_mix as lm
    assert hasattr(lm, "_loudness_cache")


def test_fix_intervals_account_for_delay(monkeypatch):
    monkeypatch.setenv("LIVEDUB_DELAY_MS", "600")
    from services.livedub_mix import extract_fix_intervals
    iv = extract_fix_intervals([{"time": "1:00", "severity": "major"}])
    assert len(iv) == 1
    a, b = iv[0]
    # окно расширено на delay: 6.0 + 0.6 = 6.6с
    assert (b - a) >= 6.5


# ── Заход 4: метаданные видео для Telegram ───────────────────────

def test_probe_and_thumbnail_helpers_exist():
    from services.livedub_mix import probe_video_meta, make_video_thumbnail
    meta = probe_video_meta(Path("/nonexistent/file.mp4"))
    assert set(meta.keys()) == {"width", "height", "duration"}
    assert make_video_thumbnail(Path("/nonexistent/file.mp4")) is None


def test_send_video_passes_metadata():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert 'width=_v_meta.get("width")' in src
    assert 'duration=_v_meta.get("duration")' in src
    assert "thumbnail=_v_thumb" in src
    # autofix-видео тоже с метаданными
    assert 'width=_fx_meta.get("width")' in src


def test_merge_subtitles_has_faststart():
    src = Path("services/eng_subtitles.py").read_text(encoding="utf-8")
    assert "+faststart" in src


# ── Заход 5: прод-фиксы QA (лог 2026-06-10) ──────────────────────

def test_qa_uses_make_audio_config_not_raw():
    src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    assert "make_audio_config" in src
    # audio_timestamp больше не передаётся (Gemini API его отвергает на вызове)
    assert "audio_timestamp=True" not in src
    # старый бюджет 4096 (съедался thinking-токенами) выпилен
    assert '"max_output_tokens": 4096' not in src


def test_qa_logs_finish_reason_on_parse_failure():
    src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    assert "finish=%s" in src and "thoughts_token_count" in src


def test_qa_skips_dub_audio_when_srt_available():
    src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    assert "_have_srt" in src
    assert "без извлечения дубляжа" in src


# ── Визуальный аудит конспектов 2026-06-10 (Playwright) ──────────

def _chain(nodes):
    from converters.md_telegraph import _final_telegraph_polish, _postprocess_telegraph_nodes
    from services.telegraph import _clean_telegraph_nodes
    return _postprocess_telegraph_nodes(_clean_telegraph_nodes(_final_telegraph_polish(nodes)))


def _flat(nodes):
    out = []
    for n in nodes:
        if isinstance(n, str):
            out.append(n)
        elif isinstance(n, dict):
            out.append(_flat(n.get("children", [])))
    return "".join(x if isinstance(x, str) else x for x in out)


def test_visual_toc_number_has_space():
    """Баг: '1.Введение' — жирная цифра слипалась с заголовком."""
    nodes = [{"tag": "p", "children": [
        {"tag": "b", "children": ["1.\u00a0"]}, "Введение",
        " — ⏱\u00a0", {"tag": "a", "attrs": {"href": "https://x"}, "children": ["0:00"]},
    ]}]
    ch = _chain(nodes)[0]["children"]
    joined = "".join(c if isinstance(c, str) else "".join(c.get("children", [])) for c in ch)
    assert "1. Введение" in joined or "1.\u00a0Введение" in joined, joined


def test_visual_quote_separator_preserved():
    """Баг: 'Иоан. 4:34:«цитата»' — пробел между b и i дропался polish-ем."""
    nodes = [{"tag": "p", "children": [
        "• ", {"tag": "b", "children": ["Иоан. 4:34:"]}, " ",
        {"tag": "i", "children": ["«Моя пища»"]},
    ]}]
    ch = _chain(nodes)[0]["children"]
    assert " " in ch, ch


def test_visual_rtl_paragraph_anchored_ltr():
    """Баг: строка с ивритом в начале рендерилась RTL на telegra.ph (dir=auto)."""
    nodes = [{"tag": "p", "children": [
        "• ", {"tag": "b", "children": ["ברית — Используется для разграничения"]},
        ", налагаемого свыше.",
    ]}]
    ch = _chain(nodes)[0]["children"]
    first = ch[0] if isinstance(ch[0], str) else ""
    assert first.startswith("\u200e"), ch


def test_visual_rtl_not_anchored_for_plain_russian():
    """LRM не должен добавляться обычным русским абзацам."""
    nodes = [{"tag": "p", "children": ["Обычный русский текст с διαθήκη внутри."]}]
    ch = _chain(nodes)[0]["children"]
    first = ch[0] if isinstance(ch[0], str) else ""
    assert not first.startswith("\u200e"), ch


# ── Заход 6: file_id-кэш LIVEDUB + шаблонные фразы ───────────────

def test_no_template_scripture_role_stub():
    """Болванка 'Подтверждает основной тезис раздела.' больше не вставляется."""
    src = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert 'Подтверждает основной тезис раздела."' not in src


def test_livedub_file_id_cache_wired():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "_livedub_cached_file_id" in src
    assert "adb_set_livedub_file_id" in src
    # протухший file_id: чистим кэш и сообщаем, НЕ оставляем юзера молча
    assert "Кэшированный перевод устарел" in src


def test_db_livedub_file_id_roundtrip(tmp_path, monkeypatch):
    import core.database as db
    import core.globals as g
    test_db = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    monkeypatch.setattr(g, "DB_PATH", test_db)
    db.db_init()
    db.db_set_livedub_file_id("vid123", "BAAC_test_file_id")
    row = db.db_get("vid123")
    assert row is not None
    assert row["livedub_file_id"] == "BAAC_test_file_id"
    # перезапись и очистка
    db.db_set_livedub_file_id("vid123", "")
    assert db.db_get("vid123")["livedub_file_id"] == ""
