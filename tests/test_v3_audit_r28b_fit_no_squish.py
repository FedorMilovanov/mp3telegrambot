#!/usr/bin/env python3
"""AUDIT R28b (живой прогон 2026-07-10, оператор: «засплющило, не 9:16, а
квадрат, с расплющенной авой»).

R28 переключал статичную заставку с crop на full_frame_blur, но фон блюра
масштабировался как `scale=720:1280` — БЕЗ сохранения пропорций. Для реального
проповедника это незаметно (резкий центр поверх), а для статичной картинки
на весь экран этот растянутый 16:9→9:16 блюр-фон и БЫЛ всей «расплющенной»
картинкой. Фикс: фон cover через force_original_aspect_ratio=increase+crop,
резкий передний план вписывается целиком (decrease), setsar=1 — квадратный
пиксель, чтобы ничто не растягивалось при показе.
"""
from pathlib import Path

SHORTS = Path("services/shorts_video_impl.py").read_text(encoding="utf-8")
MONTAGE = Path("services/render_clips_montage.py").read_text(encoding="utf-8")


def _blur_background_is_aspect_preserving(src: str) -> None:
    # фон блюра должен покрывать БЕЗ искажения
    assert "scale=720:1280:force_original_aspect_ratio=increase," in src
    assert "crop=720:1280,gblur=sigma=20,setsar=1[blurred]" in src
    # передний план — вписан целиком, тоже с квадратным пикселем
    assert "[fg]scale=720:1280:force_original_aspect_ratio=decrease,setsar=1[small]" in src
    # старый искажающий фон не должен вернуться
    assert "[bg]scale=720:1280,gblur=sigma=20[blurred]" not in src


def test_shorts_blur_background_not_squished():
    _blur_background_is_aspect_preserving(SHORTS)


def test_montage_blur_background_not_squished():
    _blur_background_is_aspect_preserving(MONTAGE)


def test_crop_zoom_path_unchanged_for_real_video():
    # Реальное видео (проповедник по центру) остаётся на crop_zoom — не трогаем.
    assert "crop=ih*9/16:ih:(iw-ih*9/16)/2:0" in SHORTS
