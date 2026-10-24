#!/usr/bin/env python3
"""Навигация Telegraph: сборка блока, editPage, обход цепочки, повторы.

HTTP заменён на `PageIO`: проверяются правила, которые раньше держались только
на комментариях внутри 170-строчного замыкания.
"""
from __future__ import annotations

import logging

import pytest

from pipelines import telegraph_nav as nav
from pipelines.telegraph_nav import (
    NAV_HEADER,
    PageContent,
    PageIO,
    add_navigation,
    compact_nav_nodes,
    find_next_urls,
    has_nav,
    nav_children,
    nav_nodes,
    normalize_url,
    strip_trailing_nav,
)

SYNOPSIS = "https://telegra.ph/konspekt-1"
STUDY = "https://telegra.ph/razbor-1"
PART2 = "https://telegra.ph/konspekt-2"
PAGES = [("Конспект", SYNOPSIS), ("Разбор материала", STUDY)]


class FakeTelegraph:
    """Fake getPage/editPage с записью операций."""

    def __init__(self, pages: dict[str, list], title: str = "Заголовок",
                 author: str = "Автор", edit_ok=True, fetch_error=None,
                 edit_error=None):
        self.pages = pages
        self.title = title
        self.author = author
        self.edit_ok = edit_ok
        self.fetch_error = fetch_error or {}
        self.edit_error = edit_error or {}
        self.edits: list[tuple[str, list]] = []
        self.fetches: list[str] = []

    async def fetch(self, url: str) -> PageContent | None:
        self.fetches.append(url)
        if url in self.fetch_error:
            raise self.fetch_error[url]
        if url not in self.pages:
            return None
        return PageContent(url=url, title=self.title, author=self.author,
                           nodes=list(self.pages[url]))

    async def edit(self, url: str, title: str, author: str, nodes: list) -> bool:
        if url in self.edit_error:
            raise self.edit_error[url]
        ok = self.edit_ok(url, nodes) if callable(self.edit_ok) else bool(self.edit_ok)
        if ok:
            self.edits.append((url, nodes))
            self.pages[url] = list(nodes)
        return ok

    def io(self) -> PageIO:
        return PageIO(fetch=self.fetch, edit=self.edit)


# ── Чистые функции ───────────────────────────────────────────────────────


def test_nav_children_skip_self_and_separate_with_dot() -> None:
    children = nav_children(SYNOPSIS, PAGES)
    assert children == [
        {"tag": "a", "attrs": {"href": STUDY}, "children": ["Разбор материала"]},
    ]

    both = nav_children("https://telegra.ph/other", PAGES)
    assert both[1] == " · "
    assert [c["children"][0] for c in both if isinstance(c, dict)] == [
        "Конспект", "Разбор материала"
    ]


def test_nav_children_empty_when_only_self() -> None:
    assert nav_children(SYNOPSIS, [("Конспект", SYNOPSIS)]) == []
    assert nav_nodes(SYNOPSIS, [("Конспект", SYNOPSIS)]) == []


def test_nav_nodes_keep_space_outside_bold() -> None:
    """Пробел вне <b>: внутри жирного узла рендер его срезает."""
    nodes = nav_nodes(SYNOPSIS, PAGES)
    assert nodes[0] == {"tag": "hr"}
    paragraph = nodes[1]
    assert paragraph["children"][0] == {"tag": "b", "children": [NAV_HEADER]}
    assert paragraph["children"][1] == " "


def test_compact_nav_has_no_rule_and_no_header() -> None:
    nodes = compact_nav_nodes(SYNOPSIS, PAGES)
    assert len(nodes) == 1
    assert nodes[0]["tag"] == "p"
    assert nodes[0]["children"][0] == "📂 "
    assert "Читать также" not in str(nodes[0])


def test_strip_trailing_nav_removes_previous_block() -> None:
    body = [{"tag": "p", "children": ["текст"]}]
    nodes = body + nav_nodes(SYNOPSIS, PAGES)
    assert strip_trailing_nav(nodes) == body
    # без блока — не трогаем
    assert strip_trailing_nav(body) == body
    assert strip_trailing_nav([]) == []


def test_strip_trailing_nav_ignores_unrelated_last_paragraph() -> None:
    nodes = [{"tag": "p", "children": [{"tag": "b", "children": ["Итог"]}]}]
    assert strip_trailing_nav(nodes) == nodes


