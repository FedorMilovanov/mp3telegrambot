#!/usr/bin/env python3
"""AUDIT R36 (запрос оператора: «пускай топовые красивые шрифты просто будут»).

Каждый прогон PDF логировал «PDF fonts: директория не найдена: …\\services\\
fonts» и откатывался на системные шрифты. Теперь премиум-шрифты (Merriweather
для тела, Manrope для заголовков/UI) забандлены статическими начертаниями в
services/fonts/ — PDF рендерится одинаково красиво на любой машине после
git pull, без установки шрифтов в систему.

Тест не зависит от fontTools (его нет в проде/CI): проверяем наличие файлов
и что _build_font_face_css() их подхватывает с верными family/weight.
"""
import re
from pathlib import Path

from services.pdf_generator import (
    PDF_BODY_FONT,
    PDF_SANS_FONT,
    _build_font_face_css,
    _parse_font_filename,
)

FONTS_DIR = Path("services/fonts")
EXPECTED = {
    "Merriweather-Regular.ttf": ("Merriweather", 400),
    "Merriweather-Bold.ttf": ("Merriweather", 700),
    "Manrope-Regular.ttf": ("Manrope", 400),
    "Manrope-Medium.ttf": ("Manrope", 500),
    "Manrope-SemiBold.ttf": ("Manrope", 600),
    "Manrope-Bold.ttf": ("Manrope", 700),
    "Manrope-ExtraBold.ttf": ("Manrope", 800),
}


def test_fonts_dir_exists_and_has_expected_faces():
    assert FONTS_DIR.is_dir(), "services/fonts отсутствует — PDF снова упадёт на системные шрифты"
    for name in EXPECTED:
        p = FONTS_DIR / name
        assert p.exists(), f"нет {name}"
        assert p.stat().st_size > 20_000, f"{name} подозрительно мал — битый файл?"


def test_filenames_parse_to_expected_family_and_weight():
    for name, (family, weight) in EXPECTED.items():
        fam, w, style = _parse_font_filename(name)
        assert fam == family and w == weight and style == "normal", f"{name} -> {(fam, w, style)}"


def test_configured_pdf_fonts_are_present():
    # PDF ссылается на эти имена в CSS — они обязаны быть в бандле.
    fams = {_parse_font_filename(p.name)[0] for p in FONTS_DIR.glob("*.ttf")}
    assert PDF_BODY_FONT in fams, f"{PDF_BODY_FONT} не забандлен"
    assert PDF_SANS_FONT in fams, f"{PDF_SANS_FONT} не забандлен"


def test_font_face_css_picks_up_bundle():
    css = _build_font_face_css()
    assert css.count("@font-face") == len(EXPECTED)
    fams = set(re.findall(r'font-family: "([^"]+)"', css))
    assert {"Merriweather", "Manrope"} <= fams
    # у обоих семейств должно быть регулярное и жирное начертание
    assert "font-weight: 400" in css and "font-weight: 700" in css
