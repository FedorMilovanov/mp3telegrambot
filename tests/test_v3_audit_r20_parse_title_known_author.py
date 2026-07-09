"""AUDIT R20 (лог 2026-07-09/10, реальные баги на проде):

    Done: [How to Pray] Prayer with Р. Ч. Спроул (21.7 MB, 128kbps)
    Done: [Questions and Answers] **MacArthur, Mohler, and Sproul**,
          Стивен Лоусон (Steven Lawson). (41.3 MB, 128kbps)

Оба случая — один и тот же корневой баг: parse_title() (core/utils.py)
решал, какая половина заголовка — исполнитель, а какая — название,
ПОДСЧЁТОМ СЛОВ, а не распознаванием имени. Эвристика ломалась, когда:
  1) более длинная сторона — описание, лишь УПОМИНАЮЩЕЕ автора
     («Prayer with R.C. Sproul» — 4 слова, длиннее «How to Pray» — 3 слова,
     эвристика решила, что более длинная сторона это «title», хотя внутри
     неё просто спрятано имя известного автора);
  2) более короткая сторона — общее название формата, а не имя
     («Questions and Answers» — 3 слова, короче «MacArthur, Mohler, and
     Sproul» — 4 слова, эвристика решила, что короткая сторона это автор).

В обоих случаях получившийся «title» затем прогонялся через
normalize_title_text() -> normalize_person_names(), который подменял
спрятанное внутри имя автора на кириллицу ПРЯМО ВНУТРИ названия —
итоговый лог показывал заведомо перепутанные и захламлённые title/author.

Исправление: parse_title() теперь СНАЧАЛА проверяет, упоминает ли
известного автора одна из половин (core.person_names.known_author_from_text
— тот же реестр и сигнал, что уже использовался только для LiveDub-подписи
в _split_title_author_line), и лишь при отсутствии однозначного сигнала
падает обратно на подсчёт слов. Реестр вынесен в core/person_names.py,
чтобы оба потребителя (parse_title и LiveDub caption) использовали ОДНУ
и ту же логику, а не рассинхронизированные копии.
"""
from core.person_names import (
    KNOWN_AUTHOR_RU,
    KNOWN_CHANNEL_AUTHOR_RU,
    known_author_from_text,
    known_ru_author_from_text,
    looks_like_author_list,
)
from core.utils import parse_title


def test_parse_title_fixes_description_that_merely_mentions_author():
    """Живой баг: длинная сторона содержит имя автора внутри фразы —
    раньше эвристика по числу слов принимала её за title."""
    performer, title = parse_title("How to Pray: Prayer with R.C. Sproul", "Ligonier Ministries")
    assert performer == "Р. Ч. Спроул"
    assert title == "How to Pray"


def test_parse_title_fixes_generic_session_label_mistaken_for_title():
    """Живой баг: короткая сторона — общее название формата («Questions and
    Answers»), а не имя, но длинная сторона (список фамилий) была короче
    по словам и потому раньше ошибочно становилась title."""
    performer, title = parse_title(
        "MacArthur, Mohler, and Sproul: Questions and Answers", "Ligonier Ministries"
    )
    assert performer == "Джон МакАртур"
    assert title == "Questions and Answers"
    # Критично: в исправленном title не должно остаться ни markdown-звёздочек,
    # ни второго автора, случайно приклеенного через normalize_person_names.
    assert "*" not in title
    assert "Лоусон" not in title and "Lawson" not in title


def test_parse_title_no_word_count_swap_when_author_known():
    """Регрессия базового кейса из существующей практики: явный author-first
    формат «Автор - Название» продолжает работать как раньше."""
    performer, title = parse_title("John MacArthur - The Gospel According to Jesus", "Grace to You")
    assert performer == "Джон МакАртур"
    assert title == "The Gospel According to Jesus"


def test_parse_title_falls_back_to_word_count_when_no_known_author():
    """Когда ни одна сторона не содержит распознанного имени — поведение
    не меняется, работает старый подсчёт слов (пример из реального прогона
    сессии, автор ещё не в реестре под этим именем)."""
    performer, title = parse_title(
        "Разве Константин придумал Троицу? - Натан Бузениц", "Fedor Milovanov"
    )
    assert performer == "Натан Бузениц"
    assert title == "Разве Константин придумал Троицу?"


def test_known_author_from_text_finds_surname_embedded_in_longer_phrase():
    assert known_author_from_text("Prayer with R.C. Sproul") == "Р. Ч. Спроул"
    assert known_author_from_text("**MacArthur, Mohler, and Sproul**") == "Джон МакАртур"
    assert known_author_from_text("Questions and Answers") == ""


def test_known_registries_shared_between_parse_title_and_livedub_caption():
    """Реестр должен жить в одном месте (core/person_names.py) — main_pipeline
    импортирует его, а не держит вторую независимую копию."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("pipelines/main_pipeline.py").read_text(
        encoding="utf-8"
    )
    assert "from core.person_names import" in src
    assert "KNOWN_AUTHOR_RU as _KNOWN_AUTHOR_RU" in src
    # старая полная копия словаря (уникальный маркер записи) не должна остаться
    assert src.count('"MacArthur": "Джон МакАртур"') == 0


def test_registries_non_empty_and_ru_lookup_works():
    assert KNOWN_AUTHOR_RU.get("MacArthur") == "Джон МакАртур"
    assert KNOWN_CHANNEL_AUTHOR_RU.get("ligonier") == "Р. Ч. Спроул"
    assert known_ru_author_from_text("проповедь: Джон МакАртур, часть 1") == "Джон МакАртур"


def test_looks_like_author_list_still_reexported():
    """Проверяем только реэкспорт/доступность — сама эвристика не менялась,
    у неё есть свои слабые места (например, «Questions and Answers» тоже
    считает похожим на список имён из-за двух заглавных слов), поэтому для
    parse_title() используется более точный known_author_from_text(), а не
    эта функция."""
    assert looks_like_author_list("John MacArthur") is True
    assert looks_like_author_list("") is False