def test_has_nav_checks_only_tail() -> None:
    nodes = [{"tag": "p", "children": [NAV_HEADER]}] + [
        {"tag": "p", "children": ["x"]} for _ in range(5)
    ]
    assert has_nav(nodes) is False
    assert has_nav([{"tag": "p", "children": [NAV_HEADER]}]) is True


def test_normalize_url_expands_relative() -> None:
    assert normalize_url("/Part-2") == "https://telegra.ph/Part-2"
    assert normalize_url("https://telegra.ph/Part-2") == "https://telegra.ph/Part-2"


def test_find_next_urls_finds_nested_marker_links() -> None:
    nodes = [
        {"tag": "p", "children": [
            {"tag": "a", "attrs": {"href": "/Part-2"}, "children": ["➡ Дальше"]},
        ]},
        {"tag": "p", "children": [
            {"tag": "b", "children": [
                {"tag": "a", "attrs": {"href": "/Part-3"}, "children": ["➡"]},
            ]},
        ]},
        {"tag": "p", "children": [
            {"tag": "a", "attrs": {"href": "/Other"}, "children": ["обычная ссылка"]},
        ]},
        "строка",
    ]
    assert find_next_urls(nodes) == [
        "https://telegra.ph/Part-2",
        "https://telegra.ph/Part-3",
    ]


# ── editPage ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_navigation_added_to_each_page() -> None:
    fake = FakeTelegraph({SYNOPSIS: [{"tag": "p", "children": ["текст"]}],
                          STUDY: [{"tag": "p", "children": ["разбор"]}]})
    count = await add_navigation(PAGES, fake.io())

    assert count == 2
    assert {url for url, _ in fake.edits} == {SYNOPSIS, STUDY}
    synopsis_nodes = dict(fake.edits)[SYNOPSIS]
    assert synopsis_nodes[-1]["children"][0]["children"] == [NAV_HEADER]
    assert STUDY in str(synopsis_nodes[-1])


@pytest.mark.asyncio
async def test_single_page_gets_no_navigation() -> None:
    fake = FakeTelegraph({SYNOPSIS: []})
    assert await add_navigation([("Конспект", SYNOPSIS)], fake.io()) == 0
    assert fake.edits == []


@pytest.mark.asyncio
async def test_repeated_run_does_not_duplicate_block() -> None:
    fake = FakeTelegraph({SYNOPSIS: [{"tag": "p", "children": ["текст"]}],
                          STUDY: [{"tag": "p", "children": ["разбор"]}]})
    await add_navigation(PAGES, fake.io())
    first = list(fake.pages[SYNOPSIS])
    await add_navigation(PAGES, fake.io())
    second = fake.pages[SYNOPSIS]

    assert sum(1 for node in second if NAV_HEADER in str(node)) == 1
    assert first == second


@pytest.mark.asyncio
async def test_size_limit_falls_back_to_compact_block() -> None:
    """editPage отверг полный блок — ставим компактный, страница остаётся живой."""
    fake = FakeTelegraph(
        {SYNOPSIS: [{"tag": "p", "children": ["текст"]}], STUDY: [{"tag": "p", "children": ["x"]}]},
        edit_ok=lambda url, nodes: "Читать также" not in str(nodes),
    )
    await add_navigation(PAGES, fake.io())

    synopsis_nodes = dict(fake.edits)[SYNOPSIS]
    assert "Читать также" not in str(synopsis_nodes)
    assert "📂" in str(synopsis_nodes[-1])


@pytest.mark.asyncio
async def test_missing_page_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeTelegraph({STUDY: [{"tag": "p", "children": ["x"]}]})
    with caplog.at_level(logging.INFO):
        await add_navigation(PAGES, fake.io())

    assert {url for url, _ in fake.edits} == {STUDY}


@pytest.mark.asyncio
async def test_fetch_failure_is_retried_then_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(nav, "NAV_RETRY_DELAY", 0)
    fake = FakeTelegraph(
        {SYNOPSIS: [{"tag": "p", "children": ["x"]}], STUDY: []},
        fetch_error={SYNOPSIS: RuntimeError("сеть легла")},
    )
    with caplog.at_level(logging.WARNING):
        await add_navigation(PAGES, fake.io())

    assert fake.fetches.count(SYNOPSIS) == 2
    assert "попытка 1/2" in caplog.text
    assert "не удалось добавить навигацию" in caplog.text


