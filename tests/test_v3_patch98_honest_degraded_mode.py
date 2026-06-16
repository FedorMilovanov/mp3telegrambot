from converters.caption import build_caption
from services.youtube_transcript import timed_text_to_caption_timestamps


def test_transcript_to_caption_timestamps_is_grounded_and_limited():
    timed = "\n".join(
        f"[{i}:00] Это длинная строка субтитров номер {i}, которая должна быть укорочена для подписи."
        for i in range(10)
    )
    out = timed_text_to_caption_timestamps(timed, max_lines=3, max_topic_chars=45)
    lines = out.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("0:00 ")
    assert lines[-1].startswith("2:00 ")
    assert "…" in lines[0]


def test_degraded_caption_announces_no_ai_pages_and_transcript_timestamps():
    cap = build_caption(
        "Автор",
        "Название",
        600,
        12.3,
        {
            "real_title": "Название",
            "real_author": "Автор",
            "format": "other",
            "_ai_unavailable_warning": "Gemini 3.5 недоступен. Отправляю MP3 без AI-конспектов и вопросов.",
            "_timestamps_source": "youtube_transcript",
            "timestamps": "0:00 Вступление\n1:00 Второй фрагмент",
        },
        url="https://www.youtube.com/watch?v=abc",
    )
    assert "Gemini 3.5 недоступен" in cap
    assert "без AI-конспектов" in cap
    assert "Таймкоды взяты из субтитров YouTube" in cap
    assert "0:00" in cap
    assert "Читать Подробный Разбор" not in cap


def test_pipeline_honest_degraded_mode_is_wired():
    src = __import__("pathlib").Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "honest degraded mode: MP3 only, no AI pages/questions" in src
    assert "DEGRADED_TRANSCRIPT_TIMESTAMPS" in src
    assert "if not ai_data:\n                return {\"rutube\": None, \"vk\": None}" in src
    assert "expect_synopsis=bool(ai_data)" in src


def test_pipeline_auto_repairs_telegraph_after_publish_by_default():
    src = __import__("pathlib").Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "TELEGRAPH_AUTO_REPAIR_AFTER_PUBLISH" in src
    assert "repair_telegraph_page_url" in src
    assert "Auto Telegraph repair after publish" in src


def test_pipeline_persists_auto_repair_status_in_generated_pages_archive():
    src = __import__("pathlib").Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "aupdate_generated_page_repair_status" in src
    assert "_auto_repair_results" in src
    assert "changed_pages=sum(1 for _r in _auto_repair_results" in src
