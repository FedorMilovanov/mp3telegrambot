"""Regression test: playlist.py must pass proxy to yt-dlp Python API."""
from pathlib import Path


def test_playlist_imports_proxy_helper():
    """playlist.py must import _proxy_for_ytdlp from services.ffmpeg."""
    src = Path("pipelines/playlist.py").read_text(encoding="utf-8")
    assert "_proxy_for_ytdlp" in src, (
        "playlist.py does not reference _proxy_for_ytdlp — "
        "yt-dlp Python API won't use proxy in no-TUN mode"
    )
    assert "from services.ffmpeg" in src
    assert "_proxy_for_ytdlp" in src.split("from services.ffmpeg")[1].split("\n")[0], (
        "_proxy_for_ytdlp must be imported from services.ffmpeg"
    )


def test_playlist_opts_include_proxy():
    """playlist_opts must set 'proxy' key when _proxy_for_ytdlp() returns a value."""
    src = Path("pipelines/playlist.py").read_text(encoding="utf-8")
    assert 'playlist_opts["proxy"]' in src or "playlist_opts['proxy']" in src, (
        "playlist_opts must include proxy key for yt-dlp Python API"
    )
