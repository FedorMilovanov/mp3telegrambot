"""Executed PDF layout and empty-source contracts for modernized annotations."""

from __future__ import annotations

import asyncio

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


def test_sync_pdf_generation_stops_when_every_source_is_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(pdf, "_fetch_telegraph", lambda _url: "")

    result = pdf.generate_sermon_pdf(
        str(tmp_path / "empty.pdf"),
        "Title",
        "Performer",
        "1:00",
        {"synopsis": "https://telegra.ph/empty"},
    )

    assert result is None


def test_async_pdf_generation_stops_when_every_source_is_empty(monkeypatch, tmp_path) -> None:
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
