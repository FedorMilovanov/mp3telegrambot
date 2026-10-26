"""Security regressions for HTML cleanup and source-link host classification."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

import pytest

from services.pdf_generator import (
    _enrich_h3_headings,
    _find_video_links,
    _strip_dangerous_tags,
    _video_ts_url,
)

_EMPTY_LINKS = {"youtube": "", "rutube": "", "vk": ""}


@pytest.mark.parametrize("tag", ["script", "style"])
@pytest.mark.parametrize("closing", ["</{tag} >", "</{tag}\n>", "</{tag} data-x>"])
def test_dangerous_tag_cleanup_accepts_html_closing_whitespace(tag: str, closing: str) -> None:
    closing = closing.format(tag=tag)
    html = f"<p>before</p><{tag}>payload{closing}<p>after</p>"

    cleaned = _strip_dangerous_tags(html)

    assert "payload" not in cleaned
    assert f"<{tag}" not in cleaned
    assert "<p>before</p>" in cleaned
    assert "<p>after</p>" in cleaned


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com.evil.example/watch?v=1",
        "https://notyoutube.com/watch?v=1",
        "https://youtu.be.evil.example/abc",
        "https://rutube.ru.evil.example/video/1",
        "https://vk.com.evil.example/video1",
        "https://vkvideo.ru.evil.example/video1",
        "https://youtube.com@evil.example/watch?v=1",
        "https://evil.example/?next=youtube.com",
    ],
)
def test_video_hosts_reject_substring_lookalikes(url: str) -> None:
    assert _find_video_links(f'<a href="{url}">source</a>') == _EMPTY_LINKS
    assert _video_ts_url(url, 42) == url


@pytest.mark.parametrize(
    ("url", "label"),
    [
        ("https://www.youtube.com/watch?v=1", "YouTube"),
        ("https://rutube.ru/video/1", "RuTube"),
        ("https://vk.com/video1", "VK"),
    ],
)
def test_h3_enrichment_uses_the_same_host_policy(url: str, label: str) -> None:
    enriched = _enrich_h3_headings(f'<h3><a href="{url}">Title</a> ⏱ 1:00</h3>')
    assert 'class="pl"' in enriched
    assert label in enriched


def test_h3_enrichment_rejects_all_lookalike_hosts() -> None:
    html = (
        '<h3><a href="https://youtube.com.evil.example/1">YouTube</a>'
        '<a href="https://rutube.ru.evil.example/1">RuTube</a>'
        '<a href="https://vk.com.evil.example/1">VK</a> ⏱ 1:00</h3>'
    )
    assert 'class="pl"' not in _enrich_h3_headings(html)


def test_video_hosts_accept_real_domains_and_subdomains() -> None:
    html = (
        '<a href="https://www.youtube.com/watch?v=1">yt</a>'
        '<a href="https://rutube.ru/video/1">rt</a>'
        '<a href="https://vk.com/video1">vk</a>'
    )
    assert _find_video_links(html) == {
        "youtube": "https://www.youtube.com/watch?v=1",
        "rutube": "https://rutube.ru/video/1",
        "vk": "https://vk.com/video1",
    }
    assert _find_video_links('<a href="https://youtu.be/abc">yt</a>')["youtube"] == "https://youtu.be/abc"
    assert _find_video_links('<a href="https://vkvideo.ru/video1">vk</a>')["vk"] == "https://vkvideo.ru/video1"


@pytest.mark.parametrize(
    ("url", "expected_t"),
    [
        ("https://www.youtube.com/watch?v=1", "42s"),
        ("https://rutube.ru/video/1", "42"),
        ("https://vk.com/video1", "42s"),
        ("https://WWW.YOUTUBE.COM./watch?v=1", "42s"),
    ],
)
def test_video_timestamp_uses_parsed_host(url: str, expected_t: str) -> None:
    enriched = _video_ts_url(url, 42)
    assert dict(parse_qsl(urlsplit(enriched).query))["t"] == expected_t


def test_invalid_authority_does_not_escape_host_parser() -> None:
    url = "http://[::1"
    assert _video_ts_url(url, 42) == url
