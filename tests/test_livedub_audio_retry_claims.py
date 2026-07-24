from pathlib import Path


def test_failed_mp3_attempt_releases_dedupe_claim():
    src = Path("services/livedub_deep_audit.py").read_text(encoding="utf-8")
    assert "except Exception:\n                _release_audio_claim(key)" in src
    assert "if not result:\n                _release_audio_claim(key)" in src
    assert 'wrap("_send_new_audio", "new")' in src
    assert 'wrap("_send_cached_audio", "cached")' in src


def test_successful_duplicate_claim_remains_suppressed():
    src = Path("services/livedub_deep_audit.py").read_text(encoding="utf-8")
    # Claims are released only on exception or false/no-op result, not on success.
    assert "return result" in src
    assert "_AUDIO_SENT.pop(key, None)" in src


def test_video_without_required_mp3_cannot_enter_file_id_cache():
    src = Path("services/livedub_deep_audit.py").read_text(encoding="utf-8")
    assert "_MP3_COMPANION_FAILED.set(True)" in src
    assert "if _MP3_COMPANION_FAILED.get():" in src
    assert "result is intentionally not cacheable" in src
    assert "_wrap_video_requires_mp3(Bot)" in src
