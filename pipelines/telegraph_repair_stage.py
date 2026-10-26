#!/usr/bin/env python3
"""Починка Telegraph-страниц после публикации — шаг, а не побочный эффект.

После `createPage`/`editPage` страницы один раз перечитываются и, если
детерминированная чистка меняет узлы, переписываются. Это не ручной шаг
оператора и не вызов Gemini: так технические страницы остаются читаемыми, пока
история аудита фиксирует классы ошибок для правки генераторов.

Раньше блок жил в `process_single_video` и был одним `try` на все страницы:
падение на первой отменяло остальные, а причина видна была только в логе.
Здесь:

* каждая страница изолирована — сбой одной не мешает починить следующую;
* цепочка частей разворачивается до починки, иначе части 2+ остаются с
  исходными дефектами;
* результат возвращается структурой, а не списком «как получилось»: статус
  `changed`, ошибки и незакрытые замечания аудита уходят в архив одной записью.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

#: Переменная окружения, которой автопочинку можно выключить.
REPAIR_ENV = "TELEGRAPH_AUTO_REPAIR_AFTER_PUBLISH"
#: Пауза между страницами: telegra.ph отвечает медленно на серию editPage.
REPAIR_DELAY = 0.5
#: Значения, которые считаются «выключено».
FALSY = {"0", "false", "no", "off"}

ExpandFn = Callable[[str], Awaitable[list[str] | None]]
RepairFn = Callable[[str], Awaitable[Any]]


@dataclass(frozen=True)
class PageRepair:
    """Что произошло с одной страницей."""

    url: str
    ok: bool
    changed: bool = False
    error: str = ""
    audit_summary: str = ""


@dataclass(frozen=True)
class RepairReport:
    """Итог прохода: его читает лог и архив."""

    results: list[PageRepair] = field(default_factory=list)

    @property
    def pages(self) -> int:
        return len(self.results)

    @property
    def changed_count(self) -> int:
        return sum(1 for result in self.results if result.changed)

    @property
    def errors(self) -> list[str]:
        """Ошибки и незакрытые замечания аудита — для записи в архив."""
        return [
            str(value)
            for result in self.results
            for value in (result.error, result.audit_summary)
            if str(value or "").strip()
        ]


def repair_enabled(value: str | None = None) -> bool:
    """Включена ли автопочинка (по умолчанию — да)."""
    if value is None:
        import os

        value = os.getenv(REPAIR_ENV, "1")
    return (value or "1").strip().lower() not in FALSY


def unique_urls(urls: list[str | None]) -> list[str]:
    """Убрать пустые и повторы, сохранив порядок публикации."""
    seen: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.append(url)
    return seen


def _to_page_repair(url: str, raw: Any) -> PageRepair:
    """Привести результат сервиса к нашему представлению."""
    return PageRepair(
        url=str(getattr(raw, "url", "") or url),
        ok=bool(getattr(raw, "ok", False)),
        changed=bool(getattr(raw, "changed", False)),
        error=str(getattr(raw, "error", "") or ""),
        audit_summary=str(getattr(raw, "audit_summary", "") or ""),
    )


def production_deps() -> tuple[ExpandFn, RepairFn]:
    """Боевые функции починки; импорт внутри — чтобы не тянуть сеть при импорте."""
    from services.telegraph_repair import (
        expand_telegraph_page_chain,
        repair_telegraph_page_url,
    )

    return expand_telegraph_page_chain, repair_telegraph_page_url


async def repair_pages(
    urls: list[str | None],
    *,
    enabled: bool = True,
    expand: ExpandFn | None = None,
    repair: RepairFn | None = None,
    delay: float = REPAIR_DELAY,
    sleep: Callable[[float], Awaitable[Any]] | None = None,
) -> RepairReport:
    """Починить все опубликованные страницы; исключения наружу не выходят.

    Починка — улучшение, а не условие публикации: пользователь уже получил
    материал, и падение на этом шаге не должно превращать успех в ошибку.
    """
    pages = unique_urls(urls)
    if not enabled or not pages:
        return RepairReport()
    if expand is None or repair is None:
        expand, repair = production_deps()
    sleep = sleep or asyncio.sleep

    results: list[PageRepair] = []
    try:
        expanded: list[str] = []
        for base in pages:
            chain = await expand(base) or [base]
            for url in chain:
                if url and url not in expanded:
                    expanded.append(url)
    except Exception as err:  # разворот цепочки — тоже необязательный шаг
        logger.warning(
            "Auto Telegraph repair after publish crashed non-fatally: %s",
            str(err)[:180],
        )
        return RepairReport()

    for url in expanded:
        try:
            outcome = _to_page_repair(url, await repair(url))
        except Exception as err:  # одна страница не отменяет остальные
            logger.warning(
                "Auto Telegraph repair after publish failed: %s error=%s",
                url,
                str(err)[:180],
            )
            results.append(PageRepair(url=url, ok=False, error=str(err)[:180]))
            continue
        results.append(outcome)
        if outcome.ok and outcome.changed:
            logger.info("Auto Telegraph repair after publish: changed %s", url)
        elif not outcome.ok:
            logger.warning(
                "Auto Telegraph repair after publish failed: %s error=%s",
                url,
                outcome.error,
            )
        if outcome.audit_summary:
            logger.warning(
                "Auto Telegraph repair after publish unresolved audit for %s: %s",
                url,
                outcome.audit_summary[:300],
            )
        if delay:
            await sleep(delay)

    report = RepairReport(results=results)
    if report.results:
        logger.info(
            "Auto Telegraph repair after publish: pages=%d changed=%d",
            report.pages,
            report.changed_count,
        )
    return report


__all__ = [
    "FALSY",
    "REPAIR_DELAY",
    "REPAIR_ENV",
    "PageRepair",
    "RepairReport",
    "production_deps",
    "repair_enabled",
    "repair_pages",
    "unique_urls",
]
