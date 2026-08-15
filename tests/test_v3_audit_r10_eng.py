"""AUDIT R10 (2026-07-05): ENG/LiveDub перевод + QA-защита.

Находки живого аудита цепочки Яндекс-перевода:
1. QA c пустым/битым SRT уходил в Gemini ВООБЩЕ без дубляжа (ни аудио,
   ни текста) — весь отчёт был бы галлюцинацией.
2. Промт QA утверждал «второй файл — ДУБЛЯЖ», когда файл был один
   (режим EN-аудио + SRT-текст).
3. Текстовый fallback QA жёстко ставил thinking_level="high" — Quick QA
   на лёгкой модели просил minimal и не получал его.
4. Quick QA был намертво загейчен на SRT: без старого vot-cli субтитры
   не скачиваются никогда → режим «перевод + проверка» молча не проверял.
5. _find_latest_file мог выдать original_audio/оригинал за перевод.
6. lang исходника не доезжал до vot-merge/tts-fallback путей.
"""
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ── 1-2. run_translation_qa: SRT прежде извлечения + честный промт ──

def _qa_fn_src() -> str:
    src = _read("services/livedub_qa.py")
    return src.split("async def _run_translation_qa_base", 1)[1].split(
        "\n\nasync def run_translation_qa", 1
    )[0]


def test_qa_parses_srt_before_deciding_dub_audio():
    fn = _qa_fn_src()
    parse_idx = fn.index("dub_timed_text = srt_to_timed_text")
    extract_idx = fn.index("_extract_audio_for_qa(dub_video_path")
    assert parse_idx < extract_idx, "SRT должен парситься ДО решения об извлечении аудио дубляжа"
    assert "_have_srt = bool(dub_timed_text)" in fn, (
        "«есть SRT» = «есть распарсенный ТЕКСТ», а не «файл существует»"
    )


def test_qa_prompt_attachment_description_matches_reality():
    fn = _qa_fn_src()
    # один файл (EN) + текст дубляжа
    assert "Приложен ОДИН аудиофайл" in fn
    # только дубляж-аудио + конспект-эталон
    assert "Единственный приложенный аудиофайл — русский ДУБЛЯЖ" in fn
    # текст-vs-текст (конспект + SRT, файлов нет)
    assert "Аудиофайлы НЕ приложены" in fn
    # честные два файла — только в ветке, где оба реально приложены
    assert "Первый файл — ОРИГИНАЛ" in fn


# ── 3. thinking_level в текстовом fallback ───────────────────────

def test_qa_text_fallback_respects_thinking_level_param():
    fn = _qa_fn_src()
    fallback = fn.split("JSON-mime недоступен", 1)[1]
    head = fallback[:900]
    assert "thinking_level=thinking_level" in head
    assert 'thinking_level="high"' not in head, (
        "fallback должен наследовать thinking_level вызова (Quick QA = minimal)"
    )


# ── 4. Quick QA не требует SRT и честно сообщает о пропуске ──────

def test_quick_qa_gate_no_longer_requires_srt():
    src = _read("pipelines/main_pipeline.py")
    block = src.split("ENG Quick QA: лёгкая проверка", 1)[1][:4000]
    assert "if _orig_for_quick:" in block
    assert "_quick_srt and _orig_for_quick" not in block, (
        "гейт «srt AND orig» молча выключал QA на установках без старого vot-cli"
    )


def test_quick_qa_skip_note_reaches_caption():
    src = _read("pipelines/main_pipeline.py")
    assert "_quick_qa_skip_note" in src
    assert 'caption += f"\\n🔍 {_quick_qa_skip_note}"' in src, (
        "если проверка не запускалась, юзер должен видеть это в caption"
    )
    # заметки на все три причины пропуска
    assert "ролик длиннее" in src
    assert "оригинальная дорожка не сохранилась" in src


# ── 5. _find_latest_file не выдаёт оригинал за перевод ───────────

def test_find_latest_file_skips_service_artifacts(tmp_path):
    from services.yandex_live_dub import _find_latest_file

    real = tmp_path / "abc123.live.mp3"
    real.write_bytes(b"x" * 10)
    old = time.time() - 600
    os.utime(real, (old, old))
    # служебные файлы СВЕЖЕЕ настоящего перевода
    for name in ("original_audio.mp3", "original_video.mp3", "clip_qa.mp3",
                 "clip_qa_original.mp3", "pro_dub.mp3"):
        p = tmp_path / name
        p.write_bytes(b"y" * 10)

    found = _find_latest_file(tmp_path, "*.mp3")
    assert found is not None and found.name == "abc123.live.mp3"


