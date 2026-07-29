from __future__ import annotations

from pathlib import Path

import pytest

from handlers import dub_wizard
from tools.voxcpm2 import clean_source_download


@pytest.mark.parametrize(
    "url",
    [
        "youtu.be/AbCdEf12345?t=3",
        "https://music.youtube.com/watch?v=AbCdEf12345",
        "https://m.youtube.com/shorts/AbCdEf12345",
        "https://www.youtube-nocookie.com/embed/AbCdEf12345",
    ],
)
def test_wizard_uses_the_production_single_video_parser(url: str) -> None:
    video_id, canonical = dub_wizard._extract_youtube_video_id(url)
    assert video_id == "AbCdEf12345"
    assert canonical == "https://youtube.com/watch?v=AbCdEf12345"
    assert clean_source_download._url_video_id(canonical) == video_id


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com/playlist?list=PL123",
        "https://youtube.com/@channel",
        "https://example.com/watch?v=AbCdEf12345",
    ],
)
def test_wizard_rejects_non_single_video_sources(url: str) -> None:
    with pytest.raises(ValueError, match="каноническая ссылка на один YouTube-ролик"):
        dub_wizard._extract_youtube_video_id(url)


def test_wizard_facade_patches_legacy_callbacks() -> None:
    assert Path(dub_wizard.__file__).name == "__init__.py"
    assert dub_wizard._legacy._extract_youtube_video_id is dub_wizard._extract_youtube_video_id
    facade = Path(dub_wizard.__file__).read_text(encoding="utf-8")
    assert "clean_source_download._url_video_id(raw)" in facade
