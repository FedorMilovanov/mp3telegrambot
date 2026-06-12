"""Regression tests for yt-dlp proxy handling in no-TUN setups."""
from pathlib import Path


def test_ytdlp_proxy_knobs_documented():
    env = Path(".env.example").read_text(encoding="utf-8")
    assert "YTDLP_PROXY_URL" in env
    assert "YTDLP_INFO_TIMEOUT" in env
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "yt-dlp" in readme and "YTDLP_PROXY_URL" in readme


def test_ytdlp_reuses_proxy_and_bumps_metadata_timeout():
    ff = Path("services/ffmpeg.py").read_text(encoding="utf-8")
    mp = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "def _proxy_for_ytdlp" in ff
    assert "YTDLP_PROXY_URL" in ff
    assert 'args += ["--proxy", _proxy]' in ff
    assert "SOCKS proxy заменён на HTTP mixed proxy" in ff
    assert "YTDLP_INFO_TIMEOUT" in mp
    assert "yt-dlp --dump-json timeout" in mp