@pytest.mark.asyncio
async def test_transient_failure_recovers_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(nav, "NAV_RETRY_DELAY", 0)
    attempts = {"n": 0}

    class Transient(FakeTelegraph):
        async def fetch(self, url: str):
            if url == SYNOPSIS:
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise RuntimeError("таймаут")
            return await super().fetch(url)

    fake = Transient({SYNOPSIS: [], STUDY: []})
    await add_navigation(PAGES, fake.io())

    assert attempts["n"] == 2
    assert SYNOPSIS in {url for url, _ in fake.edits}


# ── Цепочка «➡ Дальше» ───────────────────────────────────────────────────


def next_part_nodes(href: str) -> list:
    return [{"tag": "p", "children": [
        {"tag": "a", "attrs": {"href": href}, "children": ["➡ Дальше"]},
    ]}]


@pytest.mark.asyncio
async def test_chain_parts_receive_navigation() -> None:
    fake = FakeTelegraph({
        SYNOPSIS: next_part_nodes("/konspekt-2"),
        STUDY: [],
        PART2: [{"tag": "p", "children": ["часть 2"]}],
    })
    await add_navigation(PAGES, fake.io())

    edited = {url for url, _ in fake.edits}
    assert PART2 in edited
    part_nodes = dict(fake.edits)[PART2]
    # Часть ссылается на конспект и на разбор, но не на саму себя.
    assert SYNOPSIS in str(part_nodes)
    assert PART2 not in str(part_nodes)


@pytest.mark.asyncio
async def test_chain_does_not_loop_forever() -> None:
    fake = FakeTelegraph({
        SYNOPSIS: next_part_nodes("/konspekt-2"),
        STUDY: [],
        PART2: next_part_nodes("/konspekt-1"),  # ссылка назад на конспект
    })
    await add_navigation(PAGES, fake.io())

    assert fake.fetches.count(PART2) == 1


@pytest.mark.asyncio
async def test_chain_part_with_navigation_is_not_edited_again() -> None:
    existing = [{"tag": "p", "children": ["часть 2"]}, {"tag": "p", "children": [NAV_HEADER]}]
    fake = FakeTelegraph({
        SYNOPSIS: next_part_nodes("/konspekt-2"),
        STUDY: [],
        PART2: existing,
    })
    await add_navigation(PAGES, fake.io())

    assert PART2 not in {url for url, _ in fake.edits}


@pytest.mark.asyncio
async def test_chain_part_failure_does_not_stop_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeTelegraph(
        {
            SYNOPSIS: [*next_part_nodes("/konspekt-2"), *next_part_nodes("/konspekt-3")],
            STUDY: [],
            PART2: [{"tag": "p", "children": ["2"]}],
            "https://telegra.ph/konspekt-3": [{"tag": "p", "children": ["3"]}],
        },
        fetch_error={PART2: RuntimeError("часть недоступна")},
    )
    with caplog.at_level(logging.WARNING):
        await add_navigation(PAGES, fake.io())

    assert "не удалось добавить на часть" in caplog.text
    assert "https://telegra.ph/konspekt-3" in {url for url, _ in fake.edits}


def test_production_page_io_is_importable() -> None:
    """Боевая обвязка собирается без сети (проверяем только типы вызовов)."""
    io = nav.production_page_io()
    assert callable(io.fetch) and callable(io.edit)


# ── Боевая обвязка без сети ──────────────────────────────────────────────


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_production_fetch_builds_getpage_url_and_maps_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import requests

    seen = {}

    def fake_get(url, timeout):
        seen["url"] = url
        seen["timeout"] = timeout
        return FakeResponse({
            "ok": True,
            "result": {"title": "Т", "author_name": "А", "content": [{"tag": "p"}]},
        })

    monkeypatch.setattr(requests, "get", fake_get)
    io = nav.production_page_io()
    page = await io.fetch("https://telegra.ph/konspekt-1")

    assert seen["url"] == (
        "https://api.telegra.ph/getPage/konspekt-1?return_content=true"
    )
    assert seen["timeout"] == 30
    assert page == PageContent(
        url="https://telegra.ph/konspekt-1",
        title="Т",
        author="А",
        nodes=[{"tag": "p"}],
    )


@pytest.mark.asyncio
async def test_production_fetch_returns_none_when_api_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import requests

    monkeypatch.setattr(requests, "get", lambda url, timeout: FakeResponse({"ok": False}))
    assert await nav.production_page_io().fetch("https://telegra.ph/x") is None


