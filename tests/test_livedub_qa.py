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
    (tmp_path / "yt-dlp.conf").write_text("--cookies-from-browser firefox", encoding="utf-8")
    ck = tmp_path / "cookies.txt"
    ck.write_text("# Netscape HTTP Cookie File", encoding="utf-8")
    monkeypatch.setattr(ff, "COOKIES_FILE", ck)
    args = ff._build_ytdlp_base_args()
    joined = " ".join(args)
    assert "--cookies " + str(ck) in joined or str(ck) in joined
    assert "--config-location" not in joined  # конф с куками пропущен
    # без cookies.txt конф снова подключается
    monkeypatch.setattr(ff, "COOKIES_FILE", tmp_path / "missing.txt")
    args2 = " ".join(ff._build_ytdlp_base_args())
    assert "--config-location" in args2


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

def test_vot_cli_fail_fast_before_processing():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "_check_vot_cli()" in src
    assert "vot-cli-live не найден" in src
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
    for tool in ("ffmpeg", "ffprobe", "vot-cli-live"):
        assert tool in src
    assert "молча деградирует" in src


def test_probe_meta_ffmpeg_fallback(tmp_path):
    """Без ffprobe метаданные берутся из stderr ffmpeg -i (Windows-кейс)."""
    src = Path("services/livedub_mix.py").read_text(encoding="utf-8")
    assert "Duration:" in src and "Video:" in src
    # живой тест: в CI ffprobe может быть — проверяем структуру fallback-ветки
    fb = src[src.index("def probe_video_meta"):src.index("def make_video_thumbnail")]
    assert 'which("ffmpeg")' in fb


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
