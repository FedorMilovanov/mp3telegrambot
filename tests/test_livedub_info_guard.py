from pathlib import Path

from services.livedub_info_guard import sampled_srt_to_timed_text, sanitize_card


def _block(index: int, minute: int, text: str) -> str:
    return (
        f"{index}\n00:{minute:02d}:00,000 --> 00:{minute:02d}:05,000\n"
        f"{text}\n"
    )


def test_srt_sampling_covers_beginning_middle_and_end(tmp_path: Path):
    path = tmp_path / "lecture.srt"
    path.write_text(
        "\n".join(_block(i + 1, i, f"Содержательная мысль номер {i}") for i in range(30)),
        encoding="utf-8",
    )
    sampled = sampled_srt_to_timed_text(path, max_chars=900)
    assert "[00:00]" in sampled
    assert any(f"[{minute:02d}:00]" in sampled for minute in range(12, 18))
    assert "[29:00]" in sampled
    assert len(sampled) <= 900


def test_unsupported_scripture_and_generic_claims_are_removed():
    card = {
        "telegram_description": "Современный мир заменяет объективную истину чувствами.",
        "youtube_description": "Нужно искать разум Христов во всех сферах.",
        "compact_subtitles": [
            "Современный мир заменяет объективную истину чувствами.",
            "Эпистемология исследует, как мы знаем то, что знаем.",
        ],
        "hashtags": ["#ABlueprintForThinkingWithР.Ч.Спроул", "#Эпистемология"],
        "scripture_references": [
            {"ref": "3 Царств 16:30", "text_ru": "Ахав делал злое пред очами Господа."}
        ],
        "key_theological_terms": ["Эпистемология"],
    }
    evidence = "[00:00] Эпистемология задаёт вопрос, как мы знаем то, что знаем."
    result = sanitize_card(card, "How Do We Know What We Know? - Р. Ч. Спроул", evidence)
    assert result["scripture_references"] == []
    assert result["compact_subtitles"] == ["Эпистемология исследует, как мы знаем то, что знаем."]
    assert result["telegram_description"] == "How Do We Know What We Know? - Р. Ч. Спроул"
    assert result["youtube_description"] == "How Do We Know What We Know? - Р. Ч. Спроул"
    assert "#ABlueprintForThinkingWithР.Ч.Спроул" not in result["hashtags"]
    assert result["hashtags"][0] == "#РЧСпроул"


def test_reference_requires_explicit_book_and_chapter_verse_evidence():
    card = {
        "hashtags": [],
        "compact_subtitles": [],
        "key_theological_terms": [],
        "scripture_references": [
            {
                "ref": "3 Царств 16:30",
                "text_ru": "Ахав, сын Амврия, делал неугодное пред очами Господа.",
            }
        ],
    }
    evidence = "[18:10] Он обращается к 3 Царств 16:30, но не цитирует весь стих."
    result = sanitize_card(card, "Лекция - Р. Ч. Спроул", evidence)
    assert result["scripture_references"] == [{"ref": "3 Царств 16:30", "text_ru": ""}]


def test_no_subtitles_means_no_generated_terms_bullets_or_scripture():
    card = {
        "compact_subtitles": ["Правдоподобный тезис"],
        "key_theological_terms": ["Завет"],
        "scripture_references": [{"ref": "Иоанна 3:16", "text_ru": "текст"}],
        "hashtags": ["#Теология"],
    }
    result = sanitize_card(card, "Название - Р. Ч. Спроул", "")
    assert result["compact_subtitles"] == []
    assert result["key_theological_terms"] == []
    assert result["scripture_references"] == []