def test_find_latest_file_returns_none_when_only_artifacts(tmp_path):
    from services.yandex_live_dub import _find_latest_file
    (tmp_path / "original_audio.mp3").write_bytes(b"y")
    assert _find_latest_file(tmp_path, "*.mp3") is None


# ── 5b. find_pro_tracks выбирает файл С видеопотоком ─────────────

def test_find_pro_tracks_prefers_real_video(tmp_path, monkeypatch):
    import services.livedub_mix as lm

    (tmp_path / "original_video.f140.m4a").write_bytes(b"a" * 200_000)
    (tmp_path / "original_video.mp4").write_bytes(b"v" * 200_000)
    (tmp_path / "translation.mp3").write_bytes(b"r" * 200_000)
    monkeypatch.setattr(lm, "has_video_stream", lambda p: Path(p).suffix == ".mp4")

    orig, ru = lm.find_pro_tracks(tmp_path)
    assert orig is not None and orig.suffix == ".mp4", (
        "audio-only артефакт (f140.m4a) не должен выбираться как оригинал для ремикса"
    )
    assert ru is not None and ru.name == "translation.mp3"


# ── 6. lang доезжает до всех путей перевода ──────────────────────

def test_lang_passthrough_video_and_tts_paths():
    yl = _read("services/yandex_live_dub.py")
    video_fn = yl.split("async def get_live_dub_video", 1)[1]
    assert "lang: str" in video_fn[:400], "get_live_dub_video должен принимать lang"
    assert "duration=duration, lang=lang" in video_fn

    mix = _read("services/livedub_mix.py")
    assert 'voice_style="tts", duration=duration, lang=lang' in mix, (
        "tts-fallback терял язык исходника"
    )

    mp = _read("pipelines/main_pipeline.py")
    dub_block = mp.split("async def _make_dub", 1)[1][:1600]
    assert "lang=source_lang" in dub_block


def test_dead_env_int_removed_from_yandex_live_dub():
    assert "def _env_int" not in _read("services/yandex_live_dub.py")


# ── 7. Живой лог 2026-07-06: обрезание Reflection и дубль автора ─

def test_reflection_budgets_survive_high_thinking():
    """thoughts+output делят ОДИН бюджет: на high thinking съедает 15-20K
    ДО ответа. balanced=26000 обрезал JSON ровно на потолке
    (18971+7013=25984) — все reflection-бюджеты должны держать
    ~20K thinking + полноценный ответ."""
    from core.analysis_profiles import get_expanded_analysis_profile

    fast = get_expanded_analysis_profile(10 * 60, "reflection")
    balanced = get_expanded_analysis_profile(41 * 60, "reflection")
    deep = get_expanded_analysis_profile(70 * 60, "reflection")
    very_long = get_expanded_analysis_profile(150 * 60, "reflection")

    assert balanced.max_tokens >= 36000, "balanced reflection обрезался на 26000"
    assert fast.max_tokens >= 20000
    assert deep.max_tokens >= 44000
    assert very_long.max_tokens >= 52000
    assert fast.max_tokens < balanced.max_tokens < deep.max_tokens < very_long.max_tokens


def test_search_title_does_not_duplicate_author():
    """После отбраковки выдуманного названия real_title = полный YouTube-титул,
    часто уже с автором — второй раз автора не приклеиваем."""
    from services.search import _build_search_title

    ai = {"real_title": "Пол Вошер. Свидетельство. Трус и лжец",
          "real_author": "Пол Вошер", "real_event": ""}
    t = _build_search_title(ai, "fallback")
    assert t.lower().count("пол вошер") == 1, f"дубль автора в запросе: {t!r}"

    # автор, которого в названии нет, по-прежнему добавляется
    ai2 = {"real_title": "Что есть настоящий евангелизм",
           "real_author": "Джон МакАртур", "real_event": ""}
    t2 = _build_search_title(ai2, "fallback")
    assert "МакАртур" in t2 and "евангелизм" in t2.lower()


def test_truncation_visible_in_log():
    src = _read("services/telegraph_pages.py")
    assert "MAX_TOKENS" in src and "ОБРЕЗАН по max_tokens" in src, (
        "обрезание ответа должно логироваться warning'ом, а не тонуть в info"
    )
    assert "finish=%s" in src, "finish_reason должен быть в token-логе"
