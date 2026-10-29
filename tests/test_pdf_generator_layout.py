"""Executed PDF layout and empty-source contracts for modernized annotations."""

from __future__ import annotations
import pathlib
import pytest

import asyncio
from types import SimpleNamespace

from services import pdf_generator as pdf


def test_build_html_collects_sources_and_nested_tocs_without_mutating_sections() -> None:
    sections = [
        {
            "key": "synopsis",
            "content_html": ('<h3>Heading</h3><p><a href="https://youtu.be/example">Video</a></p>'),
        }
    ]
    snapshot = [dict(section) for section in sections]

    document = pdf._build_html("Title", "Performer", "1:00", sections)

    assert sections == snapshot
    assert "Heading" in document
    assert 'id="synopsis_h1"' in document
    assert 'href="https://youtu.be/example"' in document
    assert "YouTube" in document


def test_clean_block_elements_executes_typed_match_callback() -> None:
    cleaned = pdf._clean_block_elements("<p>  First <br> second  </p>")

    assert cleaned == "<p>First<br>second</p>"


def test_fetch_telegraph_bs4_executes_optional_parser_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pdf.BeautifulSoup is not None
    monkeypatch.setattr(
        pdf,
        "_fetch_url",
        lambda _url: SimpleNamespace(text="<article><header>Hidden</header><p>Visible paragraph</p></article>"),
    )

    result = pdf._fetch_telegraph_bs4("https://telegra.ph/typed-parser")

    assert "Visible paragraph" in result
    assert "Hidden" not in result


def test_sync_pdf_generation_stops_when_every_source_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setattr(pdf, "_fetch_telegraph", lambda _url: "")

    result = pdf.generate_sermon_pdf(
        str(tmp_path / "empty.pdf"),
        "Title",
        "Performer",
        "1:00",
        {"synopsis": "https://telegra.ph/empty"},
    )

    assert result is None


def test_async_pdf_generation_stops_when_every_source_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setattr(pdf, "_fetch_telegraph", lambda _url: "")

    result = asyncio.run(
        pdf.generate_sermon_pdf_async(
            str(tmp_path / "empty.pdf"),
            "Title",
            "Performer",
            "1:00",
            {"synopsis": "https://telegra.ph/empty"},
        )
    )

    assert result is None
