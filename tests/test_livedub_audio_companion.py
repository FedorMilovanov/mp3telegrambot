from pathlib import Path

import services.livedub_audio_companion as companion


def test_livedub_caption_detection_is_narrow() -> None:
    assert companion._is_livedub_caption("<b>Лекция</b>\n🎬 Живые голоса Яндекса")
    assert companion._is_livedub_caption("🎬 Перевод Яндекса (обычные голоса)")
    assert not companion._is_livedub_caption("Обычное видео с русскими субтитрами")


def test_title_and_variant_filenames_are_human_readable(tmp_path: Path) -> None:
    title, performer = companion._title_parts("<b>Как Мы Знаем? - Р. Ч. Спроул</b>", "fallback")
    assert title == "Как Мы Знаем?" and performer == "Р. Ч. Спроул"
    clean = companion._safe_filename(tmp_path / "pro_dub.mp4", title, "clean")
    mixed = companion._safe_filename(tmp_path / "pro_dub.mp4", title, "mixed")
    assert clean.endswith("чистый RU.mp3") and mixed.endswith("финальный микс.mp3")
    assert clean != mixed and "?" not in clean and "?" not in mixed


def test_public_error_text_masks_bot_tokens_and_proxy_passwords() -> None:
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi_123456"
    public = companion._public_error_text(RuntimeError(f"https://api.telegram.org/bot{token}/sendAudio via http://user:secret@127.0.0.1:1080"), 500)
    assert token not in public and "secret" not in public and "***" in public


def test_file_id_cache_stores_two_independent_variants(tmp_path: Path, monkeypatch) -> None:
    cache_path = tmp_path / "audio-map.json"
    monkeypatch.setattr(companion, "_cache_path", lambda: cache_path)
    companion._cache_put_variant("video-1", "clean", "audio-clean", title="Title")
    companion._cache_put_variant("video-1", "mixed", "audio-mixed", title="Title")
    cached = companion._cache_get("video-1")
    assert cached["variants"]["clean"]["audio_file_id"] == "audio-clean"
    assert cached["variants"]["mixed"]["audio_file_id"] == "audio-mixed"
    companion._cache_drop_variant("video-1", "clean")
    assert "clean" not in companion._cache_get("video-1")["variants"]
    companion._cache_drop("video-1")
    assert companion._cache_get("video-1") is None


def test_companion_validator_does_not_install_telegram_wrappers() -> None:
    source = Path("services/livedub_audio_companion.py").read_text(encoding="utf-8")
    assert "_wrap_send_video" not in source
    assert "Bot.send_video =" not in source
    assert "ExtBot.send_video =" not in source
    assert "explicit coordinator delivery" in companion.validate_livedub_audio_companion()
