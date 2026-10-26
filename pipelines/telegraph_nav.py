#!/usr/bin/env python3
"""Навигация между Telegraph-страницами: сборка блока, editPage и обход цепочки.

Раньше это жило внутри `process_single_video` одним вложенным замыканием на
~170 строк с `requests.get` и `run_in_executor` прямо в теле. Проверить его было
нельзя без настоящего telegra.ph, поэтому правила держались на комментариях:

* страница не ссылается сама на себя;
* повторный запуск не должен оставлять два блока — старый снимается, новый
  ставится;
* если editPage отверг полный блок (страница у лимита 64 КБ), ставится
  компактный, без `hr` и заголовка;
* блок нужен не только трём основным страницам: по ссылкам «➡ Дальше» находятся
  остальные части длинной публикации, и каждая получает тот же блок;
* обход цепочки защищён от циклов, а сбой одной части не отменяет остальные;
* сетевой сбой — две попытки с паузой 5 с.

Здесь правила отделены от HTTP: `PageIO` — это две функции, `fetch` и `edit`.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

#: Заголовок блока перелинковки. Виден читателю, поэтому закреплён тестом.
NAV_HEADER = "📂 Читать также:"
#: Компактный вариант — когда страница у лимита Telegraph.
NAV_COMPACT_PREFIX = "📂 "
#: Сколько попыток делает editPage при сетевом сбое.
NAV_ATTEMPTS = 2
#: Пауза между попытками, секунды.
NAV_RETRY_DELAY = 5.0
#: Сколько последних узлов проверять на уже стоящий блок.
NAV_TAIL = 3
#: Маркер ссылки на следующую часть публикации.
NEXT_PART_MARKER = "➡"
#: База telegra.ph — используется для нормализации относительных ссылок.
TELEGRAPH_BASE = "https://telegra.ph"

Node = dict[str, Any]


@dataclass(frozen=True)
class PageContent:
    """Что вернул getPage: содержимое и метаданные для editPage."""

    url: str
    title: str
    author: str
    nodes: list[Any] = field(default_factory=list)


#: Прочитать страницу; `None` — страница недоступна (getPage вернул not ok).
FetchFn = Callable[[str], Awaitable[PageContent | None]]
#: Записать страницу; `False` — Telegraph отверг содержимое (лимит размера).
EditFn = Callable[[str, str, str, list[Any]], Awaitable[bool]]


@dataclass(frozen=True)
class PageIO:
    """Две операции с Telegraph — больше навигации ничего не нужно."""

    fetch: FetchFn
    edit: EditFn


def production_page_io() -> PageIO:
    """Боевые операции: requests в executor, чтобы не блокировать loop."""
    from converters.md_telegraph import _edit_telegraph_page

    async def fetch(path_url: str) -> PageContent | None:
        import requests

        loop = asyncio.get_running_loop()
        path = path_url.replace(f"{TELEGRAPH_BASE}/", "").lstrip("/")
        resp = await loop.run_in_executor(
            None,
            lambda: requests.get(
                f"https://api.telegra.ph/getPage/{path}?return_content=true",
                timeout=30,
            ),
        )
        data = resp.json()
        if not data.get("ok"):
            return None
        result = data["result"]
        return PageContent(
            url=path_url,
            title=result.get("title", ""),
            author=result.get("author_name", ""),
            nodes=result.get("content", []),
        )

    async def edit(url: str, title: str, author: str, nodes: list[Any]) -> bool:
        loop = asyncio.get_running_loop()
        return bool(await _edit_telegraph_page(url, title, author, nodes, loop))

    return PageIO(fetch=fetch, edit=edit)


def normalize_url(href: str) -> str:
    """Развернуть относительную ссылку telegra.ph в абсолютную."""
    if href.startswith("/"):
        return f"{TELEGRAPH_BASE}{href}"
    return href


def nav_children(page_url: str, nav_pages: list[tuple[str, str]]) -> list[Any]:
    """Ссылки блока: « · » между ними, без ссылки страницы на саму себя."""
    children: list[Any] = []
    for label, url in nav_pages:
        if not url or url == page_url:
            continue
        if children:
            children.append(" · ")
        children.append({"tag": "a", "attrs": {"href": url}, "children": [label]})
    return children


def nav_nodes(page_url: str, nav_pages: list[tuple[str, str]]) -> list[Node]:
    """Полный блок: `hr` + строка с заголовком. Пустой список — нечего ставить."""
    children = nav_children(page_url, nav_pages)
    if not children:
        return []
    return [
        {"tag": "hr"},
        # Пробел — вне <b>: Telegraph/Markdown-рендеры срезают хвостовые пробелы
        # внутри жирного узла, и получается **...:**[ссылка].
        {
            "tag": "p",
            "children": [{"tag": "b", "children": [NAV_HEADER]}, " "] + children,
        },
    ]


def compact_nav_nodes(page_url: str, nav_pages: list[tuple[str, str]]) -> list[Node]:
    """Компактный блок для страниц у лимита размера."""
    children = nav_children(page_url, nav_pages)
    if not children:
        return []
    return [{"tag": "p", "children": [NAV_COMPACT_PREFIX] + children}]


def strip_trailing_nav(nodes: list[Any]) -> list[Any]:
    """Снять ранее поставленный блок, чтобы повторный прогон не дублировал его."""
    if not nodes or not isinstance(nodes[-1], dict):
        return nodes
    if nodes[-1].get("tag") != "p":
        return nodes
    last_children = nodes[-1].get("children", [])
    if not last_children or not isinstance(last_children[0], dict):
        return nodes
    last_text = last_children[0].get("children", [""])
    if last_text and "Читать также" in str(last_text[0]):
        return nodes[:-2]  # hr + p
    return nodes


def has_nav(nodes: list[Any], tail: int = NAV_TAIL) -> bool:
    """Есть ли блок перелинковки в конце страницы."""
    return any(isinstance(node, dict) and "Читать также" in str(node.get("children", "")) for node in nodes[-tail:])


def find_next_urls(nodes: list[Any]) -> list[str]:
    """Все ссылки «➡ Дальше» в узлах, включая на один уровень вложенности."""
    urls: list[str] = []

    def _collect(children: Any) -> None:
        if not isinstance(children, list):
            return
        for child in children:
            if not isinstance(child, dict) or child.get("tag") != "a":
                continue
            if NEXT_PART_MARKER not in str(child.get("children", [])):
                continue
            href = (child.get("attrs") or {}).get("href", "")
            if href:
                urls.append(normalize_url(href))

    for node in nodes:
        if not isinstance(node, dict):
            continue
        children = node.get("children", [])
        _collect(children)
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    _collect(child.get("children", []))
    return urls


async def _edit_with_compact_fallback(
    page_url: str,
    title: str,
    author: str,
    nodes: list[Any],
    full_nav: list[Node],
    compact_nav: list[Node],
    io: PageIO,
) -> bool:
    """Поставить полный блок; при отказе Telegraph — компактный."""
    if await io.edit(page_url, title, author, nodes + full_nav):
        return True
    return bool(await io.edit(page_url, title, author, nodes + compact_nav))


async def _add_nav_to_page(
    page_url: str,
    page_label: str,
    nav_pages: list[tuple[str, str]],
    io: PageIO,
) -> None:
    """Поставить блок на одну страницу и пройти по её цепочке «➡ Дальше»."""
    full_nav = nav_nodes(page_url, nav_pages)
    compact_nav = compact_nav_nodes(page_url, nav_pages)
    if not full_nav:
        return

    for attempt in range(NAV_ATTEMPTS):
        try:
            page = await io.fetch(page_url)
            if page is None:
                return
            title = page.title or page_label
            clean_nodes = strip_trailing_nav(page.nodes)
            ok = await _edit_with_compact_fallback(page_url, title, page.author, clean_nodes, full_nav, compact_nav, io)
            if ok:
                logger.info("Navigation: добавлена навигация на %s", page_url)
            else:
                logger.warning(
                    "Navigation: %s у лимита размера Telegraph — навигация не добавлена (страница цела, но без футера)",
                    page_url,
                )
            await _walk_chain(page.nodes, page_url, nav_pages, io)
            return
        except Exception as err:  # сеть, битый JSON, отказ API
            if attempt == 0:
                logger.warning(
                    "Navigation: ошибка на %s (попытка 1/%d): %s — retry через %.0fс",
                    page_url,
                    NAV_ATTEMPTS,
                    err,
                    NAV_RETRY_DELAY,
                )
                await asyncio.sleep(NAV_RETRY_DELAY)
            else:
                logger.warning("Navigation: не удалось добавить навигацию на %s: %s", page_url, err)


async def _walk_chain(
    nodes: list[Any],
    origin_url: str,
    nav_pages: list[tuple[str, str]],
    io: PageIO,
) -> None:
    """Пройти по «➡ Дальше» и поставить блок на каждую часть (без циклов).

    Блок строится **для каждой части отдельно**: раньше он собирался один раз
    для исходной страницы и переиспользовался, поэтому часть получала ссылку на
    саму себя (если попадала в план) и никогда не ссылалась обратно на конспект.
    """
    visited = {origin_url}
    queue = find_next_urls(nodes)

    while queue:
        part_url = queue.pop(0)
        if part_url in visited:
            continue
        visited.add(part_url)
        try:
            part = await io.fetch(part_url)
            if part is None:
                continue
            queue.extend(url for url in find_next_urls(part.nodes) if url not in visited)
            if has_nav(part.nodes):
                continue
            part_full = nav_nodes(part_url, nav_pages)
            if not part_full:
                continue
            ok = await _edit_with_compact_fallback(
                part_url,
                part.title,
                part.author,
                part.nodes,
                part_full,
                compact_nav_nodes(part_url, nav_pages),
                io,
            )
            if ok:
                logger.info("Navigation: добавлена навигация на %s (часть 2+)", part_url)
            else:
                logger.warning("Navigation: часть %s у лимита размера — без футера", part_url)
        except Exception as err:  # одна часть не должна отменять остальные
            logger.warning("Navigation: не удалось добавить на часть %s: %s", part_url, err)


async def add_navigation(
    nav_pages: list[tuple[str, str]],
    io: PageIO | None = None,
) -> int:
    """Поставить блок на все страницы плана.

    Возвращает число страниц в плане — вызывающему коду это нужно только для
    лога; ошибки не пробрасываются, потому что навигация не критична для
    публикации.
    """
    pages = [(label, url) for label, url in nav_pages if url]
    if len(pages) < 2:
        return 0
    io = io or production_page_io()
    await asyncio.gather(
        *(_add_nav_to_page(url, label, pages, io) for label, url in pages),
        return_exceptions=True,
    )
    return len(pages)


__all__ = [
    "NAV_ATTEMPTS",
    "NAV_HEADER",
    "NAV_RETRY_DELAY",
    "NEXT_PART_MARKER",
    "PageContent",
    "PageIO",
    "add_navigation",
    "compact_nav_nodes",
    "find_next_urls",
    "has_nav",
    "nav_children",
    "nav_nodes",
    "normalize_url",
    "production_page_io",
    "strip_trailing_nav",
]
