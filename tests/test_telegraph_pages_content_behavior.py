"""Executed content assembly and robust JSON parsing for Telegraph pages."""

import asyncio
import json

from services import telegraph_pages as pages


def _run(callback, *args, **kwargs):
    return asyncio.run(callback(*args, **kwargs))


def test_analytics_skips_empty_input_and_builds_all_sections(monkeypatch):
    assert _run(pages.create_telegraph_analytics, {}, "Title", "Author") is None
    calls = []

    async def post(title, author, nodes, _loop):
        calls.append((title, author, nodes))
        return "https://telegra.ph/analytics"

    monkeypatch.setattr(pages, "_telegraph_post", post)
    ai_data = {
        "analysis_summary": "Summary with **bold**",
        "argument_arc": "Arc",
        "key_categories": ["Term — explanation", "", "Plain category"],
    }
    result = _run(pages.create_telegraph_analytics, ai_data, "Title", "Author")
    assert result == "https://telegra.ph/analytics"
    title, author, nodes = calls[0]
    assert title == "Аналитика: Title"
    assert author == "Author"
    assert [node["tag"] for node in nodes].count("hr") == 2
    assert {"tag": "b", "children": ["Term"]} in nodes[-2]["children"]
    assert nodes[-1]["children"] == ["Plain category."]


def test_question_normalization_filters_deduplicates_and_caps(monkeypatch):
    assert pages._normalize_question_items(None) == []
    monkeypatch.setattr(
        pages,
        "normalize_question_text",
        lambda text: text.lstrip("🟢🔵 ").strip(),
    )
    monkeypatch.setattr(pages, "question_is_usable", lambda text: text.rstrip(".") != "weak")
    monkeypatch.setattr(pages, "question_key", lambda text: text.casefold().rstrip("."))
    questions = ["", "weak", "🔵 Same?", "same?", "Next?", "Ignored?"]
    assert pages._normalize_question_items(questions, max_items=2) == [
        "🔵 Same?",
        "🟢 Next?",
    ]


def test_questions_skip_empty_and_publish_green_blue_groups(monkeypatch):
    assert _run(pages.create_telegraph_questions, [], "Title", "Author") is None
    monkeypatch.setattr(
        pages,
        "_normalize_question_items",
        lambda _questions, max_items: [
            "🟢 First\nline?",
            "🟢 ",
            "🔵 Deep?",
            "🔵 ",
        ],
    )
    calls = []

    async def create(title, author, nodes, _loop):
        calls.append((title, author, nodes))
        return "https://telegra.ph/questions", None

    monkeypatch.setattr(pages, "_create_telegraph_page_single", create)
    result = _run(pages.create_telegraph_questions, ["input"], "Title", "Author")
    assert result == "https://telegra.ph/questions"
    title, _author, nodes = calls[0]
    assert title == "Вопросы: Title"
    assert [node["tag"] for node in nodes] == ["h3", "p", "hr", "h3", "p"]
    assert nodes[1]["children"] == ["1. First line?"]
    assert nodes[-1]["children"] == ["1. Deep?"]


def test_questions_guard_malformed_groups_and_create_failure(monkeypatch):
    monkeypatch.setattr(pages, "_normalize_question_items", lambda *_a, **_k: ["unmarked"])
    assert _run(pages.create_telegraph_questions, ["input"], "T", "A") is None

    monkeypatch.setattr(pages, "_normalize_question_items", lambda *_a, **_k: ["🟢 Question?"])

    async def fail(*_args):
        return None, "API_ERROR"

    monkeypatch.setattr(pages, "_create_telegraph_page_single", fail)
    assert _run(pages.create_telegraph_questions, ["input"], "T", "A") is None


def _capture_terms_post(monkeypatch):
    calls = []

    async def post(title, author, nodes, _loop):
        calls.append((title, author, nodes))
        return "https://telegra.ph/terms"

    monkeypatch.setattr(pages, "_telegraph_post", post)
    return calls