@pytest.mark.asyncio
async def test_production_edit_delegates_to_edit_telegraph_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from converters import md_telegraph

    seen = {}

    async def fake_edit(url, title, author, nodes, loop):
        seen.update(url=url, title=title, author=author, nodes=nodes, loop=loop)
        return True

    monkeypatch.setattr(md_telegraph, "_edit_telegraph_page", fake_edit)
    ok = await nav.production_page_io().edit("https://telegra.ph/x", "t", "a", [{"tag": "p"}])

    assert ok is True
    assert seen["url"] == "https://telegra.ph/x"
    assert seen["nodes"] == [{"tag": "p"}]
    assert seen["loop"] is not None


# ── Вырожденные случаи чистых функций ────────────────────────────────────


def test_compact_nav_empty_when_only_self() -> None:
    assert compact_nav_nodes(SYNOPSIS, [("Конспект", SYNOPSIS)]) == []


@pytest.mark.parametrize(
    "nodes",
    [
        ["строка"],
        [{"tag": "hr"}],
        [{"tag": "p"}],
        [{"tag": "p", "children": []}],
        [{"tag": "p", "children": ["обычный текст"]}],
        [{"tag": "p", "children": [{"tag": "b", "children": ["Итог"]}]}],
    ],
)
def test_strip_trailing_nav_ignores_other_shapes(nodes) -> None:
    assert strip_trailing_nav(nodes) == nodes


def test_find_next_urls_skips_non_dict_entries() -> None:
    assert find_next_urls(["строка", {"tag": "p"}]) == []


@pytest.mark.asyncio
async def test_duplicate_page_urls_produce_no_navigation() -> None:
    """Один и тот же URL дважды — не «две страницы», ставить нечего."""
    fake = FakeTelegraph({SYNOPSIS: []})
    assert await add_navigation([("Конспект", SYNOPSIS), ("Конспект", SYNOPSIS)], fake.io()) == 2
    assert fake.edits == []


@pytest.mark.asyncio
async def test_chain_part_unavailable_is_skipped() -> None:
    fake = FakeTelegraph({SYNOPSIS: next_part_nodes("/konspekt-2"), STUDY: []})
    await add_navigation(PAGES, fake.io())

    assert PART2 not in {url for url, _ in fake.edits}
    assert SYNOPSIS in {url for url, _ in fake.edits}


@pytest.mark.asyncio
async def test_chain_part_size_limit_warns_without_failing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeTelegraph(
        {SYNOPSIS: next_part_nodes("/konspekt-2"), STUDY: [], PART2: [{"tag": "p"}]},
        edit_ok=lambda url, nodes: "📂" not in str(nodes),
    )
    with caplog.at_level(logging.WARNING):
        await add_navigation(PAGES, fake.io())

    assert "часть https://telegra.ph/konspekt-2 у лимита размера" in caplog.text


@pytest.mark.asyncio
async def test_chain_part_with_empty_nav_is_skipped() -> None:
    """Защита от вырожденного плана: для части нечего сгенерировать."""
    from pipelines.telegraph_nav import _walk_chain

    fake = FakeTelegraph({PART2: [{"tag": "p"}]})
    await _walk_chain(next_part_nodes("/konspekt-2"), SYNOPSIS, [("X", PART2), ("Y", PART2)], fake.io())

    assert fake.edits == []


def test_find_next_urls_handles_string_children() -> None:
    nodes = [
        {"tag": "p", "children": "просто текст"},
        {"tag": "p", "children": [{"tag": "b", "children": "тоже текст"}]},
        {"tag": "div", "children": ["x", {"tag": "span"}]},
    ]
    assert find_next_urls(nodes) == []


@pytest.mark.asyncio
async def test_part_queued_twice_is_visited_once() -> None:
    """Две ссылки на одну и ту же часть: обрабатываем её один раз."""
    p3 = "https://telegra.ph/konspekt-3"
    fake = FakeTelegraph({
        SYNOPSIS: [*next_part_nodes("/konspekt-2"), *next_part_nodes("/konspekt-3")],
        STUDY: [],
        PART2: next_part_nodes("/konspekt-3"),
        p3: [{"tag": "p", "children": ["3"]}],
    })
    await add_navigation(PAGES, fake.io())

    assert fake.fetches.count(p3) == 1
    assert p3 in {url for url, _ in fake.edits}


def test_find_next_urls_skips_marker_links_without_href() -> None:
    nodes = [
        {"tag": "p", "children": [{"tag": "a", "children": ["➡ Дальше"]}]},
        {"tag": "p", "children": [
            {"tag": "a", "attrs": {"href": ""}, "children": ["➡ Дальше"]},
        ]},
    ]
    assert find_next_urls(nodes) == []
