"""Regression tests for v3 patch 28 — scored/deduped source packs."""

from core.source_packs import (
    _PACKS,
    get_source_pack,
    get_source_pack_for_ai_data,
    select_source_pack_topics,
)


def test_source_pack_has_new_major_doctrinal_topics():
    for topic in ["christology", "trinity", "pneumatology", "eschatology", "angelology", "isaiah_servant", "scripture_sufficiency", "mission"]:
        assert topic in _PACKS
        assert len(_PACKS[topic]) >= 3


def test_source_pack_scoring_prefers_prayer_over_generic_church():
    topics = select_source_pack_topics(["молитва", "церковь"], "Как нам следует молиться в церкви")
    assert topics[0][0] == "prayer_spirituality"
    assert "ecclesiology" not in [t for t, _ in topics[:1]]


def test_source_pack_scoring_for_isaiah_servant_and_sufficiency():
    isaiah = get_source_pack(["Исаия 53", "заместительное искупление"], "Презренный Раб Иеговы", duration_seconds=3700)
    assert "темы: isaiah_servant" in isaiah
    assert "Commentary on Isaiah" in isaiah
    assert "The Death of Death" in isaiah

    suff = get_source_pack(["достаточность Писания", "Псалом 18"], "Достаточность Писания: Псалом 18", duration_seconds=3400)
    assert "scripture_sufficiency" in suff
    assert "The Sufficiency of Scripture" in suff


def test_source_pack_duration_limits_and_dedupe():
    short = get_source_pack(["Исаия 53"], "Раб Иеговы", duration_seconds=600)
    long = get_source_pack(["Исаия 53"], "Раб Иеговы", duration_seconds=4000)
    short_sources = [ln for ln in short.splitlines() if ln.startswith("- ")]
    long_sources = [ln for ln in long.splitlines() if ln.startswith("- ")]
    assert len(short_sources) == 4
    assert len(long_sources) == 7
    assert len(long_sources) == len(set(long_sources))


def test_source_pack_for_ai_data_reads_duration_and_gives_positive_guidance():
    pack = get_source_pack_for_ai_data({
        "key_categories": ["ангелология", "служение ангелов"],
        "main_topic": "Служение святых ангелов",
        "duration": 3100,
    })
    assert "темы: angelology" in pack
    assert "Не добавляй случайные книги" in pack
    assert "Названия на английском не переводи" in pack


def test_source_packs_do_not_include_disallowed_authors():
    from core.source_packs import _PACKS
    from core.source_titles import is_disallowed_source_author

    offenders = []
    for pack_name, items in _PACKS.items():
        for item in items:
            if is_disallowed_source_author(item):
                offenders.append((pack_name, item))
    assert offenders == []
