#!/usr/bin/env python3
"""Автопочинка после публикации: изоляция страниц, цепочка частей, отчёт."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from pipelines.telegraph_repair_stage import (
    PageRepair,
    RepairReport,
    repair_enabled,
    repair_pages,
    unique_urls,
)

A = "https://telegra.ph/a"
B = "https://telegra.ph/b"
C = "https://telegra.ph/c"


@dataclass
class ServiceResult:
    """Копия `TelegraphRepairResult` из services — без его импорта."""

    url: str
    ok: bool = True
    changed: bool = False
    error: str = ""
    audit_summary: str = ""


class Service:
    def __init__(self, chains=None, results=None, errors=None):
        self.chains = chains or {}
        self.results = results or {}
        self.errors = errors or {}
        self.expanded: list[str] = []
        self.repaired: list[str] = []
        self.sleeps: list[float] = []

    async def expand(self, url: str):
        self.expanded.append(url)
        return self.chains.get(url)

    async def repair(self, url: str):
        self.repaired.append(url)
        if url in self.errors:
            raise self.errors[url]
        return self.results.get(url, ServiceResult(url=url, ok=True))

    async def sleep(self, delay: float):
        self.sleeps.append(delay)

    async def run(self, urls, **kwargs):
        return await repair_pages(
            urls, expand=self.expand, repair=self.repair, sleep=self.sleep, **kwargs
        )


# ── Настройка и нормализация ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, True),
        ("", True),
        ("1", True),
        ("true", True),
        ("YES", True),
        ("0", False),
        ("false", False),
        ("No", False),
        ("off", False),
    ],
)
def test_repair_enabled_reads_flag(value, expected) -> None:
    assert repair_enabled(value) is expected


def test_repair_enabled_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAPH_AUTO_REPAIR_AFTER_PUBLISH", raising=False)
    assert repair_enabled() is True
    monkeypatch.setenv("TELEGRAPH_AUTO_REPAIR_AFTER_PUBLISH", "0")
    assert repair_enabled() is False


def test_unique_urls_drops_empty_and_repeats_keeping_order() -> None:
    assert unique_urls([A, None, "", B, A, C, B]) == [A, B, C]
    assert unique_urls([]) == []


# ── Проход по страницам ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_makes_no_calls() -> None:
    service = Service()
    report = await service.run([A, B], enabled=False)

    assert report.results == []
    assert service.repaired == []
    assert service.expanded == []


@pytest.mark.asyncio
async def test_no_urls_makes_no_calls() -> None:
    service = Service()
    report = await service.run([None, ""])

    assert report.pages == 0
    assert service.expanded == []


@pytest.mark.asyncio
async def test_each_page_is_repaired_once() -> None:
    service = Service(results={A: ServiceResult(A, changed=True), B: ServiceResult(B)})
    report = await service.run([A, B, A])

    assert service.repaired == [A, B]
    assert report.pages == 2
    assert report.changed_count == 1


@pytest.mark.asyncio
async def test_chain_parts_are_expanded_before_repair() -> None:
    service = Service(chains={A: [A, B], B: [B, C]})
    report = await service.run([A, B])

    assert service.repaired == [A, B, C]
    assert report.pages == 3


@pytest.mark.asyncio
async def test_expand_failure_does_not_stop_publication(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = Service()
    service.expand = _raiser  # type: ignore[assignment]
    with caplog.at_level(logging.WARNING):
        report = await service.run([A])

    assert report.results == []
    assert "crashed non-fatally" in caplog.text


async def _raiser(url: str):
    raise RuntimeError("сеть легла")


@pytest.mark.asyncio
async def test_one_failed_page_does_not_cancel_the_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = Service(errors={A: RuntimeError("editPage упал")})
    with caplog.at_level(logging.WARNING):
        report = await service.run([A, B])

    assert service.repaired == [A, B]
    assert report.pages == 2
    assert report.results[0].ok is False
    assert report.results[0].error == "editPage упал"
    assert report.results[1].ok is True
    assert "error=editPage упал" in caplog.text


@pytest.mark.asyncio
async def test_changed_and_audit_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    service = Service(
        results={
            A: ServiceResult(A, changed=True),
            B: ServiceResult(B, audit_summary="остались двойные пробелы"),
            C: ServiceResult(C, ok=False, error="CONTENT_TOO_BIG"),
        }
    )
    with caplog.at_level(logging.INFO):
        report = await service.run([A, B, C])

    assert "changed https://telegra.ph/a" in caplog.text
    assert "unresolved audit for https://telegra.ph/b" in caplog.text
    assert "pages=3 changed=1" in caplog.text
    assert report.errors == ["остались двойные пробелы", "CONTENT_TOO_BIG"]


@pytest.mark.asyncio
async def test_pause_between_pages() -> None:
    service = Service()
    await service.run([A, B])

    assert service.sleeps == [0.5, 0.5]


@pytest.mark.asyncio
async def test_zero_delay_is_respected() -> None:
    service = Service()
    await service.run([A], delay=0)

    assert service.sleeps == []


def test_report_properties() -> None:
    report = RepairReport(
        results=[
            PageRepair(url=A, ok=True, changed=True),
            PageRepair(url=B, ok=False, error="e"),
        ]
    )
    assert report.pages == 2
    assert report.changed_count == 1
    assert report.errors == ["e"]
    assert RepairReport().errors == []


def test_production_deps_are_the_service_functions() -> None:
    from pipelines.telegraph_repair_stage import production_deps
    from services.telegraph_repair import (
        expand_telegraph_page_chain,
        repair_telegraph_page_url,
    )

    expand, repair = production_deps()
    assert expand is expand_telegraph_page_chain
    assert repair is repair_telegraph_page_url


@pytest.mark.asyncio
async def test_default_dependencies_are_used_when_not_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без явных зависимостей стадия берёт боевые (`production_deps`)."""
    from pipelines import telegraph_repair_stage as stage

    calls: list[str] = []

    async def fake_expand(url):
        calls.append(f"expand:{url}")
        return [url]

    async def fake_repair(url):
        calls.append(f"repair:{url}")
        return ServiceResult(url, changed=True)

    async def fake_sleep(delay):
        calls.append(f"sleep:{delay}")

    monkeypatch.setattr(stage, "production_deps", lambda: (fake_expand, fake_repair))
    report = await repair_pages([A], sleep=fake_sleep)

    assert calls == [f"expand:{A}", f"repair:{A}", "sleep:0.5"]
    assert report.changed_count == 1


@pytest.mark.asyncio
async def test_empty_chain_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    service = Service(chains={A: ["", None]})  # цепочка без валидных URL
    with caplog.at_level(logging.INFO):
        report = await service.run([A])

    assert report.results == []
    assert service.repaired == []
    assert "pages=" not in caplog.text
