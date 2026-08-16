#!/usr/bin/env python3
"""AUDIT R28 (запрос пользователя + скриншоты): реальное видео проповедника
в 9:16 crop'ается отлично (заполняет кадр), а статичная картинка-заставка
(«АНАТОМИЯ ЦЕРКВИ» — аудио-проповедь с обложкой) при crop режется криво
(заголовок за краем). Фикс: детектор статичного кадра (freezedetect); для
таких фрагментов Shorts/Montage переключаются на уже существующий режим
full_frame_blur (картинка целиком по центру + размытый фон + субтитры на
нём). Реальное видео остаётся на crop_zoom.
"""
import inspect
from pathlib import Path

from services.ffmpeg import _is_static_video


def test_is_static_video_is_async_and_uses_source_owned_static_policy():
    assert inspect.iscoroutinefunction(_is_static_video)
    wrapper = Path("services/ffmpeg.py").read_text(encoding="utf-8")
    policy = Path("services/shorts_static_policy.py").read_text(encoding="utf-8")
    assert "_is_static_video_confident" in wrapper
    assert "freezedetect" in policy
    assert "moving/default-crop" in policy



def test_shorts_render_switches_static_to_full_frame():
    src = Path("services/shorts_video_impl.py").read_text(encoding="utf-8")
    assert "_is_static_video(source_video_path" in src
    idx = src.find("_is_static_video(source_video_path")
    window = src[idx - 200:idx + 200]
    assert 'visual_mode == "crop_zoom"' in window
    assert 'visual_mode = "full_frame_vertical"' in src


def test_montage_render_switches_static_to_full_frame():
    src = Path("services/render_clips_montage.py").read_text(encoding="utf-8")
    assert "_is_static_video(source_video_path" in src
    assert 'visual_mode = "full_frame_vertical"' in src


def test_full_frame_blur_filter_still_present_in_both_renderers():
    """Режим вписывания (blur-фон + overlay по центру) должен существовать —
    именно в него мы переключаемся для статичных кадров."""
    for path in ("services/shorts_video_impl.py", "services/render_clips_montage.py"):
        src = Path(path).read_text(encoding="utf-8")
        assert "gblur=sigma=20" in src
        assert "overlay=(W-w)/2:(H-h)/2" in src
