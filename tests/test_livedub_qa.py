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
    assert VALID_MODES == ("rus", "eng", "eng_fast", "eng_fast_qa")
    for m in VALID_MODES:
        assert m in MODE_LABELS and m in MODE_DESCRIPTIONS


def test_pipeline_handles_eng_fast():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert 'in ("eng", "eng_fast", "eng_fast_qa")' in src
    assert 'user_mode in ("eng_fast", "eng_fast_qa")' in src
    assert 'user_mode == "eng_fast_qa"' in src
    # Full QA remains full-mode; Quick QA is a separate lightweight path.
    assert '(user_mode == "eng") and await asettings_get("eng_subtitles")' in src
    assert '(user_mode == "eng") and await asettings_get("livedub_qa")' in src


def test_settings_key_registered():
    from core.database import SETTINGS_LABELS
    assert "livedub_qa" in SETTINGS_LABELS


def test_eng_fast_qa_mode_registered_and_wired():
    mode = Path("handlers/mode_command.py").read_text(encoding="utf-8")
    assert "eng_fast_qa" in mode
    assert "⚡🔍 ENG Quick QA" in mode
    pipe = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "LIVEDUB_QUICK_QA_MAX_DURATION" in pipe
    assert "LIVEDUB_QUICK_QA_MODEL" in pipe
    assert "LIVEDUB_QUICK_QA_THINKING" in pipe
    assert "LiveDubQuickQA" in pipe
    assert "apply_qa_audio_fixes" in pipe
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "LIVEDUB_QUICK_QA_MAX_DURATION=120" in env
    assert "LIVEDUB_QUICK_QA_MODEL=gemini-3.1-flash-lite" in env
    assert "LIVEDUB_QUICK_QA_THINKING=minimal" in env


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
    assert "duration=longest" in fc
    assert "volume=0.45" in fc and "volume=1.3" in fc
    fc2 = build_mix_filter(0.45, 1.3, 600, duck=False)
    assert "sidechaincompress" not in fc2 and "amix" in fc2


def test_build_mix_filter_tail_guard_extends_last_phrase():
    from services.livedub_mix import build_mix_filter, _extend_filter_with_video_tail
    fc = build_mix_filter(0.45, 1.3, 600, duck=True, tail_pad_ms=1600)
    assert "apad=pad_dur=1.600" in fc
    assert "duration=longest" in fc
    full_fc = _extend_filter_with_video_tail(fc, 1600)
    assert "tpad=stop_mode=clone:stop_duration=1.600[vout]" in full_fc


def test_tail_guard_accounts_for_longer_yandex_audio():
    from services.livedub_mix import calculate_tail_pad_ms
    assert calculate_tail_pad_ms(600, 1000, orig_duration=40.0, ru_duration=40.0) == 1600
    # RU-аудио длиннее оригинала на 2.2с: хвост = 2.2 + 0.6 delay + 1.0 margin.
    assert calculate_tail_pad_ms(600, 1000, orig_duration=40.0, ru_duration=42.2) == 3800


def test_livedub_safe_video_filename_and_prepare_path(tmp_path):
    from services.livedub_mix import safe_livedub_video_filename, prepare_livedub_send_path
    name = safe_livedub_video_filename('Один грех отправляет в ад: почему? - Пол Вошер / HeartCry')
    assert name == "Один грех отправляет в ад почему - Пол Вошер HeartCry.mp4"
    src = tmp_path / "pro_dub.mp4"
    src.write_bytes(b"video")
    out = prepare_livedub_send_path(src, "Один грех отправляет в ад - Пол Вошер")
    assert out.name == "Один грех отправляет в ад - Пол Вошер.mp4"
    assert out.exists() and not src.exists()


def test_pipeline_prepares_human_livedub_filename_before_send():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    helper = src[src.index("async def _send_livedub_result"):src.index("performer, title = parse_title")]
    assert "prepare_livedub_send_path" in helper
    assert "десятки одинаковых pro_dub.mp4" in helper
    assert helper.index("prepare_livedub_send_path") < helper.index("probe_video_meta")


def test_mix_params_env_defaults_and_clamping(monkeypatch):
    from services import livedub_mix as lm
    monkeypatch.delenv("LIVEDUB_ORIG_VOLUME", raising=False)
    monkeypatch.delenv("LIVEDUB_DELAY_MS", raising=False)
    monkeypatch.delenv("LIVEDUB_TAIL_MARGIN_MS", raising=False)
    p = lm.get_mix_params()
    assert p["orig_volume"] == 0.45 and p["delay_ms"] == 600
    assert p["tail_margin_ms"] == 1000 and p["tail_pad_ms"] == 1600
    monkeypatch.setenv("LIVEDUB_ORIG_VOLUME", "99")   # вне диапазона -> дефолт
    monkeypatch.setenv("LIVEDUB_DELAY_MS", "-5")
    monkeypatch.setenv("LIVEDUB_TAIL_MARGIN_MS", "250")
    p = lm.get_mix_params()
    assert p["orig_volume"] == 0.45 and p["delay_ms"] == 600
    assert p["tail_pad_ms"] == 850


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


def test_technical_check_allows_livedub_tail_guard(monkeypatch):
    from services import livedub_qa as qa
    monkeypatch.setenv("LIVEDUB_DELAY_MS", "600")
    monkeypatch.setenv("LIVEDUB_TAIL_MARGIN_MS", "1000")
    monkeypatch.setattr(qa, "_ffprobe_json", lambda _p: {
        "format": {"duration": "41.5"},
        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
    })
    assert qa.technical_check(Path("dub.mp4"), expected_duration=40) == []


def test_technical_check_still_warns_when_video_is_shorter(monkeypatch):
    from services import livedub_qa as qa
    monkeypatch.setattr(qa, "_ffprobe_json", lambda _p: {
        "format": {"duration": "35"},
        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
    })
    warnings = qa.technical_check(Path("dub.mp4"), expected_duration=40)
    assert warnings and "короче оригинала" in warnings[0]


def test_technical_check_does_not_noise_on_moderate_longer_tail(monkeypatch):
    from services import livedub_qa as qa
    monkeypatch.setattr(qa, "_ffprobe_json", lambda _p: {
        "format": {"duration": "44"},
        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
    })
    assert qa.technical_check(Path("dub.mp4"), expected_duration=40) == []


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
    assert "ВСЕ текстовые поля JSON НА РУССКОМ ЯЗЫКЕ" in _QA_PROMPT


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
    assert "без видеопотока" in src
    assert "_has_video_stream" in src


def test_has_video_stream_does_not_treat_audio_m4a_as_video(tmp_path, monkeypatch):
    from services import livedub_mix as lm
    audio = tmp_path / "original_video.f140.m4a"
    audio.write_bytes(b"0" * (200 * 1024))
    video = tmp_path / "original_video.mp4"
    video.write_bytes(b"0" * (200 * 1024))
    monkeypatch.setattr(lm.shutil, "which", lambda _name: None)
    assert lm.has_video_stream(audio) is False
    assert lm.has_video_stream(video) is True


def test_livedub_mix_guards_audio_only_original():
    src = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert "входной оригинал не содержит видеопотока" in src
    assert "return None" in src[src.index("if not has_video_stream(orig_video)"):]


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
    assert "current_livedub_file_id_cache_version" in src
    assert "кэш file_id устарел" in src
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
    assert row["livedub_file_id_version"] == db.current_livedub_file_id_cache_version()
    # перезапись и очистка
    db.db_set_livedub_file_id("vid123", "")
    row2 = db.db_get("vid123")
    assert row2["livedub_file_id"] == ""
    assert row2["livedub_file_id_version"] == ""


def test_livedub_file_id_version_changes_with_mix_knobs(monkeypatch):
    import core.database as db
    monkeypatch.delenv("LIVEDUB_TAIL_MARGIN_MS", raising=False)
    v1 = db.current_livedub_file_id_cache_version()
    monkeypatch.setenv("LIVEDUB_TAIL_MARGIN_MS", "1500")
    v2 = db.current_livedub_file_id_cache_version()
    assert v1 != v2
    assert "tail=1500" in v2


# ── Заход 7: хирургическое закрытие недозакрытого ────────────────

def test_eng_subtitles_skip_by_known_duration():
    """Длинное видео отбрасывается ДО скачивания аудио (метаданные уже есть)."""
    src = Path("services/eng_subtitles.py").read_text(encoding="utf-8")
    assert "known_duration: int = 0" in src
    assert "субтитры пропущены без скачивания" in src
    pipe = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "known_duration=duration" in pipe


