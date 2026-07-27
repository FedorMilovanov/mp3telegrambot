from __future__ import annotations

import pytest

from handlers.dub_wizard import _extract_youtube_video_id


@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://youtube.com/shorts/tNlIoCeGyLk?si=abc", "tNlIoCeGyLk"),
        ("https://youtu.be/tNlIoCeGyLk", "tNlIoCeGyLk"),
        ("https://www.youtube.com/watch?v=tNlIoCeGyLk", "tNlIoCeGyLk"),
    ],
)
def test_extract_youtube_video_id(url: str, video_id: str) -> None:
    actual, canonical = _extract_youtube_video_id(url)
    assert actual == video_id
    assert canonical.endswith(video_id)


def test_rejects_non_youtube_url() -> None:
    with pytest.raises(ValueError, match="YouTube"):
        _extract_youtube_video_id("https://example.com/video")
