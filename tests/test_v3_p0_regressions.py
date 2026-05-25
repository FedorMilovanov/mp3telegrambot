import ast
from pathlib import Path

from core.prompts import build_audio_analysis_prompt, _get_audio_analysis_profile
from services.pdf_generator import _linkify_timecodes


def _load_valid_clip_for_test():
    # Берём только чистую функцию без импорта всего services.shorts_candidates,
    # потому что полный модуль требует Telegram/Flask/dotenv runtime-зависимости.
    tree = ast.parse(Path("services/shorts_candidates.py").read_text(encoding="utf-8"))
    fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_valid_clip"
    )
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {}
    exec(compile(module, "_valid_clip_for_test", "exec"), ns)
    return ns["_valid_clip"]


def test_audio_prompt_does_not_start_with_literal_backslash():
    prompt = build_audio_analysis_prompt(
        title="Тест",
        performer="Канал",
        duration_str="не указана",
        duration_seconds=0,
    )
    assert not prompt.startswith("\\")
    assert prompt.startswith("Ты — ассистент")


def test_unknown_duration_uses_conservative_profile_not_long_profile():
    profile = _get_audio_analysis_profile(0, "deep")
    assert "Длительность неизвестна" in profile["density_note"]
    assert profile["ts_target"] == "7–12"
    assert profile["hints_target"] == "14–28"


def test_valid_clip_rejects_end_after_duration():
    valid_clip = _load_valid_clip_for_test()
    assert valid_clip({"start_seconds": 10, "end_seconds": 20}, 30)
    assert not valid_clip({"start_seconds": 10, "end_seconds": 41}, 30)
    assert not valid_clip({"start_seconds": 20, "end_seconds": 20}, 30)


def test_pdf_linkify_keeps_bible_refs_plain_and_links_real_timecodes():
    html = "<p>Ссылка Ин. 3:16 не таймкод, а мысль начинается в 12:34.</p>"
    out = _linkify_timecodes(html, "https://youtube.com/watch?v=abc")
    assert "Ин. 3:16" in out
    assert 'title="12:34"' in out
    assert "t=754" in out or "start=754" in out