def test_synopsis_source_rule_in_prompt():
    src = Path("core/prompts.py").read_text(encoding="utf-8")
    assert "во вводной секции source-блок не ставь" in src


def test_help_mentions_mode_and_eng():
    src = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert "/mode" in src and "ENG Quick" in src


# ── Заход 8: cover (Bot API 8.3) ─────────────────────────────────

def test_livedub_cover_wired_with_fallback():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "_v_cover" in src
    assert "cover=_v_cover" in src
    # graceful fallback: повтор без cover при старом Bot API сервере
    assert "отправка без cover" in src


def test_ytdlp_no_double_cookie_sources(tmp_path, monkeypatch):
    """cookies.txt + yt-dlp.conf с --cookies-from-browser не смешиваются."""
    import services.ffmpeg as ff
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Создаём реальный Firefox cookie-store: только тогда конф с browser-cookies
    # безопасно подключать без cookies.txt.
    prof = tmp_path / ".mozilla" / "firefox" / "abc.default-release"
    prof.mkdir(parents=True)
    (prof / "cookies.sqlite").write_bytes(b"sqlite")
    (tmp_path / "yt-dlp.conf").write_text("--cookies-from-browser firefox", encoding="utf-8")
    ck = tmp_path / "cookies.txt"
    ck.write_text("# Netscape HTTP Cookie File", encoding="utf-8")
    monkeypatch.setattr(ff, "COOKIES_FILE", ck)
    args = ff._build_ytdlp_base_args()
    joined = " ".join(args)
    assert "--cookies " + str(ck) in joined or str(ck) in joined
    assert "--config-location" not in joined  # конф с куками пропущен
    # без cookies.txt конф снова подключается, потому что профиль существует
    monkeypatch.setattr(ff, "COOKIES_FILE", tmp_path / "missing.txt")
    args2 = " ".join(ff._build_ytdlp_base_args())
    assert "--config-location" in args2


def test_ytdlp_cookie_comments_do_not_disable_config(tmp_path, monkeypatch):
    import services.ffmpeg as ff
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ff, "COOKIES_FILE", tmp_path / "missing.txt")
    (tmp_path / "yt-dlp.conf").write_text(
        "# --cookies-from-browser firefox\n--no-playlist\n", encoding="utf-8"
    )
    joined = " ".join(ff._build_ytdlp_base_args())
    assert "--config-location" in joined


def test_ytdlp_skips_dead_browser_cookie_config(tmp_path, monkeypatch):
    """Репозиторный yt-dlp.conf не должен валить headless-хост без Firefox-профиля."""
    import services.ffmpeg as ff
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES_FROM_BROWSER", raising=False)
    (tmp_path / "yt-dlp.conf").write_text("--cookies-from-browser firefox", encoding="utf-8")
    monkeypatch.setattr(ff, "COOKIES_FILE", tmp_path / "missing.txt")
    joined = " ".join(ff._build_ytdlp_base_args())
    assert "--config-location" not in joined
    assert "--cookies-from-browser firefox" not in joined


def test_ytdlp_env_browser_cookie_source_can_override_dead_config(tmp_path, monkeypatch):
    import services.ffmpeg as ff
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    dead_home = tmp_path / "dead_home"
    monkeypatch.setenv("APPDATA", str(dead_home))
    good_prof = tmp_path / "profiles" / "ff"
    good_prof.mkdir(parents=True)
    (good_prof / "cookies.sqlite").write_bytes(b"sqlite")
    (tmp_path / "yt-dlp.conf").write_text("--cookies-from-browser firefox", encoding="utf-8")
    monkeypatch.setattr(ff, "COOKIES_FILE", tmp_path / "missing.txt")
    monkeypatch.setenv("YTDLP_COOKIES_FROM_BROWSER", f"firefox:{good_prof}")
    joined = " ".join(ff._build_ytdlp_base_args())
    assert "--config-location" not in joined
    assert "--cookies-from-browser" in joined and str(good_prof) in joined


def test_ytdlp_js_runtime_version_filter(monkeypatch, tmp_path):
    """yt-dlp 2026.06.09 требует Deno>=2.3 / Node>=22 для JS runtime."""
    import subprocess
    import services.ffmpeg as ff

    def fake_run(cmd, **kwargs):
        exe = str(cmd[0])
        if "node20" in exe:
            return subprocess.CompletedProcess(cmd, 0, stdout="v20.20.2", stderr="")
        if "node22" in exe:
            return subprocess.CompletedProcess(cmd, 0, stdout="v22.1.0", stderr="")
        if "deno_old" in exe:
            return subprocess.CompletedProcess(cmd, 0, stdout="deno 2.2.9", stderr="")
        if "deno_new" in exe:
            return subprocess.CompletedProcess(cmd, 0, stdout="deno 2.3.0", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(ff.subprocess, "run", fake_run)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ff, "COOKIES_FILE", tmp_path / "missing.txt")

    monkeypatch.setattr(ff.shutil, "which", lambda name: {"node": "/bin/node20"}.get(name))
    assert ff._supported_js_runtimes() == []
    assert "--js-runtimes" not in " ".join(ff._build_ytdlp_base_args())

    monkeypatch.setattr(ff.shutil, "which", lambda name: {"node": "/bin/node22"}.get(name))
    assert ff._supported_js_runtimes() == ["node"]
    assert "--js-runtimes node" in " ".join(ff._build_ytdlp_base_args())

    monkeypatch.setattr(ff.shutil, "which", lambda name: {"deno": "/bin/deno_new", "node": "/bin/node22"}.get(name))
    assert ff._supported_js_runtimes() == ["deno", "node"]

    monkeypatch.setattr(ff.shutil, "which", lambda name: {"deno": "/bin/deno_old"}.get(name))
    assert ff._supported_js_runtimes() == []


def test_startup_diagnostics_mention_supported_ytdlp_js_runtime():
    src = Path("main.py").read_text(encoding="utf-8")
    assert "yt-dlp JS runtime (Deno>=2.3/Node>=22)" in src
    req = Path("requirements.txt").read_text(encoding="utf-8")
    assert "nodejs>=22 или deno>=2.3" in req


# ── LiveDub captions: translated title for every DUB ────────────

def test_livedub_caption_title_fallback_known_authors():
    from pipelines.main_pipeline import _fallback_livedub_ru_title, _format_livedub_title_line
    title, author = _fallback_livedub_ru_title(
        "Why Are You a Dispensationalist? - Abner Chou & Costi Hinn",
        "", "", "BibleQ&A",
    )
    assert title == "Why Are You a Dispensationalist?"
    assert author == "Абнер Чау & Кости Хинн"
    assert _format_livedub_title_line(title, author).endswith(" - Абнер Чау & Кости Хинн")
    title2, author2 = _fallback_livedub_ru_title("Angels: God's Invisible Army - Part 1", "", "", "Grace to You")
    assert author2 == "Джон МакАртур"


def test_livedub_caption_title_light_translation(monkeypatch):
    import asyncio
    import pipelines.main_pipeline as mp

    async def fake_card(title_line, dub_srt_path=None, *, source_url="", force=False):
        assert force is True
        return {"youtube_title": "Почему вы диспенсационалист? - Абнер Чау & Кости Хинн"}

    monkeypatch.setattr(mp, "GEMINI_CLIENTS", [object()])
    monkeypatch.setenv("LIVEDUB_TITLE_TRANSLATE", "1")
    import services.livedub_info as li
    monkeypatch.setattr(li, "build_livedub_info_card", fake_card)
    mp._LIVEDUB_TITLE_CACHE.clear()
    title, author = asyncio.run(mp._translate_livedub_title_for_caption(
        "Why Are You a Dispensationalist? - Abner Chou & Costi Hinn",
        "Why Are You a Dispensationalist?",
        "Abner Chou & Costi Hinn",
        "BibleQ&A",
    ))
    assert title == "Почему вы диспенсационалист? - Абнер Чау & Кости Хинн"
    assert author == ""
    assert mp._format_livedub_title_line(title, author) == "Почему вы диспенсационалист? - Абнер Чау & Кости Хинн"


def test_livedub_caption_uses_existing_analysis_without_extra_gemini(monkeypatch):
    import asyncio
    import pipelines.main_pipeline as mp
    monkeypatch.setattr(mp, "GEMINI_CLIENTS", [])
    mp._LIVEDUB_TITLE_CACHE.clear()
    title, author = asyncio.run(mp._translate_livedub_title_for_caption(
        "Why Are You a Dispensationalist? - Abner Chou & Costi Hinn",
        "Why Are You a Dispensationalist?",
        "Abner Chou & Costi Hinn",
        "BibleQ&A",
        analysis_title="Почему вы диспенсационалист?",
        analysis_author="Абнер Чау & Кости Хинн",
    ))
    assert title == "Почему вы диспенсационалист?"
    assert author == "Абнер Чау & Кости Хинн"


