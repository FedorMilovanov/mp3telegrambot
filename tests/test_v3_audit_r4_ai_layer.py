"""AUDIT R4 (2026-07-05): AI/text-layer regressions.

Covers: git-word scrub eating English Scripture quotes, fake source cards
from English prose, Keller denylist wiping prose fields, Gemini second-round
success flag / timeout rotation / upload cleanup, verse refs counted as
inline anchors, timestamp cap dropping the tail.
"""
from pathlib import Path

from core.source_titles import is_disallowed_source_author, normalize_source_card_line
from core.text_utils import _scrub_inline


def test_git_scrub_keeps_english_scripture_quotes():
    """BUG-R3-01 скраб был безусловным: "Commit your way to the LORD" и
    "a righteous Branch" портились в полях en_raw/quote."""
    assert _scrub_inline("Commit your way to the LORD; trust in him") == \
        "Commit your way to the LORD; trust in him"
    assert "Branch" in _scrub_inline("I will raise up for David a righteous Branch")
    assert "commit" in _scrub_inline("You shall not commit adultery")


def test_git_scrub_still_cleans_russian_context():
    assert "commit" not in _scrub_inline("опубли commit вавшего серию")
    assert "push" not in _scrub_inline("опубликовал push серию статей")
    assert "merge" not in _scrub_inline("merge конфликт")
    # соседние русские слова не склеиваются
    assert "опубликовал серию" in _scrub_inline("опубликовал push серию статей")


def test_english_prose_not_rewritten_into_source_card():
    """_EN_AUTHOR_WITH_TITLE_RE принимал любой Latin-титул: английские цитаты
    превращались в фейковые карточки с **жирным**."""
    r = _scrub_inline("Behold, I stand at the door and knock.")
    assert "**" not in r and "Behold" in r
    r2 = _scrub_inline("Truly, truly, I say to you, whoever believes has eternal life.")
    assert "**" not in r2


def test_known_author_card_still_normalized():
    # AUDIT R34: имя автора канонизируется, а известное название книги теперь
    # подставляется официальным русским переводом (Оуэн из реестра), англ.
    # оригинал уходит в скобки-верификатор.
    r = normalize_source_card_line("• John Owen, The Mortification of Sin")
    assert "Джон Оуэн" in r and "**Об умерщвлении греха в верующих**" in r
    assert "The Mortification of Sin" in r  # оригинал сохранён в скобках


def test_keller_denylist_scoped_to_source_cards():
    # карточки — вырезаются молча
    assert normalize_source_card_line("• Tim Keller, Center Church") == ""
    assert is_disallowed_source_author("Tim Keller")
    assert is_disallowed_source_author("Тимоти Келлером")
    # проза с упоминанием — НЕ обнуляется (нарушало «no silent downgrade»)
    prose = "Разбор недавней дискуссии: Tim Keller и его взгляд на брак подвергается критике, потому что..."
    assert "критике" in _scrub_inline(prose)
    # другой автор Keller — легитимная карточка, не выпиливается
    assert not is_disallowed_source_author("W. Phillip Keller")
    card = normalize_source_card_line(
        "• Добрый пастырь и Его овцы, Филлип Келлер (A Shepherd Looks at Psalm 23, W. Phillip Keller)."
    )
    assert card != ""


def test_gemini_second_round_sets_success_and_cleans_uploads():
    src = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    second_round = src.split("второй круг", 1)[1]
    assert "success = True" in second_round
    assert "_spawn_safe_delete(client, audio_part.name)" in second_round


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
    # структурный блок с anchor_timestamp — тоже якорь
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
