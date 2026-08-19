"""AUDIT R4 (2026-07-05): AI/text-layer regressions.

Covers: git-word scrub eating English Scripture quotes, fake source cards
from English prose, Keller denylist wiping prose fields, bounded Gemini retry /
upload reuse, verse refs counted as inline anchors, timestamp cap dropping tail.
"""
from pathlib import Path

from core.source_titles import is_disallowed_source_author, normalize_source_card_line
from core.text_utils import _scrub_inline


def test_git_scrub_keeps_english_scripture_quotes():
    assert _scrub_inline("Commit your way to the LORD; trust in him") == \
        "Commit your way to the LORD; trust in him"
    assert "Branch" in _scrub_inline("I will raise up for David a righteous Branch")
    assert "commit" in _scrub_inline("You shall not commit adultery")


def test_git_scrub_still_cleans_russian_context():
    assert "commit" not in _scrub_inline("опубли commit вавшего серию")
    assert "push" not in _scrub_inline("опубликовал push серию статей")
    assert "merge" not in _scrub_inline("merge конфликт")
    assert "опубликовал серию" in _scrub_inline("опубликовал push серию статей")


def test_english_prose_not_rewritten_into_source_card():
    r = _scrub_inline("Behold, I stand at the door and knock.")
    assert "**" not in r and "Behold" in r
    r2 = _scrub_inline("Truly, truly, I say to you, whoever believes has eternal life.")
    assert "**" not in r2


def test_known_author_card_still_normalized():
    r = normalize_source_card_line("• John Owen, The Mortification of Sin")
    assert "Джон Оуэн" in r and "**Об умерщвлении греха в верующих**" in r
    assert "The Mortification of Sin" in r


def test_keller_denylist_scoped_to_source_cards():
    assert normalize_source_card_line("• Tim Keller, Center Church") == ""
    assert is_disallowed_source_author("Tim Keller")
    assert is_disallowed_source_author("Тимоти Келлером")
    prose = "Разбор недавней дискуссии: Tim Keller и его взгляд на брак подвергается критике, потому что..."
    assert "критике" in _scrub_inline(prose)
    assert not is_disallowed_source_author("W. Phillip Keller")
    card = normalize_source_card_line(
        "• Добрый пастырь и Его овцы, Филлип Келлер (A Shepherd Looks at Psalm 23, W. Phillip Keller)."
    )
    assert card != ""


def test_gemini_reuses_upload_and_has_no_second_full_retry_circle():
    src = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    assert "if audio_part is None:" in src
    assert "на уже загруженном аудио" in src
    assert "second full re-upload circle is disabled" in src
    assert "Gemini: второй круг успешен!" not in src
    assert "await asyncio.sleep(60)" not in src


def test_gemini_timeout_rotates_to_next_key():
    src = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    assert "_is_timeout" in src
    assert "if _is_timeout:" in src


def test_verse_refs_not_counted_as_inline_anchors():
    from core.synopsis_quality import _section_inline_timestamp_count

    section = {"title": "S", "time": "0:00",
               "content": "Ин 3:16 говорит о любви. Рим 8:28 напоминает о промысле." * 3}
    assert _section_inline_timestamp_count(section) == 0
    real = {"title": "S", "time": "0:00",
            "content": "Когда жизнь рухнула, всё изменилось ⏱ **7:30**. И дальше ⏱ **12:45**."}
    assert _section_inline_timestamp_count(real) == 2
    blk = {"title": "S", "time": "0:00", "content": "",
           "blocks": [{"type": "pull_quote", "quote": "…", "anchor_timestamp": "5:10"}]}
    assert _section_inline_timestamp_count(blk) == 1


def test_timestamp_cap_keeps_tail():
    from core.json_parser import _parse_gemini_response

    ts = [{"time": f"{i}:00", "topic": f"Тема номер {i} содержательная"} for i in range(60)]
    data = "{" + '"real_author": "A", "real_title": "T", "timestamps": ' + str(ts).replace("'", '"') + "}"
    parsed = _parse_gemini_response(data, duration=4000)
    lines = parsed["timestamps"].splitlines()
    assert any(ln.startswith("59:00") for ln in lines), "последний таймкод обязан выжить"
    assert len(lines) == 50