def test_livedub_title_translate_can_be_disabled_for_metadata_only(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import pipelines.main_pipeline as mp

    class _BadModels:
        async def generate_content(self, **kwargs):
            raise AssertionError("Gemini must not be called when LIVEDUB_TITLE_TRANSLATE=0")

    monkeypatch.setattr(mp, "GEMINI_CLIENTS", [SimpleNamespace(aio=SimpleNamespace(models=_BadModels()))])
    monkeypatch.setenv("LIVEDUB_TITLE_TRANSLATE", "0")
    mp._LIVEDUB_TITLE_CACHE.clear()
    title, author = asyncio.run(mp._translate_livedub_title_for_caption(
        "All Your Deeds Will Be Exposed at the White Throne - Paul Washer",
        "", "", "HeartCry Missionary Society",
    ))
    assert author == "Пол Вошер"
    assert title.startswith("All Your Deeds")


def test_livedub_title_default_uses_light_info_pipeline(monkeypatch):
    import asyncio
    import pipelines.main_pipeline as mp

    async def fake_card(title_line, dub_srt_path=None, *, source_url="", force=False):
        assert force is True
        return {"youtube_title": "Все ваши дела будут вскрыты у Белого престола - Пол Вошер"}

    monkeypatch.setattr(mp, "GEMINI_CLIENTS", [object()])
    monkeypatch.delenv("LIVEDUB_TITLE_TRANSLATE", raising=False)
    import services.livedub_info as li
    monkeypatch.setattr(li, "build_livedub_info_card", fake_card)
    mp._LIVEDUB_TITLE_CACHE.clear()
    title, author = asyncio.run(mp._translate_livedub_title_for_caption(
        "All Your Deeds Will Be Exposed at the White Throne - Paul Washer",
        "", "", "HeartCry Missionary Society",
    ))
    assert title == "Все ваши дела будут вскрыты у Белого престола - Пол Вошер"
    assert author == ""


def test_livedub_title_author_first_is_reordered():
    import pipelines.main_pipeline as mp
    title, author = mp._split_livedub_title_author(
        "R.C. Sproul: True Seeker-Sensitive Churches", "", "", "BibleQ&A"
    )
    assert title == "True Seeker-Sensitive Churches"
    assert author == "Р. Ч. Спроул"
    assert mp._format_livedub_title_line("Р. Ч. Спроул - True Seeker-Sensitive Churches", "") == (
        "True Seeker-Sensitive Churches - Р. Ч. Спроул"
    )


def test_livedub_send_video_caption_includes_title_and_html_parse_mode():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "_translate_livedub_title_for_caption" in src
    assert "_livedub_title_html" in src
    assert "<b>{_livedub_title_html}</b>" in src
    # и свежая отправка, и file_id-кэш используют HTML caption
    helper = src[src.index("async def _send_livedub_result"):src.index("performer, title = parse_title")]
    assert helper.count('parse_mode="HTML"') >= 2
    assert "🎬 Живые голоса Яндекса" in helper
    # title translation is only done if there is an actual DUB to send
    assert "if not (_livedub_cached_file_id or live_dub_task):" in helper


# ── LiveDub lightweight info cards (Gemini light model) ──────────

def test_livedub_info_card_module_contract(tmp_path):
    from services.livedub_info import format_livedub_info_message, get_light_model, _normalize_card, livedub_info_response_schema
    assert get_light_model()
    assert "telegram_description" in livedub_info_response_schema()["properties"]
    card = _normalize_card({
        "telegram_description": "Короткое описание.",
        "youtube_title": "Название - Автор",
        "youtube_description": "Описание YouTube.",
        "compact_subtitles": ["Тезис 1", "Тезис 2"],
        "hashtags": ["евангелие", "Paul Washer"],
    }, "Fallback", source_url="https://youtu.be/original")
    msg = format_livedub_info_message(card)
    assert "Готовое описание к переводу" in msg
    assert "Кратко для Telegram" in msg
    assert "Для YouTube" in msg
    assert "<pre>" in msg and "#Евангелие" in msg
    assert "Оригинал на YouTube" in msg and "https://youtu.be/original" in msg


def test_livedub_info_youtube_description_contains_original_link():
    from services.livedub_info import format_livedub_info_message, _normalize_card
    card = _normalize_card({
        "telegram_description": "Описание",
        "youtube_title": "Название",
        "youtube_description": "Описание для YouTube",
        "compact_subtitles": [],
        "hashtags": ["евангелие"],
    }, "Fallback", source_url="https://www.youtube.com/watch?v=abc123")
    msg = format_livedub_info_message(card)
    assert "Оригинал: https://www.youtube.com/watch?v=abc123" in msg
    assert "Оригинал на YouTube" in msg


def test_livedub_info_prompt_requires_russian_title_order():
    src = Path("services/livedub_info.py").read_text(encoding="utf-8")
    assert "YouTube title тоже переведи на русский" in src
    assert "Название - Автор" in src
    assert "R.C. Sproul=Р. Ч. Спроул" in src


def test_livedub_info_normalizes_hashtags_and_title_case():
    from services.livedub_info import _normalize_card
    card = _normalize_card({
        "telegram_description": " В этом видео  ",
        "youtube_title": "почему вы диспенсационалист? - абнер чау",
        "youtube_description": "Описание",
        "compact_subtitles": [" Странный огонь , МакАртора . "],
        "hashtags": ["реформированный баптист", "#Покаяние", "bad tag!!!"],
    }, "Fallback")
    assert card["youtube_title"].startswith("Почему")
    assert "#РеформированныйБаптист" in card["hashtags"]
    assert "#Покаяние" in card["hashtags"]


def test_livedub_info_message_escapes_html():
    from services.livedub_info import format_livedub_info_message
    msg = format_livedub_info_message({
        "telegram_description": "5 < 7 & важно",
        "youtube_title": "A < B",
        "youtube_description": "Use <tag> & quote",
        "compact_subtitles": ["x < y"],
        "hashtags": ["#ok"],
    })
    assert "5 &lt; 7 &amp; важно" in msg
    assert "A &lt; B" in msg
    assert "Use &lt;tag&gt; &amp; quote" in msg
    assert "x &lt; y" in msg


def test_livedub_light_model_default_fallbacks_include_preview_and_stable_lite(monkeypatch):
    from services.livedub_info import get_light_model_fallbacks
    monkeypatch.delenv("GEMINI_LIGHT_FALLBACK_MODELS", raising=False)
    monkeypatch.delenv("GEMINI_LIGHT_MODEL", raising=False)
    fallbacks = get_light_model_fallbacks()
    assert "gemini-3.1-flash-lite-preview" in fallbacks
    assert "gemini-2.5-flash-lite" in fallbacks


def test_livedub_info_message_uses_safe_html_trim():
    src = Path("services/livedub_info.py").read_text(encoding="utf-8")
    assert "safe_trim_caption" in src
    assert "[:3900]" not in src


def test_livedub_info_card_sent_for_cached_file_id_too():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    helper = src[src.index("async def _send_livedub_result"):src.index("performer, title = parse_title")]
    cached_block = helper[helper.index("if _livedub_cached_file_id and context:"):helper.index("except Exception as _fid_err")]
    assert "await _send_livedub_info_card_once(None)" in cached_block
    assert "async def _send_livedub_info_card_once" in helper


def test_quick_qa_report_sent_after_video_not_before():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    helper = src[src.index("async def _send_livedub_result"):src.index("performer, title = parse_title")]
    assert "_quick_qa_report_html = format_qa_report" in helper
    assert "главный результат режима" in helper
    assert helper.index("_sent_msg = await context.bot.send_video") < helper.index("if _quick_qa_report_html:")


def test_livedub_info_card_wired_for_eng_quick_and_quick_qa():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "livedub_info_enabled" in src
    assert 'user_mode in ("eng_fast", "eng_fast_qa")' in src
    assert 'user_mode not in ("eng_fast", "eng_fast_qa")' in src
    assert "build_livedub_info_card" in src
    assert "format_livedub_info_message" in src
    assert "get_translation_subtitles(video_url, workdir)" in src


def test_livedub_light_model_env_documented():
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "GEMINI_LIGHT_MODEL=gemini-3.1-flash-lite" in env
    assert "GEMINI_LIGHT_FALLBACK_MODELS=gemini-3.1-flash-lite-preview,gemini-2.5-flash-lite" in env
    assert "GEMINI_LIGHT_ALLOW_MAIN_FALLBACK=1" in env
    assert "LIVEDUB_INFO_CARD=1" in env
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "GEMINI_LIGHT_MODEL=gemini-3.1-flash-lite" in readme


# ── Заход 9: обслуживание диска + flood control ──────────────────

def test_cleanup_botapi_server_files(tmp_path, monkeypatch):
    import os, time
    from core.utils import cleanup_botapi_server_files
    old = tmp_path / "video.mp4"; old.write_bytes(b"x" * 10)
    os.utime(old, (time.time() - 90000,) * 2)
    fresh = tmp_path / "fresh.mp4"; fresh.write_bytes(b"y")
    binlog = tmp_path / "td.binlog"; binlog.write_bytes(b"z")
    os.utime(binlog, (time.time() - 90000,) * 2)
    monkeypatch.setenv("LOCAL_BOT_API_DATA_DIR", str(tmp_path))
    n = cleanup_botapi_server_files(max_age_hours=24)
    assert n == 1
    assert not old.exists() and fresh.exists() and binlog.exists()
    monkeypatch.delenv("LOCAL_BOT_API_DATA_DIR")
    assert cleanup_botapi_server_files() == 0  # no-op без env


def test_cleanup_stale_livedub_dirs(tmp_path, monkeypatch):
    import os, time, tempfile
    from core.utils import cleanup_stale_livedub_dirs
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    stale = tmp_path / "livedub_OLD1"; stale.mkdir()
    f = stale / "v.mp4"; f.write_bytes(b"a")
    os.utime(f, (time.time() - 30000,) * 2)
    active = tmp_path / "livedub_NEW1"; active.mkdir()
    (active / "v.mp4").write_bytes(b"b")
    n = cleanup_stale_livedub_dirs(max_age_hours=6)
    assert n == 1
    assert not stale.exists() and active.exists()


def test_periodic_maintenance_wires_new_cleanups():
    src = Path("main.py").read_text(encoding="utf-8")
    assert "cleanup_botapi_server_files" in src
    assert "cleanup_stale_livedub_dirs" in src


def test_upload_retry_honors_retry_after():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert src.count('getattr(upload_err, "retry_after", None)') == 2


# ── Заход 10: vot-cli retries для длинных переводов ──────────────

def test_vot_cli_retries_for_long_translations():
    src = Path("services/yandex_live_dub.py").read_text(encoding="utf-8")
    # audio: параметризованный таймаут + ретраи (Яндекс готовит перевод минутами)
    assert "timeout: int = 480, retries: int = 2" in src
    assert "Перевод ещё готовится у Яндекса" in src
    # merge-video fallback: повтор после паузы
    assert "merge-video повтор" in src
    # NOT_AVAILABLE прерывает ретраи сразу (не ждём 90с впустую)
    audio_fn = src[src.index("def get_live_dub_audio"):src.index("def get_live_dub_video")]
    assert audio_fn.count("LIVEDUB_NOT_AVAILABLE") >= 2


def test_pipeline_waitfor_covers_retry_budget():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "timeout=1800" in src           # wait_for поднят с 600
    assert "не успел за 30 минут" in src   # сообщение синхронизировано
    # бюджет: audio worst-case 480*3+180=1620 < 1800; merge 600*2+90=1290 < 1800


def test_livedub_autofix_mutes_major_errors_by_default(monkeypatch):
    import services.livedub_mix as lm
    monkeypatch.delenv("LIVEDUB_AUTOFIX_RU_VOLUME", raising=False)
    assert lm._FIX_RU_GAIN == 0.0
    src = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert "LIVEDUB_AUTOFIX_RU_VOLUME" in src
    assert "полностью вырезается" in src
    pipe = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "русский дубляж вырезан" in pipe


def test_livedub_qa_prompt_flags_parent_sexual_mistranslation_as_major():
    src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    assert "fools bring grief to their parents" in src
    assert "fools commit sexual" in src
    assert "родителями" in src
    assert "severity=major" in src


def test_run_translation_qa_accepts_thinking_level_for_quick_mode():
    src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    assert 'thinking_level: str = "high"' in src
    assert "thinking_level=thinking_level" in src


# ── Заход 11: реюз Gemini audio_part в QA ────────────────────────

def test_qa_reuses_existing_audio_part():
    src = Path("services/livedub_qa.py").read_text(encoding="utf-8")
    assert "existing_audio_part=None" in src and "existing_client=None" in src
    assert "реюз audio_part основного анализа" in src
    # реюзнутый part НЕ попадает в uploaded (его удаляет пайплайн, не QA)
    reuse_block = src[src.index("реюз audio_part"):src.index("elif original_audio_path")]
    assert "uploaded.append" not in reuse_block
    # клиент с готовым part идёт первым в ротации
    assert "_clients_order.insert(0, existing_client)" in src
    # state-guard терпим к enum/строке
    assert 'in str(getattr(existing_audio_part, "state"' in src


def test_pipeline_passes_existing_part_to_qa():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "existing_audio_part=_qa_part" in src
    assert "existing_client=_qa_client" in src


# ── Заход 12: внешний код-ревью отчёт ────────────────────────────

def test_bot_new_starts_with_docstring():
    """Шебанг/докстринг до кода; HF_HUB env — после докстринга, до импортов."""
    import ast
    src = Path("bot_new.py").read_text(encoding="utf-8")
    assert src.startswith("#!")
    tree = ast.parse(src)
    assert isinstance(tree.body[0], ast.Expr)  # докстринг — первый узел
    assert src.index("HF_HUB_DISABLE_SYMLINKS_WARNING") < src.index("from main import")


def test_no_dead_globals_guard():
    src = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert '"_archive_parse_limit" in globals()' not in src


def test_requirements_no_duplicate_waitress():
    lines = [l.strip() for l in Path("requirements.txt").read_text(encoding="utf-8").splitlines()]
    waitress = [l for l in lines if l.startswith("waitress")]
    assert len(waitress) == 1, waitress


def test_ruff_gate_extended():
    src = Path("pyproject.toml").read_text(encoding="utf-8")
    for code in ("F811", "B023", "E722", "F841"):
        assert code in src


def test_no_proxy_fix_is_not_legacy():
    """Ревью-отчёт п.3 ОПРОВЕРГНУТ: httpx 0.28 НЕ исключает localhost из
    прокси сам — без NO_PROXY pool для 127.0.0.1 это HTTPProxy (проверено
    в этом репо: прод-лог 18:13 TimedOut + повторная проверка _pool).
    Фикс обязан остаться."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert 'os.environ["NO_PROXY"]' in src


# ── Заход 13: Path-отправка для всех видео ───────────────────────

def test_all_video_sends_use_path_not_handle():
    """Все reply_video/send_video шлют Path (file:// при local_mode):
    HTTP-передача >100MB на локальный сервер ловит TimedOut (PTB #4528)."""
    import re
    for fname in ("pipelines/clips.py", "pipelines/montage.py", "pipelines/shorts.py",
                  "handlers/callbacks.py", "handlers/commands.py",
                  "pipelines/main_pipeline.py"):
        src = Path(fname).read_text(encoding="utf-8")
        # ни одного video=vf / video=f (file handle)
        assert not re.search(r"video=\s*v?f\b", src), fname


# ── Заход 14: параллельная обработка апдейтов ────────────────────

def test_concurrent_updates_enabled():
    """Без concurrent_updates бот молчит на команды все 10-20 минут
    обработки видео (включая /stop). Гонки закрыты: video-lock,
    UPSERT rate_limit, SQLite WAL, threading.Lock у Whisper-синглтона."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert ".concurrent_updates(" in src


# ── Заход 15: error handler + диск для temp ──────────────────────

def test_global_error_handler_registered():
    src = Path("main.py").read_text(encoding="utf-8")
    assert "add_error_handler" in src
    assert "Внутренняя ошибка при обработке" in src
    # сетевой шум не транслируется юзеру
    assert "NetworkError" in src.split("add_error_handler")[0].split("_global_error_handler")[1]


def test_disk_check_covers_tempdir():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "disk_usage(_tf.gettempdir())" in src


# ── Заход 16: ENG fail-fast и гарантированный ответ юзеру ────────

def test_vot_client_fail_fast_before_processing():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "_ensure_vot_helper" in src
    assert "_check_vot_cli()" in src
    assert "нет клиента для Яндекс LiveDub" in src
    # Quick: стоп сразу; Full: деградация до RUS-анализа
    assert 'user_mode = "rus"  # ENG Full деградирует' in src


def test_eng_quick_never_silent():
    """При сбое перевода ENG Quick юзер получает объяснение, а не тишину."""
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "_delivered = await _send_livedub_result()" in src
    assert "Перевод «Живые голоса» не получился" in src


def test_send_helper_returns_delivery_status():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "async def _send_livedub_result() -> bool:" in src
    helper = src[src.index("async def _send_livedub_result"):src.index("performer, title = parse_title")]
    assert "return True" in helper and "return False" in helper


def test_eng_full_livedub_failure_is_not_silent():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    helper = src[src.index("async def _send_livedub_result"):src.index("performer, title = parse_title")]
    bg = src[src.index("async def _run_livedub_bg"):src.index("live_dub_task = asyncio.create_task")]
    assert "_notify_full_livedub_failure" in helper
    assert "Живой перевод Яндекса не получился" in helper
    assert "проверка точности перевода не запускались" in helper
    assert ".not_available" in bg and ".livedub_failed" in bg


# ── Заход 17: edited messages не ломают хендлеры ─────────────────

def test_handlers_ignore_edited_messages():
    """edited_message даёт update.message=None -> AttributeError в хендлерах;
    редактирование старой ссылки не должно перезапускать обработку видео."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "filters.UpdateType.MESSAGE" in src
    # MessageHandler с guard
    assert "& filters.UpdateType.MESSAGE, handle_message" in src
    # все CommandHandler с фильтром
    import re
    handlers = re.findall(r"app\.add_handler\(CommandHandler\([^)]+\)", src)
    assert handlers, "no CommandHandlers found"
    for h in handlers:
        assert "_MSG_ONLY" in h, h


def test_edited_message_filter_behavior():
    """Живая проверка против установленного PTB."""
    import datetime
    from telegram.ext import filters as tg_filters
    from telegram import Update, Message, Chat, User
    f = tg_filters.TEXT & ~tg_filters.COMMAND & tg_filters.UpdateType.MESSAGE
    msg = Message(message_id=1, date=datetime.datetime.now(),
                  chat=Chat(id=1, type="private"),
                  from_user=User(id=2, is_bot=False, first_name="x"),
                  text="https://youtu.be/abc")
    assert not f.check_update(Update(update_id=1, edited_message=msg))
    assert f.check_update(Update(update_id=2, message=msg))


# ── Заход 18: startup-диагностика + ffprobe-fallback ─────────────

def test_startup_tool_diagnostics():
    src = Path("main.py").read_text(encoding="utf-8")
    for tool in ("ffmpeg", "ffprobe", "VOT helper (@vot.js/node)", "vot-cli-live fallback"):
        assert tool in src
    assert "молча деградирует" in src


def test_probe_meta_ffmpeg_fallback(tmp_path):
    """Без ffprobe метаданные берутся из stderr ffmpeg -i (Windows-кейс)."""
    src = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert "Duration:" in src and "Video:" in src
    # живой тест: в CI ffprobe может быть — проверяем структуру fallback-ветки
    fb = src[src.index("def probe_video_meta"):src.index("def make_video_thumbnail")]
    assert 'which("ffmpeg")' in fb


def test_vot_helper_and_vot_cli_fallback_are_distinct_in_diagnostics():
    """@vot.js helper — основной путь; vot-cli-live теперь только fallback."""
    main = Path("main.py").read_text(encoding="utf-8")
    assert "VOT helper (@vot.js/node)" in main
    assert "vot-cli-live fallback" in main
    cmd = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert "vot-helper" in cmd and "vot-cli-fallback" in cmd


# ── Заход 19: кликабельные таймкоды в QA-отчёте ──────────────────

def test_qa_report_clickable_timestamps():
    from services.livedub_qa import format_qa_report
    qa = {"score": 88, "verdict": "v", "issues": [
        {"time": "14:32", "heard": "x", "problem": "p", "should_be": "y", "severity": "major"},
        {"time": "trash", "heard": "x", "problem": "p", "should_be": "y", "severity": "minor"},
    ]}
    out = format_qa_report(qa, video_url="https://www.youtube.com/watch?v=ID")
    assert 't=872"><b>14:32</b></a>' in out          # ссылка на секунду
    assert "<b>trash</b>" in out and "trash</a>" not in out  # мусор без ссылки
    assert "<a href" not in format_qa_report(qa)      # без url — без ссылок


# ── Заход 20: автофикс сохраняет сабы; auto data-dir ─────────────

def test_autofix_restores_burned_subtitles():
    src = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    fb = src[src.index("def apply_qa_audio_fixes"):]
    assert "gemini_subs.srt" in fb
    assert "merge_subtitles" in fb


def test_botapi_cleanup_autodetects_default_dir():
    src = Path("core/utils.py").read_text(encoding="utf-8")
    assert "C:/ProgramData/TelegramBotAPI/data" in src


# ── Заход 21: RNNoise-опция ──────────────────────────────────────

def test_rnnoise_optional_and_safe():
    from services.livedub_mix import build_mix_filter
    fc = build_mix_filter(0.45, 1.3, 600, rnnoise_model="")  # выключено
    assert "arnndn" not in fc
    fc2 = build_mix_filter(0.45, 1.3, 600, rnnoise_model="/nonexistent/m.rnnn")
    assert "arnndn" not in fc2  # несуществующая модель -> тихо пропущена
    src = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert "повтор без шумодава" in src  # fallback при битой модели/сборке
    # RU-дорожка Яндекса шумодавом не трогается
    ru_part = src[src.index('ru_chain = f"[1:a]'):src.index('en_chain')]
    assert "_dn" not in ru_part


# ── Заход 22: hardsub + concurrent fragments ─────────────────────

def test_subtitles_burned_for_telegram_visibility():
    """Telegram-плеер не показывает mov_text — сабы прожигаются в кадр."""
    src = Path("services/eng_subtitles.py").read_text(encoding="utf-8")
    assert "_burn_subtitles" in src
    assert "force_style" in src and "BorderStyle=3" in src
    assert "LIVEDUB_HARDSUB" in src        # ручка отключения
    assert "fallback на mov_text" in src   # graceful деградация


def test_ytdlp_concurrent_fragments():
    from services.ffmpeg import _build_ytdlp_base_args
    args = " ".join(_build_ytdlp_base_args())
    assert "--concurrent-fragments" in args


# ── Заход 23: глючная GPU — принудительный CPU-энкодер ───────────

def test_video_force_cpu_explicit_only(monkeypatch):
    """NVENC — отдельный ASIC, не CUDA: WHISPER_FORCE_CPU его НЕ отключает
    (уточнено пользователем: NVENC стабилен при глючном CUDA).
    VIDEO_FORCE_CPU=1 — только явное отключение."""
    import services.ffmpeg as ff
    # WHISPER_FORCE_CPU не влияет на видео-энкодер
    monkeypatch.setattr(ff, "_VIDEO_ENCODER", None)
    monkeypatch.setenv("WHISPER_FORCE_CPU", "1")
    monkeypatch.delenv("VIDEO_FORCE_CPU", raising=False)
    ff._get_video_encoder()  # автопроба (в CI nvenc нет -> libx264; не падает)
    # явное принуждение работает
    monkeypatch.setattr(ff, "_VIDEO_ENCODER", None)
    monkeypatch.setenv("VIDEO_FORCE_CPU", "1")
    enc, _, _ = ff._get_video_encoder()
    assert enc == "libx264"


def test_video_cpu_preset_knob(monkeypatch):
    import services.ffmpeg as ff
    monkeypatch.setattr(ff, "_VIDEO_ENCODER", None)
    monkeypatch.setenv("WHISPER_FORCE_CPU", "1")
    monkeypatch.setenv("VIDEO_CPU_PRESET", "medium")
    _, _, preset = ff._get_video_encoder()
    assert preset == ["-preset", "medium"]
    # мусорное значение -> дефолт
    monkeypatch.setattr(ff, "_VIDEO_ENCODER", None)
    monkeypatch.setenv("VIDEO_CPU_PRESET", "garbage")
    _, _, preset = ff._get_video_encoder()
    assert preset == ["-preset", "veryfast"]


# ── Заход 24: NVENC возвращён + качество ─────────────────────────

def test_nvenc_quality_formula(monkeypatch):
    """NVENC: p5+tune hq+spatial-aq+lookahead+b:v 0 — VMAF-паритет с
    libx264 medium. p4 без -b:v 0 давал нечестный CQ."""
    import services.ffmpeg as ff
    monkeypatch.setattr(ff, "_VIDEO_ENCODER", "h264_nvenc")
    enc, q, p = ff._get_video_encoder()
    assert enc == "h264_nvenc"
    assert "-b:v" in q and "-spatial-aq" in q and "-rc-lookahead" in q
    assert p == ["-preset", "p5", "-tune", "hq"]


# ── Заход 25: hardsub на NVENC + MP3-нормализация ────────────────

def test_hardsub_uses_detected_encoder():
    src = Path("services/eng_subtitles.py").read_text(encoding="utf-8")
    fb = src[src.index("def _burn_subtitles"):src.index("async def merge_subtitles")]
    assert "_get_video_encoder" in fb
    assert '"libx264", "-preset", "veryfast"' not in fb  # хардкод убран


def test_mp3_normalization_is_lossless_not_loudnorm(monkeypatch):
    """Round 26 исправляет round 25: single-pass loudnorm в энкоде КАЧАЛ
    динамику речи (pumping). Теперь mp3gain (lossless, только заголовки
    фреймов) ПОСЛЕ скачивания; нет mp3gain — файл не трогаем вовсе."""
    import services.ffmpeg as ff
    args = " ".join(ff._build_ytdlp_base_args())
    assert "loudnorm" not in args  # из энкода убран
    assert hasattr(ff, "normalize_mp3_lossless")
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert src.count("normalize_mp3_lossless") >= 2  # обе ветки скачивания
    # выключатель уважается
    monkeypatch.setenv("MP3_LOUDNORM", "0")
    assert ff.normalize_mp3_lossless(Path("/nonexistent.mp3")) is False


# ── Заход 27: SponsorBlock opt-in + ID3-метаданные ───────────────

def test_sponsorblock_optin_validated(monkeypatch):
    import pipelines.main_pipeline as mp
    monkeypatch.delenv("SPONSORBLOCK_REMOVE", raising=False)
    assert mp._sponsorblock_args() == []                    # default OFF
    monkeypatch.setenv("SPONSORBLOCK_REMOVE", "sponsor,selfpromo")
    assert mp._sponsorblock_args() == ["--sponsorblock-remove", "sponsor,selfpromo"]
    monkeypatch.setenv("SPONSORBLOCK_REMOVE", "sponsor,hack_category")
    assert mp._sponsorblock_args() == []                    # мусор отвергнут


def test_mp3_embeds_id3_metadata():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert src.count('"--embed-metadata", "--embed-thumbnail"') == 2  # обе ветки


# ── Заход 28: ID3-главы из Gemini-таймкодов ──────────────────────

def test_mp3_chapters_embed_and_validate(tmp_path):
    import subprocess, shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        import pytest
        pytest.skip("no ffmpeg in CI")
    mp3 = tmp_path / "a.mp3"
    subprocess.run([ffmpeg, "-f", "lavfi", "-i", "sine=frequency=300:duration=30",
                    "-c:a", "libmp3lame", "-y", str(mp3)], capture_output=True, timeout=60)
    from services.mp3_chapters import embed_chapters
    ts = [{"time": "0:00", "topic": "**Один**"}, {"time": "0:10", "topic": "Два"},
          {"time": "trash", "topic": "x"}, {"time": "0:10", "topic": "дубль"}]
    assert embed_chapters(mp3, ts, duration_sec=30)
    from mutagen.id3 import ID3
    tags = ID3(str(mp3))
    chaps = tags.getall("CHAP")
    assert len(chaps) == 2 and len(tags.getall("CTOC")) == 1
    titles = {c.sub_frames.getall("TIT2")[0].text[0] for c in chaps}
    assert "Один" in titles  # markdown очищен
    # одна точка — не навигация
    assert not embed_chapters(mp3, [{"time": "0:00", "topic": "x"}], 30)


def test_mp3_chapters_wired_both_branches():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert src.count("embed_chapters") >= 4  # 2 импорта + 2 вызова


# ── Заход 29: mp3 file_id resend при кэш-хите ────────────────────

def test_audio_file_id_roundtrip(tmp_path, monkeypatch):
    import core.database as db
    import core.globals as g
    test_db = tmp_path / "t.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    monkeypatch.setattr(g, "DB_PATH", test_db)
    db.db_init()
    db.db_set_audio_file_id("vidA", "CQAC_audio_fid")
    assert db.db_get("vidA")["audio_file_id"] == "CQAC_audio_fid"
    # generic-сеттер защищён whitelist'ом
    import pytest
    with pytest.raises(ValueError):
        db._db_set_file_id("vidA", "evil_column; DROP TABLE", "x")


def test_cache_mp3_resend_wiring():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert 'cached or {}).get("audio_file_id")' in src
    assert "adb_set_audio_file_id" in src
    # протухший file_id чистится
    assert "file_id протух" in src


# ── Заход 30: file_id с первой отправки + faststart-аудит ────────

def test_audio_file_id_saved_on_first_send():
    """Зазор round 29 закрыт: file_id сохраняется уже при ПЕРВОЙ отправке
    свежескачанного mp3 — повтор мгновенен сразу, а не со 2-го раза."""
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "_sent_fresh_audio" in src
    # оба пути сохранения: первая отправка + кэш-заливка
    assert src.count("adb_set_audio_file_id(media_id") >= 2


def test_all_video_renders_have_faststart():
    """moov-атом в начале файла = мгновенный стриминг в Telegram."""
    import re
    for fname in ("services/shorts_video.py", "services/render_clips_montage.py"):
        src = Path(fname).read_text(encoding="utf-8")
        renders = len(re.findall(r'"-c:v"', src))
        fasts = src.count("faststart")
        assert fasts >= renders, f"{fname}: {renders} renders, {fasts} faststart"


# ── Заход 31: бэкап БД + chat actions ────────────────────────────

def test_db_backup_creates_and_rotates(tmp_path, monkeypatch):
    import core.database as db
    import core.globals as g
    test_db = tmp_path / "bot.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    monkeypatch.setattr(g, "DB_PATH", test_db)
    db.db_init()
    db.db_set_audio_file_id("v1", "fid")
    out = db.db_backup()
    assert out and Path(out).exists()
    assert db.db_backup() == ""  # одна копия в сутки
    import sqlite3
    row = sqlite3.connect(out).execute(
        "SELECT audio_file_id FROM video_cache WHERE video_id=?", ("v1",)).fetchone()
    assert row and row[0] == "fid"  # копия валидна и полна
    # ротация: старые копии удаляются
    for d in ("20200101", "20200102", "20200103"):
        (tmp_path / f"bot.db.bak.{d}").write_bytes(b"x")
    db_today = Path(out)
    db_today.unlink()
    db.db_backup(max_keep=2)
    baks = sorted(tmp_path.glob("bot.db.bak.*"))
    assert len(baks) == 2, baks


def test_backup_wired_into_maintenance():
    src = Path("main.py").read_text(encoding="utf-8")
    assert "db_backup" in src


def test_chat_actions_during_uploads():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert 'send_action("upload_voice")' in src
    assert 'action="upload_video"' in src


# ── Заход 32: /status + чистка if True ───────────────────────────

def test_no_if_true_hacks_left():
    import re
    for fname in ("pipelines/clips.py", "pipelines/montage.py", "pipelines/shorts.py",
                  "handlers/callbacks.py", "handlers/commands.py"):
        src = Path(fname).read_text(encoding="utf-8")
        assert not re.search(r"^\s*if True:", src, re.M), fname


def test_status_command_registered():
    src = Path("main.py").read_text(encoding="utf-8")
    assert 'CommandHandler("status"' in src
    cmd = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert "async def status_command" in cmd
    for probe in ("ffmpeg", "vot-helper", "vot-cli-fallback", "mp3gain", "Диск", "бэкап"):
        assert probe in cmd
    assert "yt-js(Deno≥2.3/Node≥22)" in cmd
    assert "LiveDub:" in cmd and "base-tail" in cmd and "vot-duration=floor" in cmd and "cache=" in cmd


def test_startup_logs_livedub_mix_diagnostics():
    src = Path("main.py").read_text(encoding="utf-8")
    assert "LiveDub mix: delay=%dms base_tail=%dms" in src
    assert "vot_duration=floor" in src
    assert "current_livedub_file_id_cache_fingerprint" in src


def test_proxy_knobs_documented_for_no_tun_mode():
    env = Path(".env.example").read_text(encoding="utf-8")
    for key in (
        "TELEGRAM_PROXY_URL",
        "LOCAL_BOT_API_PROXY_URL",
        "LOCAL_BOT_API_TDLIB_PROXY_TYPE",
        "LOCAL_BOT_API_PROXY_SERVER",
        "LOCAL_BOT_API_PROXY_PORT",
        "LOCAL_BOT_API_CLOUD_FALLBACK",
        "TELEGRAM_PROXY_HTTP_FALLBACK",
    ):
        assert key in env
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "Работа без TUN/VPN" in readme
    req = Path("requirements.txt").read_text(encoding="utf-8")
    assert "python-telegram-bot[socks]" in req


def test_proxy_wiring_for_cloud_and_local_bot_api():
    src = Path("main.py").read_text(encoding="utf-8")
    # Cloud Bot API: HTTPXRequest gets proxy only when LOCAL_BOT_API_URL is empty.
    assert "TELEGRAM_PROXY_URL" in src
    assert '_request_kwargs["proxy"] = _cloud_fallback_proxy_url or _telegram_proxy_url' in src
    assert "not LOCAL_BOT_API_URL" in src
    # Local Bot API: official telegram-bot-api.exe supports --proxy=<HTTP URL>,
    # but NOT old bogus TDLib flags; those caused immediate startup crashes.
    assert "--proxy=" in src
    assert "LOCAL_BOT_API_PROXY_URL" in src
    assert "*_botapi_proxy_args" in src
    assert "--proxy-server" in src  # mentioned only in warning/comment
    assert "не поддерживает SOCKS/MTProto" in src
    assert "LOCAL_BOT_API_CLOUD_FALLBACK" in src
    assert "Авто-fallback: перехожу на облачный Bot API" in src
    assert "no-TUN fast path" in src
    assert "socks5h://" in src
    assert "TELEGRAM_PROXY_HTTP_FALLBACK" in src
    assert "пакет socksio не установлен" in src
    assert "http://" in src


def test_status_reports_proxy_layers():
    cmd = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert "TELEGRAM_PROXY_URL" in cmd
    assert "LOCAL_BOT_API_PROXY_URL" in cmd
    assert "Proxy: PTB" in cmd and "local Bot API HTTP" in cmd


# ── Заход 33: pre-flight Bot API + сетевой backoff ───────────────

def test_preflight_waits_for_local_server():
    """Live log 2026-06-11 02:03: сервер не запущен -> бесконечный цикл
    стен-трейсбеков каждые 5с. Теперь: TCP pre-flight с человеческим
    сообщением и ожиданием до 5 минут."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "socket.create_connection" in src
    assert "НЕ ЗАПУЩЕН" in src
    assert "не поднялся за 5 минут" in src
    assert "/getMe" in src
    assert "getMe OK" in src
    assert "порт открыт, но /getMe не работает" in src
    assert "LOCAL_BOT_API_CLOUD_FALLBACK" in src
    assert "не жду 60с локальный /getMe" in src


def test_network_errors_get_short_log_and_backoff():
    src = Path("main.py").read_text(encoding="utf-8")
    assert "_net_fail_streak" in src
    assert "ConnectError" in src
    # экспоненциальный backoff с потолком 60с
    assert "min(restart_delay * (2 **" in src


# ── Заход 33b: бот сам поднимает Bot API сервер ──────────────────

def test_bot_autostarts_local_server():
    """Start Bot.bat в репо НЕ содержит автостарт (секреты не коммитятся в
    bat) — теперь бот стартует telegram-bot-api.exe сам из .env-ключей."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "_try_autostart_botapi" in src
    assert "TELEGRAM_API_ID" in src and "TELEGRAM_API_HASH" in src
    assert "LOCAL_BOT_API_AUTOSTART" in src   # выключатель
    assert "0x8 | 0x200 | 0x08000000" in src  # detached на Windows
    # автостарт пробуется ДО входа в цикл ожидания
    assert src.index("_autostart_tried = True") < src.index("Жду появления сервера")


# ── Заход 34: диагностика автостарта сервера ─────────────────────

def test_autostart_diagnoses_dead_server():
    """02:18 live log: автостарт сработал, но сервер не поднялся, а ПРИЧИНУ
    мы не видели (DEVNULL). Теперь: лог сервера в файл, проверка что процесс
    жив через 2с, хвост лога при мгновенной смерти, grace до 20с (первый
    старт создаёт binlog и коннектится к DC)."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "botapi-server.log" in src
    assert "--verbosity=2" in src
    assert "mp3bot autostart" in src
    assert "stderr=_sp.STDOUT" in src
    assert "stdout=_lf" in src
    assert '"--api-hash=***"' in src
    # round 35: liveness-проверка вынесена в _spawn() (poll внутри неё)
    assert "_pr.poll() is None" in src
    assert "упал сразу" in src
    assert "for _grace in range(10)" in src


def test_polling_started_log_is_after_start_polling():
    src = Path("main.py").read_text(encoding="utf-8")
    assert "📡 Запускаю polling getUpdates" in src
    assert "✅ Polling запущен — бот слушает сообщения" in src
    assert src.index("start_polling(") < src.index("✅ Polling запущен")
    assert "logger.info(\"✅ Бот запущен!\")" not in src


# ── Заход 35: ACL-fallback для data-dir сервера ──────────────────

def test_autostart_falls_back_to_user_dir_on_acl():
    """02:28 live log: 'Access is denied' на tqueue.binlog — data-dir в
    ProgramData принадлежит админу (создан run-as-admin), бот стартует
    сервер от юзера. Fallback на %LOCALAPPDATA% + подсказка icacls."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert '"Access is denied" in _tail' in src
    assert "LOCALAPPDATA" in src
    assert "icacls" in src           # рецепт постоянного решения
    assert "LOCAL_BOT_API_DATA_DIR" in src  # cleanup переключается на новую папку


# ── Заход 36: PowerShell-совместимая подсказка + env-доки ────────

def test_acl_hint_is_shell_aware():
    """icacls-подсказка с %USERNAME% падала в PowerShell 7 (user report):
    OI парсился как команда. Теперь оба варианта: pwsh и cmd."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "${env:USERNAME}" in src   # PowerShell-форма
    assert "%%USERNAME%%" in src      # cmd-форма (экранированный %)


def test_all_env_knobs_documented():
    env = Path(".env.example").read_text(encoding="utf-8")
    for v in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "LOCAL_BOT_API_EXE",
              "LOCAL_BOT_API_AUTOSTART", "LOCAL_BOT_API_DATA_DIR",
              "MP3_LOUDNORM", "SPONSORBLOCK_REMOVE", "LIVEDUB_RNNOISE_MODEL",
              "VIDEO_CPU_PRESET", "YTDLP_FRAGMENTS", "LIVEDUB_HARDSUB",
              "LIVEDUB_ORIG_VOLUME", "LIVEDUB_DELAY_MS", "LIVEDUB_TAIL_MARGIN_MS",
              "LIVEDUB_TAIL_FREEZE_MAX_SEC", "SYNOPSIS_YT_TRANSCRIPT",
              "SYNOPSIS_YT_TRANSCRIPT_MAX_CHARS", "SYNOPSIS_YT_TRANSCRIPT_MIN_COVERAGE",
              "SYNOPSIS_VERBATIM_PROMPT", "MAX_FILE_SIZE_MB"):
        assert v in env, f"{v} не задокументирован в .env.example"


# ── Заход 37: cleanup следует за реальной папкой сервера ─────────

def test_acl_fallback_overrides_cleanup_dir():
    """setdefault не перезаписывал .env-значение -> cleanup чистил
    недоступную ProgramData, а реальная LOCALAPPDATA росла бесконечно."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert 'os.environ["LOCAL_BOT_API_DATA_DIR"] = str(_user_data)' in src
    assert 'setdefault("LOCAL_BOT_API_DATA_DIR"' not in src


def test_cleanup_autodetects_both_standard_dirs(tmp_path, monkeypatch):
    import os as _os, time as _t
    from core.utils import cleanup_botapi_server_files
    # LOCALAPPDATA-кандидат работает без подмены os.name (гейт убран:
    # monkeypatch os.name ломает pathlib — WindowsPath на posix)
    lad = tmp_path / "lad"
    target = lad / "TelegramBotAPI" / "data"
    target.mkdir(parents=True)
    old_f = target / "v.mp4"; old_f.write_bytes(b"x")
    _os.utime(old_f, (_t.time() - 90000,) * 2)
    monkeypatch.setenv("LOCALAPPDATA", str(lad))
    monkeypatch.delenv("LOCAL_BOT_API_DATA_DIR", raising=False)
    assert cleanup_botapi_server_files(max_age_hours=24) == 1
    assert not old_f.exists()


# ── Заход 38/39: строго live; TTS — только явный opt-in ─

def test_live_mode_does_not_tts_fallback_by_default():
    """Пользователь требует только «Живые голоса». Поэтому обычный TTS
    не должен включаться по умолчанию и маскировать настоящую проблему
    SESSION_REQUIRED / VOT_API_TOKEN."""
    src = Path("services/yandex_live_dub.py").read_text(encoding="utf-8")
    assert 'voice_style: str = "live"' in src   # параметризован
    mix = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert 'voice_style="tts"' in mix           # аварийный opt-in оставлен
    assert 'os.getenv("LIVEDUB_TTS_FALLBACK", "0")' in mix  # default OFF
    assert "режим ENG — это ТОЛЬКО «Живые голоса»" in mix
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "LIVEDUB_TTS_FALLBACK=0" in env
    pipe = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "обычные голоса" in pipe             # caption честен, если opt-in включат


# ── Заход 39: НАСТОЯЩАЯ причина отказов — OAuth для живых голосов ─

def test_new_protocol_helper_exists():
    """Живой тест @vot.js/node ДОКАЗАЛ: на те же ролики Яндекс отвечает
    status=7 SESSION_REQUIRED (нужен OAuth-токен, как в VOT 1.10), а не
    'перевод недоступен'. Ролик из кэша (уже переведён юзером в
    Я.Браузере) отдаёт live-MP3 мгновенно — поэтому «Shorts раньше
    работали». Новый протокол: vot_helper/vot_live.mjs."""
    helper = Path("vot_helper/vot_live.mjs")
    assert helper.exists()
    src = helper.read_text(encoding="utf-8")
    assert "VOT_API_TOKEN" in src                  # токен из env
    assert "YANDEX_OAUTH_TOKEN" in src             # альтернативное имя токена
    assert "--duration" in src                     # точный cache-key Яндекса
    assert "videoData.duration" in src             # duration прокидывается в @vot.js
    assert "useLivelyVoice" in src                 # новый протокол
    assert "LIVEDUB_AUTH_REQUIRED" in src          # маркер status=7
    assert "LIVEDUB_NOT_AVAILABLE" in src          # честный отказ
    pkg = Path("vot_helper/package.json")
    assert pkg.exists() and "@vot.js/node" in pkg.read_text(encoding="utf-8")


def test_new_protocol_first_in_get_live_dub_audio():
    """get_live_dub_audio должен пробовать новый протокол ПЕРВЫМ,
    со старым vot-cli-live как fallback (нет node — нет паники)."""
    src = Path("services/yandex_live_dub.py").read_text(encoding="utf-8")
    assert "_ensure_vot_helper" in src
    assert "_get_audio_new_protocol" in src
    # новый протокол вызывается до _check_vot_cli в get_live_dub_audio
    body = src.split("async def get_live_dub_audio", 1)[1]
    assert body.index("_ensure_vot_helper") < body.index("_check_vot_cli()")
    # AUTH_REQUIRED пробрасывается наверх (не маскируется под сбой)
    assert "LIVEDUB_AUTH_REQUIRED" in body


def test_auth_required_marker_and_user_hint():
    """При SESSION_REQUIRED юзер получает инструкцию про VOT_API_TOKEN,
    а не общую отписку «перевод недоступен»."""
    mix = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert ".auth_required" in mix                 # маркер в workdir
    assert "LIVEDUB_AUTH_REQUIRED" in mix          # не маскируется TTS по умолчанию
    pipe = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "VOT_API_TOKEN" in pipe                 # подсказка юзеру
    assert "YANDEX_OAUTH_TOKEN" in pipe            # alias тоже подсказан
    assert ".auth_required" in pipe
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "VOT_API_TOKEN" in env                  # задокументирован
    assert "YANDEX_OAUTH_TOKEN" in env             # alias задокументирован
    assert "rust-server-531j.onrender.com" in env  # как получить


def test_merge_video_uses_new_protocol_before_old_vot_merge():
    """Если pro-mix выключен, get_live_dub_video всё равно должен идти через
    новый протокол/OAuth, а не сразу через старый vot-cli --merge-video."""
    src = Path("services/yandex_live_dub.py").read_text(encoding="utf-8")
    body = src.split("async def get_live_dub_video", 1)[1]
    assert "get_live_dub_audio(video_url" in body
    assert "download_original_video" in body
    assert "mix_tracks" in body
    assert body.index("get_live_dub_audio(video_url") < body.index("_check_vot_cli()")


def test_pipeline_accepts_new_helper_without_global_vot_cli():
    """Fail-fast не должен требовать vot-cli-live, если доступен node helper
    нового протокола."""
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "_ensure_vot_helper" in src
    assert "старый резерв: npm install -g vot-cli-live" in src
    assert src.index("_ensure_vot_helper") < src.index("_check_vot_cli()")



def test_vot_duration_and_token_alias_threaded_through():
    """Для YouTube @vot.js сам duration не знает и подставляет default 343s;
    реальная длительность из yt-dlp должна попадать в VOT cache-key. Также
    поддерживаем оба имени OAuth-токена."""
    helper = Path("vot_helper/vot_live.mjs").read_text(encoding="utf-8")
    assert "YANDEX_OAUTH_TOKEN" in helper
    assert "knownDuration" in helper and "videoData.duration = knownDuration" in helper
    yld = Path("services/yandex_live_dub.py").read_text(encoding="utf-8")
    assert "duration: float = 0.0" in yld
    assert "_vot_request_duration" in yld
    assert 'cmd += ["--duration", str(request_duration)]' in yld
    mix = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert "build_pro_dub(video_url: str, workdir: Path, duration: float = 0.0" in mix
    assert "get_live_dub_audio(video_url, workdir, duration=duration" in mix
    pipe = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "build_pro_dub(video_url, workdir, duration=duration" in pipe
    assert "duration=duration," in pipe


def test_vot_request_duration_uses_floor_for_yandex_cache_key(monkeypatch):
    import services.yandex_live_dub as yld
    monkeypatch.setenv("LIVEDUB_VOT_DURATION_PAD_SEC", "9")  # legacy knob ignored on purpose
    assert yld._vot_request_duration(37.0) == 37
    assert yld._vot_request_duration(37.9) == 37
    src = Path("services/yandex_live_dub.py").read_text(encoding="utf-8")
    assert "лишний паддинг" in src and "cache-key" in src


def test_vot_token_alias_functional(monkeypatch):
    import services.yandex_live_dub as yld
    monkeypatch.delenv("VOT_API_TOKEN", raising=False)
    monkeypatch.delenv("YANDEX_OAUTH_TOKEN", raising=False)
    assert yld._vot_oauth_token_present() is False
    monkeypatch.setenv("YANDEX_OAUTH_TOKEN", "tok")
    assert yld._vot_oauth_token_present() is True
    monkeypatch.setenv("VOT_API_TOKEN", "tok2")
    assert yld._vot_oauth_token_present() is True


def test_vot_token_startup_diagnostic():
    """Startup-диагностика 🔧 предупреждает об отсутствии VOT_API_TOKEN/YANDEX_OAUTH_TOKEN."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert 'os.getenv("VOT_API_TOKEN"' in src
    assert "YANDEX_OAUTH_TOKEN" in src
    helper = Path("vot_helper/vot_live.mjs").read_text(encoding="utf-8")
    assert "VOT_API_TOKEN/YANDEX_OAUTH_TOKEN не задан" in helper


def test_vot_token_is_documented_in_readme_help_and_status():
    """VOT_API_TOKEN должен быть виден не только в .env.example, но и в
    пользовательской документации/админ-статусе."""
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "VOT_API_TOKEN" in readme
    assert "LIVEDUB_TTS_FALLBACK=0" in readme
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "YTDLP_COOKIES_FROM_BROWSER" in env
    assert "YANDEX_OAUTH_TOKEN" in env
    assert "LIVEDUB_TITLE_TRANSLATE=1" in env
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert "Для стабильных «Живых голосов» нужен VOT_API_TOKEN" in commands
    assert "YANDEX_OAUTH_TOKEN" in commands
    assert "🔑 VOT_API_TOKEN/YANDEX_OAUTH_TOKEN" in commands


def test_env_example_gemini_model_is_not_truncated():
    """Копирование .env.example не должно ставить несуществующую модель из-за опечатки."""
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "GEMINI_MODEL=gemini-3.5-flash" in env
    assert "gemini-3.5-flas\n" not in env
