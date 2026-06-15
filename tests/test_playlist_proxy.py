"""Regression tests: playlist.py must pass proxy and safe cookies to yt-dlp Python API."""
from pathlib import Path
import re


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


def test_playlist_uses_safe_cookie_check():
    """playlist.py must use _firefox_cookie_source_available, not shutil.which('firefox').

    shutil.which only checks if the binary exists. On machines with Firefox
    installed but no profile, yt-dlp crashes with 'could not find firefox
    cookies database'. _firefox_cookie_source_available verifies the profile.
    """
    src = Path("pipelines/playlist.py").read_text(encoding="utf-8")
    assert "_firefox_cookie_source_available" in src, (
        "playlist.py must use _firefox_cookie_source_available from services.ffmpeg"
    )
    # Strip comments: check that shutil.which("firefox") is NOT used in active code
    active_lines = [
        line for line in src.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    active_code = "\n".join(active_lines)
    assert 'shutil.which("firefox")' not in active_code and "shutil.which('firefox')" not in active_code, (
        "playlist.py must NOT use shutil.which('firefox') in active code — "
        "use _firefox_cookie_source_available instead"
    )
