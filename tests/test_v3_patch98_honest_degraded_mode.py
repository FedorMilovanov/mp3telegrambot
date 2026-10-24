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
    """Честная деградация подключена, а её текст и выключатель живут в своём модуле.

    Раньше проверка искала строку `DEGRADED_TRANSCRIPT_TIMESTAMPS` прямо в
    пайплайне; после выноса в `pipelines/degraded_mode.py` проверяется, что
    пайплайн действительно использует эту стадию, а сам текст предупреждения
    не потерялся.
    """
    src = __import__("pathlib").Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    stage = __import__("pathlib").Path("pipelines/analysis_stage.py").read_text(encoding="utf-8")
    assert "run_analysis(" in src
    assert "honest degraded mode" not in src
    assert "honest degraded mode" in stage
    assert "MP3 only, no AI pages/questions" in stage
    assert "degraded_caption_data(request.title, request.performer)" in stage
    assert "attach_transcript_timestamps(" in stage
    mode = __import__("pathlib").Path("pipelines/degraded_mode.py").read_text(encoding="utf-8")
    assert "DEGRADED_TRANSCRIPT_TIMESTAMPS" in mode
    assert "Gemini 3.5 сейчас недоступен" in mode
    render = __import__("pathlib").Path("pipelines/telegraph_render.py").read_text(encoding="utf-8")
    assert 'return {"rutube": None, "vk": None}' in render
    assert "expect_synopsis=bool(ai_data)" in src


def _stage() -> str:
    return __import__("pathlib").Path(
        "pipelines/telegraph_repair_stage.py"
    ).read_text(encoding="utf-8")


def _pipeline() -> str:
    return __import__("pathlib").Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")


def test_pipeline_auto_repairs_telegraph_after_publish_by_default():
    """Починка включена по умолчанию и подключена к пайплайну.

    После выноса в `pipelines/telegraph_repair_stage.py` проверяются оба конца:
    сам шаг (флаг, вызов сервиса) и то, что пайплайн его вызывает.
    """
    stage = _stage()
    assert "TELEGRAPH_AUTO_REPAIR_AFTER_PUBLISH" in stage
    assert "repair_telegraph_page_url" in stage
    assert 'os.getenv(REPAIR_ENV, "1")' in stage
    pipeline = _pipeline()
    assert "repair_pages(" in pipeline
    assert "enabled=repair_enabled()" in pipeline


def test_pipeline_persists_auto_repair_status_in_generated_pages_archive():
    stage = _stage()
    assert "def changed_count" in stage
    pipeline = _pipeline()
    assert "aupdate_generated_page_repair_status" in pipeline
    assert "changed_pages=_repair.changed_count" in pipeline
    assert "errors=_repair.errors" in pipeline


def test_pipeline_persists_auto_repair_unresolved_audit_summaries():
    stage = _stage()
    assert "unresolved audit" in stage
    assert "audit_summary" in stage
    assert "for value in (result.error, result.audit_summary)" in stage


def test_pipeline_auto_repair_expands_multipart_telegraph_chains():
    stage = _stage()
    assert "expand_telegraph_page_chain" in stage
    assert "chain = await expand(base) or [base]" in stage
