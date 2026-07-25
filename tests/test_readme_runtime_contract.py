from pathlib import Path


def test_readme_matches_current_startup_and_dual_audio_contract():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "TELEGRAM_API_ID" in readme
    assert "TELEGRAM_API_HASH" in readme
    assert "LOCAL_BOT_API_ID" not in readme
    assert "LOCAL_BOT_API_HASH" not in readme
    assert "не использует тихий облачный fallback" in readme
    assert "gemini-3.5-flash-lite" in readme
    assert "чистый русский MP3" in readme
    assert "финальный объединённый MP3" in readme
