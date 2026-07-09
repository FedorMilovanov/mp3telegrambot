"""AUDIT R15 (2026-07-09, скриншот оператора): дубль секции в оглавлении.

Живой баг: Synopsis part [2/2] показал в TOC «Решительный выбор в
библиотеке — 20:00» ДВАЖДЫ подряд. Лог того же прогона: outline_rebuilt=True,
sections=11, и у section[6] (time=20:00) обнаружились inline-таймкоды
16:15-19:45 — все ДО начала собственной секции (contentом из чужого раздела).
Density-retry (schema отключена для verbatim-режима) вернул повторяющуюся
секцию. Нигде в пайплайне не было проверки на дубли — audit_expanded_sections
теперь убирает подряд идущие секции с одинаковым title+time.
"""
from core.content_audit import ContentAuditIssue, audit_expanded_sections


def test_consecutive_duplicate_section_removed():
    sections = [
        {"title": "Посланник в ночи", "time": "12:20", "content": "А" * 200},
        {"title": "Решительный выбор в библиотеке", "time": "20:00", "content": "Б" * 100},
        {"title": "Решительный выбор в библиотеке", "time": "20:00", "content": "В" * 400},
        {"title": "Рождение свыше", "time": "24:50", "content": "Г" * 200},
    ]
    new_sections, new_outline, issues = audit_expanded_sections(sections, [], label="Test")
    titles = [s["title"] for s in new_sections]
    assert titles.count("Решительный выбор в библиотеке") == 1, f"дубль не убран: {titles}"
    assert len(new_sections) == 3
    # оставлена более содержательная версия (400 символов, не 100)
    kept = next(s for s in new_sections if s["title"] == "Решительный выбор в библиотеке")
    assert len(kept["content"]) == 400
    assert any(i.code == "duplicate_section_removed" for i in issues)


def test_non_duplicate_sections_with_same_empty_time_survive():
    """QA-режим: несколько application-секций легитимно делят time='' —
    дедуп не должен их схлопывать, раз title у них разный."""
    sections = [
        {"title": "Малая группа", "time": "", "content": "А" * 200},
        {"title": "Семья", "time": "", "content": "Б" * 200},
        {"title": "Самоиспытание", "time": "", "content": "В" * 200},
        {"title": "Молитва", "time": "", "content": "Г" * 200},
    ]
    new_sections, _, issues = audit_expanded_sections(sections, [], label="Test")
    assert len(new_sections) == 4
    assert not any(i.code == "duplicate_section_removed" for i in issues)


def test_non_consecutive_duplicates_not_merged():
    """Дедуп ловит только ПОДРЯД идущие дубли — не трогает случайные
    совпадения title в разных концах material (риск ложного схлопывания)."""
    sections = [
        {"title": "Молитва", "time": "5:00", "content": "А" * 200},
        {"title": "Другое", "time": "10:00", "content": "Б" * 200},
        {"title": "Молитва", "time": "40:00", "content": "В" * 200},
    ]
    new_sections, _, issues = audit_expanded_sections(sections, [], label="Test")
    assert len(new_sections) == 3
    assert not any(i.code == "duplicate_section_removed" for i in issues)


def test_dedup_keeps_richer_when_first_is_better():
    sections = [
        {"title": "Тема", "time": "1:00", "content": "Х" * 500},
        {"title": "Тема", "time": "1:00", "content": "Х" * 50},
    ]
    new_sections, _, _ = audit_expanded_sections(sections, [], label="Test")
    assert len(new_sections) == 1
    assert len(new_sections[0]["content"]) == 500


def test_content_audit_issue_dataclass_accepts_dedup_code():
    issue = ContentAuditIssue(code="duplicate_section_removed", location="x", message="y")
    assert issue.code == "duplicate_section_removed"


# ── R15b (скриншот 2026-07-09): мёртвые таймкоды в mini-outline части 2+ ──

def test_mini_outline_builds_real_link_not_bare_string():
    """Part [2/2] TOC вставлял таймкод голой строкой без href — курсор не
    менялся, ссылка была мертва. Теперь строится настоящая <a>-нода, как
    и в _build_toc_nodes_v2 для части 1."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("services/telegraph.py").read_text(encoding="utf-8")
    block = src.split("elif total > 1:", 1)[1].split("for sec_idx, sec in enumerate(part_secs):", 1)[0]
    assert '"tag": "a"' in block, "mini-outline части 2+ не строит ссылку — регрессия BUG-5/R15"
    assert "get_youtube_timestamp_url(url, _secs)" in block
    assert "time_to_seconds(_ps_time)" in block


def test_telegraph_imports_link_building_utils():
    from pathlib import Path
    header = Path(__file__).resolve().parents[1].joinpath("services/telegraph.py").read_text(
        encoding="utf-8"
    ).split("\n\n", 1)[0]
    assert "time_to_seconds" in header
    assert "from core.url_utils import get_youtube_timestamp_url" in header


def test_mini_outline_logic_produces_link_children():
    """Модульная проверка самой логики построения ссылки (без запуска
    полного async pipeline публикации)."""
    from core.core_utils import time_to_seconds
    from core.url_utils import get_youtube_timestamp_url

    url = "https://www.youtube.com/watch?v=NKehpxxEWsY"
    part_secs = [
        {"title": "Решительный выбор в библиотеке", "time": "20:00"},
    ]
    _mini_outline = []
    for _ps in part_secs:
        _ps_title = _ps.get("title", "")
        _ps_time = (_ps.get("time") or "").strip()
        _children = [{"tag": "b", "children": [f"• {_ps_title}"]}]
        if _ps_time:
            _secs = time_to_seconds(_ps_time)
            if _secs is not None and url:
                _children.append(" — ⏱ ")
                _children.append({
                    "tag": "a",
                    "attrs": {"href": get_youtube_timestamp_url(url, _secs)},
                    "children": [_ps_time],
                })
        _mini_outline.append({"tag": "p", "children": _children})

    link_node = _mini_outline[0]["children"][-1]
    assert link_node["tag"] == "a"
    assert "t=1200" in link_node["attrs"]["href"]  # 20:00 = 1200s
    assert link_node["children"] == ["20:00"]
