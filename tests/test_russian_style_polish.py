from core.russian_style import polish_public_russian, polish_public_russian_text


def test_spiritual_equipping_calque_is_rewritten_naturally() -> None:
    text, fixes = polish_public_russian("Приветствие и духовное укомплектование мужей")
    assert text == "Приветствие и духовная подготовка мужчин"
    assert len(fixes) == 1
    assert fixes[0].code == "spiritual_equipping_calque"
    assert fixes[0].before == "духовное укомплектование мужей"


def test_inflected_calque_forms_preserve_russian_case() -> None:
    cases = {
        "призыв к духовному укомплектованию мужей":
            "призыв к духовной подготовке мужчин",
        "необходимость духовного укомплектования мужчин":
            "необходимость духовной подготовки мужчин",
        "заниматься духовным укомплектованием мужей":
            "заниматься духовной подготовкой мужчин",
        "говорить о духовном укомплектовании мужчин":
            "говорить о духовной подготовке мужчин",
    }
    for source, expected in cases.items():
        assert polish_public_russian_text(source) == expected


def test_capitalization_is_preserved_at_phrase_start() -> None:
    assert polish_public_russian_text(
        "Духовное укомплектование мужчин начинается со Слова"
    ) == "Духовная подготовка мужчин начинается со Слова"


def test_legitimate_logistics_uses_are_not_touched() -> None:
    for value in (
        "Укомплектование подразделения завершено",
        "Укомплектование штата сотрудниками",
        "Духовная подготовка мужей",
    ):
        assert polish_public_russian_text(value) == value