def test_terms_skip_empty_and_render_every_supported_section(monkeypatch):
    assert _run(pages.create_telegraph_terms, {}, "Title", "Author") is None
    calls = _capture_terms_post(monkeypatch)
    terms = {
        "concepts": [
            "",
            "Term||Explanation||Почему важно: Why||1:00, 5, xx:yy, 2:00, 3:00, 4:00",
            "Only term",
        ],
        "scripture": [
            "",
            "John 3:16||Use||Ключевая фраза: Phrase||1:00",
            "Romans 8:28||||«Already quoted»",
        ],
        "translations": [
            "",
            "John||word||ru one; ru two||en one; en two||Наблюдение: Note||1:00 2:00",
            "Reference only",
        ],
        "lexicon_notes": [
            "",
            "John 3:16||love||אהבה||HALOT and BDAG||Note||Почему важно: Why||1:00||Context",
            "||||word",
            "Reference only",
        ],
    }
    result = _run(
        pages.create_telegraph_terms,
        terms,
        "Title",
        "Author",
        "https://youtu.be/video",
    )
    assert result == "https://telegra.ph/terms"
    title, author, nodes = calls[0]
    assert title == "Термины: Title"
    assert author == "Author"
    encoded = json.dumps(nodes, ensure_ascii=False)
    for heading in ("🧩 Понятия", "📖 Писание", "🌍 Переводы", "🔤 Словарные заметки"):
        assert heading in encoded
    assert "HALOT (евр. словарь ВЗ) and BDAG (греч. словарь НЗ)" in encoded
    assert "https://youtu.be/video?t=60" in encoded
    assert "Phrase" in encoded
    assert "Already quoted" in encoded
    assert "Context" in encoded
    assert nodes[-1]["tag"] == "p"


def test_terms_render_plain_timestamps_without_youtube_url(monkeypatch):
    calls = _capture_terms_post(monkeypatch)
    result = _run(
        pages.create_telegraph_terms,
        {"concepts": ["Term||||||1:00,2:00"]},
        "Title",
        "Author",
    )
    assert result == "https://telegra.ph/terms"
    nodes = calls[0][2]
    assert nodes[-1]["children"] == ["1:00", ", ", "2:00"]


def test_parse_expanded_json_removes_fence_and_bidi_marks():
    raw = "\u2066```json\n" + json.dumps({"outline": [{"title": "A"}], "sections": [{"title": "A"}]}) + "\n```"
    assert pages._parse_expanded_json(raw) == (
        [{"title": "A"}],
        [{"title": "A"}],
    )


def test_parse_expanded_json_extracts_wrapped_object():
    raw = "prefix " + json.dumps({"outline": [], "sections": [{"title": "A"}]}) + " suffix"
    assert pages._parse_expanded_json(raw) == ([], [{"title": "A"}])


def test_parse_expanded_json_escapes_raw_control_characters():
    section = {"title": "A", "content": 'line1\nline2\rline3\tC:\\path "quote"'}
    payload = json.dumps({"outline": [], "sections": [section]}, ensure_ascii=False)
    raw = payload.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
    parsed = pages._parse_expanded_json(raw)
    assert parsed == ([], [section])


def test_parse_expanded_json_recovers_last_complete_section():
    complete = json.dumps({"title": "A", "time": "1:00", "content": 'C:\\path "quote"'})
    raw = '{"sections":[' + complete + ',{"title":"truncated"'
    outline, sections = pages._parse_expanded_json(raw)
    assert sections == [json.loads(complete)]
    assert outline == [{"title": "A", "time": "1:00"}]


def test_parse_expanded_json_enforces_depth_and_iteration_limits():
    raw = '{"sections":[{"title":"A"},{"title":"B"'
    assert pages._parse_expanded_json(raw, max_depth=1) is None
    assert pages._parse_expanded_json(raw, max_iterations=1) is None
    assert pages._parse_expanded_json("not json") is None


def test_parse_expanded_json_rejects_unrecoverable_fixed_payload():
    complete = json.dumps({"title": "A"})
    raw = '{bad "sections":[' + complete + ',{"title":"B"'
    assert pages._parse_expanded_json(raw) is None
