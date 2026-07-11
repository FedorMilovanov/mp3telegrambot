"""
pdf_generator.py — v2.8.1 FINAL — reading-first PDF из Telegraph страниц.

Архитектура:
- Single-pass anchoring: якоря h3 и TOC генерируются ОДНИМ проходом
  через _enrich_section, гарантируя синхронизацию ID ↔ ссылок
- _fetch_telegraph возвращает str (чистый HTML), TOC строится позже
- _enrich_section: единая точка обогащения (якоря + TOC + таймкоды)
- _build_html: порядок операций — enrich → toc → parts
- Нет мутации входных данных
- Заголовки обрабатываются один раз (data-title в _polish_html)

Зависимости:
    pip install pdfkit requests beautifulsoup4
    wkhtmltopdf — https://wkhtmltopdf.org/downloads.html
    Опционально: pip install weasyprint
"""

from __future__ import annotations

__all__ = [
    "generate_sermon_pdf",
    "generate_sermon_pdf_async",
    "clear_telegraph_cache",
    "invalidate_telegraph_url",
    "SECTIONS",
]

import asyncio
import html as html_module
import logging
import os
import platform as _platform
import re
import shutil as _shutil
import subprocess as _subprocess
import tempfile
import threading
import time as _time
from collections import OrderedDict
from pathlib import Path
from typing import (
    Awaitable,
    Callable,
    Dict,
    Generic,
    Hashable,
    List,
    Optional,
    Tuple,
    TypeVar,
)
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

try:
    import requests
except ImportError:
    raise ImportError(
        "pdf_generator requires 'requests': pip install requests"
    ) from None

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  1. Precompiled regex constants
# ══════════════════════════════════════════════════════════════

_BIBLE_BOOKS_PATTERN = (
    r"(?:"
    r"[1-3]\s*(?:Цар|Пар|Кор|Фес|Тим|Пет|Ин|Макк)|"
    r"(?:Быт|Исх|Лев|Чис|Втор|Нав|Суд|Руфь?|Езд|Неем|Есф|Иов|Пс|Притч?|"
    r"Еккл?|Песн?|Ис|Иер|Плач|Иез|Дан|Ос|Иоил|Ам|Авд|Ион|Мих|Наум|Авв|"
    r"Соф|Агг|Зах|Мал|Мф|Мк|Лк|Ин|Деян|Иак|Иуд|Откр|Рим|Гал|Еф|Флп|"
    r"Кол|Евр|Флм|Тит)"
    r")"
)
_BIBLE_REF_BEFORE_TC_RE = re.compile(
    _BIBLE_BOOKS_PATTERN + r"\.?\s*\d*\s*$"
)

_STRIP_INVISIBLE_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\u2600-\u27BF"
    "\u2300-\u23FF"
    "\u25A0-\u25FF"
    "\u2460-\u24FF"
    "\u200B-\u200D"
    "\uFE0E-\uFE0F"
    "\u205F-\u2060"
    "\u2066-\u2069"
    "\u00AD"
    "]",
    flags=re.UNICODE,
)
_BOX_DRAWING_RE = re.compile(r"[\u2500-\u257F\u2580-\u259F]")
_ARROWS_RE = re.compile(r"[\u2190-\u21FF\u2900-\u297F\u2B00-\u2BFF]")
_SPECIAL_SPACES_RE = re.compile(r"[\u2000-\u200A\u202F\u2009]")
_TIMECODE_RE = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?)")

# _normalize_spaces
_RE_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_RE_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")
_RE_PUNCT_GLUE = re.compile(
    r'([,.;:!?\u00BB"\u201D\)])([А-Яа-яЁёA-Za-z])'
)
_RE_PAREN_OPEN = re.compile(r"([А-Яа-яЁёA-Za-z0-9])\(")
_RE_PAREN_CLOSE = re.compile(r"\)([А-Яа-яЁёA-Za-z])")
_RE_EM_DASH = re.compile(r"\s*—\s*")
_RE_EN_DASH = re.compile(r"(?<!\d)\s*–\s*(?!\d)")
_RE_MULTI_NEWLINE = re.compile(r"\n{3,}")

# Glued words
_PREPS = (
    r"и|в|к|с|о|а|у|на|не|но|за|из|об|от|по|до"
    r"|без|для|при|над|под|или|что|как|так"
)
_GLUED_WORDS_RE = re.compile(
    rf"(?<![а-яёА-ЯЁa-zA-Z])({_PREPS})([А-ЯЁ][а-яё])"
)

# Heading cleanup
_HEADING_BOILERPLATE = (
    "Doctrinal Stakes",
    "Bible Study",
    "Reading-first PDF",
    "Digital Reading Edition",
)
_RE_MULTI_SPACE_SIMPLE = re.compile(r"\s{2,}")
_RE_TRAILING_PREP = re.compile(
    r"\s+(?:и|а|но|или|да|с|в|к|на|по|за|из|о|об)\s*$"
)

# Href parsing — два regex для точного матча кавычек
_HREF_DOUBLE_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
_HREF_SINGLE_RE = re.compile(r"href='([^']+)'", re.IGNORECASE)

# Tag matching
_RE_TAG_SPLIT = re.compile(r"(<[^>]+>)")
_RE_A_OPEN = re.compile(r"<a[\s>]")
_RE_SCRIPT = re.compile(
    r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE,
)
_RE_STYLE = re.compile(
    r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE,
)
_RE_EMPTY_P = re.compile(r"<p[^>]*>\s*</p>", re.IGNORECASE)
_RE_BACK_TO_TOC_CLASS = re.compile(
    r'<a[^>]*class=["\']?back-to-toc["\']?[^>]*>.*?</a>',
    re.DOTALL | re.IGNORECASE,
)
_RE_BACK_TO_TOC_TEXT = re.compile(
    r"<a[^>]*>\s*\^?\s*К содержанию\s*</a>",
    re.DOTALL | re.IGNORECASE,
)
_RE_BACK_TO_TOC_BARE = re.compile(
    r">\s*К содержанию\s*<", re.IGNORECASE,
)
_RE_CLOSE_TAG_GLUE = re.compile(
    r"(</(?:b|strong|i|em|a|span)>)([А-Яа-яЁёA-Za-z])"
)
_RE_OPEN_TAG_GLUE = re.compile(
    r"([А-Яа-яЁёA-Za-z])(<(?:b|strong|i|em|a|span)\b)"
)
_RE_H3 = re.compile(
    r"<h3[^>]*>(.*?)</h3>", re.DOTALL | re.IGNORECASE,
)
_RE_H3_WITH_ATTRS = re.compile(
    r"<h3([^>]*)>(.*?)</h3>", re.DOTALL | re.IGNORECASE,
)
_RE_DATA_TITLE = re.compile(r'data-title="([^"]*)"')
_RE_BLOCK_P_H4 = re.compile(
    r"<(p|h4)([^>]*)>(.*?)</\1>", re.DOTALL | re.IGNORECASE,
)
_RE_BLOCKQUOTE = re.compile(
    r"<(blockquote)([^>]*)>((?:(?!<blockquote).)*?)</blockquote>",
    re.DOTALL | re.IGNORECASE,
)
_RE_TIMECODE_STAR_PREFIX = re.compile(r"(?<!\w)\*\s*(\d{1,2}:\d{2})")
_RE_TIMECODE_STAR_SUFFIX = re.compile(
    r"(\d{1,2}:\d{2}(?::\d{2})?)\s*\\\*"
)
_RE_TIMECODE_STAR_AFTER = re.compile(
    r"(\d{1,2}:\d{2}(?::\d{2})?)\s+\*(?=[\s<])"
)
_RE_STRIP_TAGS = re.compile(r"<[^>]+>")
_RE_MULTI_INLINE_SPACE = re.compile(r" {2,}")
# PART5: sanitize_cli теперь использует translate(), regex для этого не нужен.
_CLI_UNSAFE_TRANSLATION = str.maketrans({ch: " " for ch in "\"\'\\\n\r\t`$|;&<>"})

# Telegraph article parsing
_RE_ARTICLE = re.compile(
    r"<article[^>]*>(.*?)</article>", re.DOTALL | re.IGNORECASE,
)
_RE_ADDRESS = re.compile(
    r"<address[^>]*>.*?</address>", re.DOTALL | re.IGNORECASE,
)
_RE_HEADER = re.compile(
    r"<header[^>]*>.*?</header>", re.DOTALL | re.IGNORECASE,
)

# _extract_h3_parts: platform detection
_RE_PLATFORM_YT = re.compile(r"\bYouTube\b")
_RE_PLATFORM_RT = re.compile(r"\bRuTube\b")
_RE_PLATFORM_VK = re.compile(r"\bVK\b")
_RE_H3_SEPARATOR = re.compile(
    r"\s*\u2014\s*(?:YouTube|RuTube|VK|\d{1,2}:\d{2})"
)
_RE_URL_SCHEME = re.compile(r"https?://", re.IGNORECASE)


# ══════════════════════════════════════════════════════════════
#  2. Optional dependencies
# ══════════════════════════════════════════════════════════════

try:
    import pdfkit
    _HAS_PDFKIT = True
except ImportError:
    _HAS_PDFKIT = False
    pdfkit = None  # type: ignore[assignment]

try:
    from bs4 import BeautifulSoup
    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False
    BeautifulSoup = None  # type: ignore[assignment,misc]

try:
    from weasyprint import HTML as WeasyprintHTML
    _HAS_WEASYPRINT = True
except ImportError:
    _HAS_WEASYPRINT = False
    WeasyprintHTML = None  # type: ignore[assignment,misc]


# ══════════════════════════════════════════════════════════════
#  3. wkhtmltopdf finder
# ══════════════════════════════════════════════════════════════

def _find_wkhtmltopdf() -> str:
    """Ищет бинарник wkhtmltopdf: env → Windows default → PATH."""
    env_path = os.environ.get("WKHTMLTOPDF_PATH", "").strip()
    if env_path and Path(env_path).exists():
        return env_path
    if _platform.system() == "Windows":
        win_path = r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe"
        if Path(win_path).exists():
            return win_path
    return _shutil.which("wkhtmltopdf") or ""


WKHTMLTOPDF_PATH: str = _find_wkhtmltopdf()


# ══════════════════════════════════════════════════════════════
#  4. Fonts config
# ══════════════════════════════════════════════════════════════

PDF_BODY_FONT: str = (
    os.environ.get("PDF_BODY_FONT", "Merriweather").strip()
    or "Merriweather"
)
PDF_SANS_FONT: str = (
    os.environ.get("PDF_SANS_FONT", "Manrope").strip()
    or "Manrope"
)


# ══════════════════════════════════════════════════════════════
#  5. Thread-safe typed LRU cache
# ══════════════════════════════════════════════════════════════

_KT = TypeVar("_KT", bound=Hashable)
_VT = TypeVar("_VT")


class _LRUCache(Generic[_KT, _VT]):
    """Потокобезопасный LRU-кеш на базе OrderedDict (композиция)."""

    def __init__(self, maxsize: int = 64) -> None:
        self._data: OrderedDict[_KT, _VT] = OrderedDict()
        self._max = maxsize
        self._lock = threading.Lock()

    def get_cached(self, key: _KT) -> Optional[_VT]:
        """Возвращает значение по ключу или None, сдвигая в конец."""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        return None

    def put(self, key: _KT, value: _VT) -> None:
        """Добавляет элемент, вытесняя старейший при переполнении."""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear_safe(self) -> None:
        """Thread-safe полная очистка."""
        with self._lock:
            self._data.clear()

    def delete(self, key: _KT) -> bool:
        """Thread-safe удаление одного ключа. Возвращает True если ключ был в кеше."""
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
        return False


_TELEGRAPH_CACHE_SIZE: int = int(os.environ.get("PDF_TELEGRAPH_CACHE_SIZE", "64"))
_TELEGRAPH_CACHE: _LRUCache[str, str] = _LRUCache(_TELEGRAPH_CACHE_SIZE)


def clear_telegraph_cache() -> None:
    """Thread-safe очистка кеша Telegraph-страниц."""
    _TELEGRAPH_CACHE.clear_safe()


def invalidate_telegraph_url(url: str) -> None:
    """#105: инвалидирует одну запись кеша Telegraph по URL (после успешного editPage)."""
    removed = _TELEGRAPH_CACHE.delete(url)
    if removed:
        logger.debug("Telegraph cache invalidated for URL: %s", url)


# ══════════════════════════════════════════════════════════════
#  6. Safe progress callback
# ══════════════════════════════════════════════════════════════

ProgressCallback = Optional[Callable[[str, int], Awaitable[None]]]


async def _safe_progress(
    cb: ProgressCallback,
    stage: str,
    pct: int,
) -> None:
    """Вызывает callback; при ошибке — логирует и продолжает."""
    if cb:
        try:
            await cb(stage, pct)
        except Exception as exc:
            logger.debug("progress_callback error: %s", exc)


# ══════════════════════════════════════════════════════════════
#  7. Section metadata
# ══════════════════════════════════════════════════════════════

_SECTION_KEYS: Tuple[str, ...] = (
    "synopsis", "study", "reflection", "terms",
)
_SECTION_ORDER: Dict[str, int] = {
    k: i for i, k in enumerate(_SECTION_KEYS)
}

SECTIONS: Dict[str, Dict[str, str]] = {
    "synopsis": {
        "roman": "I",
        "color": "#345E53",
        "label": "Конспект",
        "intro": (
            "Структурная карта материала"
            " для последовательного чтения."
        ),
    },
    "study": {
        "roman": "II",
        "color": "#375C83",
        "label": "Разбор материала",
        "intro": (
            "Богословская, экзегетическая"
            " и понятийная глубина материала."
        ),
    },
    "reflection": {
        "roman": "III",
        "color": "#6A4E85",
        "label": "Размышление и применение",
        "intro": (
            "Пасторское сопровождение: диагностика сердца,"
            " контрасты и молитвенный отклик."
        ),
    },
    "terms": {
        "roman": "IV",
        "color": "#7A5C37",
        "label": "Термины",
        "intro": (
            "Понятия, Писание, переводческие наблюдения"
            " и словарные заметки."
        ),
    },
}


# ══════════════════════════════════════════════════════════════
#  8. Font helpers
# ══════════════════════════════════════════════════════════════

_WEIGHT_MAP: Dict[str, int] = {
    "thin": 100, "hairline": 100,
    "extralight": 200, "ultralight": 200,
    "light": 300,
    "regular": 400, "normal": 400,
    "medium": 500,
    "semibold": 600, "demibold": 600,
    "bold": 700,
    "extrabold": 800, "ultrabold": 800,
    "black": 900, "heavy": 900,
}


def _path_to_file_uri(p: Path) -> str:
    """Конвертирует Path в file:// URI для CSS @font-face."""
    resolved = p.resolve()
    if _platform.system() == "Windows":
        path_str = "/" + str(resolved).replace("\\", "/")
    else:
        path_str = str(resolved)
    return "file://" + quote(path_str, safe="/:")


def _parse_font_filename(filename: str) -> Tuple[str, int, str]:
    """Извлекает (family, weight, style) из имени файла шрифта."""
    stem = Path(filename).stem
    parts = re.split(r"[-_]", stem)
    family = parts[0] if parts else stem
    family = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", family)
    family = re.sub(r"(?<=[A-Z]{2})(?=[A-Z][a-z])", " ", family)
    weight = 400
    style = "normal"
    for part in parts[1:]:
        low = part.lower().replace("italic", "")
        if low in _WEIGHT_MAP:
            weight = _WEIGHT_MAP[low]
        if "italic" in part.lower():
            style = "italic"
    return family, weight, style


_font_face_cache: Dict[str, str] = {}
_font_face_lock = threading.Lock()  # FIXED #108: защита от double-compute при параллельных PDF


def _build_font_face_css(fonts_dir: Optional[str] = None) -> str:
    """Генерирует CSS @font-face для всех шрифтов в директории."""
    if not fonts_dir:
        fonts_dir = os.environ.get("PDF_FONTS_DIR", "").strip()
    if not fonts_dir:
        fonts_dir = str(Path(__file__).parent / "fonts")
    with _font_face_lock:
        if fonts_dir in _font_face_cache:
            return _font_face_cache[fonts_dir]
    d = Path(fonts_dir)
    if not d.exists():
        logger.warning("PDF fonts: директория не найдена: %s", d)
        return ""
    css_parts: List[str] = []
    for f in sorted(d.iterdir()):
        # AUDIT R39: пропускаем каталоги/не-файлы с «шрифтовым» суффиксом —
        # иначе @font-face указывал бы на директорию и wkhtmltopdf ронял грань.
        if not f.is_file() or f.suffix.lower() not in (".ttf", ".otf", ".woff", ".woff2"):
            continue
        family, weight, style = _parse_font_filename(f.name)
        fmt = {
            ".ttf": "truetype", ".otf": "opentype",
            ".woff": "woff", ".woff2": "woff2",
        }.get(f.suffix.lower(), "truetype")
        uri = _path_to_file_uri(f).replace('"', "%22")
        css_parts.append(
            f'@font-face {{\n'
            f'  font-family: "{family}";\n'
            f'  src: url("{uri}") format("{fmt}");\n'
            f'  font-weight: {weight};\n'
            f'  font-style: {style};\n'
            f'}}'
        )
    if css_parts:
        logger.info(
            "PDF fonts: загружено %d начертаний из %s",
            len(css_parts), d,
        )
    result = "\n".join(css_parts)
    with _font_face_lock:
        _font_face_cache[fonts_dir] = result
    return result


# ══════════════════════════════════════════════════════════════
#  9. CSS template
# ══════════════════════════════════════════════════════════════
#
# Footer strategy:
#   @bottom-center — используется WeasyPrint (CSS Paged Media).
#   wkhtmltopdf ИГНОРИРУЕТ CSS @page footer при наличии CLI-опций
#   --footer-center, поэтому дублирования на практике нет.

CSS_TEMPLATE = r"""
{font_face_css}

@page {{
    size: A4;
    margin: 16mm 16mm 18mm 16mm;
    @bottom-center {{
        content: counter(page) " / " counter(pages);
        font-family: Arial, sans-serif;
        font-size: 8pt;
        color: #9a9a9a;
    }}
}}

* {{ box-sizing: border-box; }}

html, body {{
    margin: 0; padding: 0;
    background: #fff;
}}

body.reading-pdf {{
    font-family: "{body_font}", "PT Serif", Georgia, "Times New Roman", serif;
    font-size: 10.9pt;
    line-height: 1.78;
    color: #171717;
    background: #fff;
    text-rendering: optimizeLegibility;
    -webkit-font-smoothing: antialiased;
    font-kerning: normal;
    font-variant-ligatures: common-ligatures;
    hyphens: auto;
    -webkit-hyphens: auto;
    -ms-hyphens: auto;
}}

.cover {{
    padding-top: 24mm; padding-bottom: 10mm;
    page-break-after: always;
}}
.cover-inner {{ max-width: 156mm; margin: 0 auto; }}
.cover-rule {{
    width: 100%; height: 3px;
    background: #2a2a2a; margin-bottom: 8mm;
}}
.cover-title {{
    font-family: "{sans_font}", "Inter", "Segoe UI", Arial, sans-serif;
    font-size: 32pt; line-height: 1.06; font-weight: 800;
    color: #111; margin-bottom: 7mm; letter-spacing: -0.5px;
    hyphens: auto; -webkit-hyphens: auto; -ms-hyphens: auto;
}}
.cover-divider {{
    width: 56px; height: 3px;
    background: #2a2a2a; margin-bottom: 6mm;
}}
.cover-author {{
    font-family: "{sans_font}", "Inter", "Segoe UI", Arial, sans-serif;
    font-size: 13pt; font-weight: 600; color: #3a3a3a;
    margin-bottom: 1.5mm; letter-spacing: 0.1px;
}}
.cover-duration {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 8.8pt; color: #8d8d8d; margin-bottom: 9mm;
}}
.cover-blurb {{
    max-width: 138mm; font-size: 10.7pt; line-height: 1.75;
    color: #484848; margin-bottom: 9mm;
    hyphens: auto; -webkit-hyphens: auto; -ms-hyphens: auto;
}}
.cover-includes {{
    border-top: 1px solid #e7e1d8; padding-top: 5mm;
}}
.cover-includes-label {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 7.8pt; font-weight: 800;
    letter-spacing: 1.8px; text-transform: uppercase;
    color: #8f8f8f; margin-bottom: 3mm;
}}
.cover-includes-list {{ margin: 0; padding: 0; list-style: none; }}
.cover-includes-item {{
    margin: 0 0 2mm 0;
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 10.6pt; font-weight: 700; color: #222;
}}

.toc-page {{ padding-top: 8mm; padding-bottom: 4mm; }}
.toc-inner {{ max-width: 170mm; margin: 0 auto; }}
.toc-heading {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 22pt; line-height: 1.12; font-weight: 800; color: #111;
    margin: 0 0 10mm 0; padding-bottom: 4mm;
    border-bottom: 2px solid #2a2a2a;
}}
.toc-part {{ margin: 0 0 8mm 0; page-break-inside: avoid; }}
.toc-part-head {{
    border-top: 1px solid #e7e1d8;
    padding-top: 4.5mm; margin-bottom: 3.5mm;
}}
.toc-part-kicker {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 7.6pt; font-weight: 800;
    letter-spacing: 1.6px; text-transform: uppercase;
    color: #7f7f7f; margin-bottom: 1.5mm;
}}
.toc-part-title {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 14pt; font-weight: 800; line-height: 1.18;
    color: #111; margin-bottom: 1.5mm;
}}
.toc-list {{ margin: 0; padding: 0 0 0 18px; }}
.toc-item {{ margin: 0 0 3mm 0; page-break-inside: avoid; }}
.toc-link {{ text-decoration: none; color: inherit; }}
.toc-item-title {{
    display: block; font-size: 10.7pt; line-height: 1.42;
    color: #262626; font-weight: 700;
    hyphens: auto; -webkit-hyphens: auto; -ms-hyphens: auto;
}}

.source-block {{
    margin-top: 8mm; border-top: 1px solid #ece5db;
    padding-top: 4.5mm;
}}
.source-label {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 7.8pt; font-weight: 800;
    letter-spacing: 1.6px; text-transform: uppercase;
    color: #8a8a8a; margin-bottom: 2.8mm;
}}
.source-links {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 8.7pt; line-height: 1.5; color: #8d8d8d;
}}
.source-links a {{
    color: #4a4a4a; text-decoration: none; margin-right: 10px;
}}

.part-page {{ page-break-before: always; }}
.part-opener {{
    margin: 0 0 10mm 0; padding-top: 5mm;
    page-break-inside: avoid;
}}
.part-opener-inner {{ max-width: 170mm; margin: 0 auto; }}
.part-line {{ width: 100%; height: 2px; margin: 0 0 5mm 0; }}
.part-kicker {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 7.8pt; font-weight: 800;
    letter-spacing: 2px; text-transform: uppercase;
    color: #8a8a8a; margin-bottom: 1.5mm;
}}
.part-title {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 20pt; font-weight: 800; line-height: 1.1;
    color: #111; margin-bottom: 2.5mm;
}}
.part-intro {{
    max-width: 146mm; font-size: 10.2pt; line-height: 1.60;
    color: #595959; margin-bottom: 4.5mm;
    hyphens: auto; -webkit-hyphens: auto; -ms-hyphens: auto;
}}
.part-meta {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 8.6pt; color: #888;
}}

.local-toc {{ margin: 0 0 9mm 0; padding: 0 0 0 18px; }}
.local-toc li {{ margin: 0 0 2.5mm 0; }}
.local-toc a {{ text-decoration: none; color: #303030; font-weight: 700; }}

.section-content-wrap {{ max-width: 170mm; margin: 0 auto; }}
.tg-content {{ color: #181818; }}
.tg-content h3 {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 13.8pt; font-weight: 800; line-height: 1.28;
    letter-spacing: -0.1px; color: #121212;
    margin: 6mm 0 4mm 0; padding: 0 0 2.5mm 0;
    border-bottom: 1px solid #ddd6ca;
    page-break-inside: avoid; page-break-after: avoid;
}}
.tg-content h3 a.pl {{
    color: #2d5b8e; text-decoration: none; font-weight: 800;
}}
.tg-content h4 {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 11.2pt; font-weight: 800; line-height: 1.34;
    color: #262626; margin: 6.5mm 0 2.5mm 0;
    page-break-inside: avoid; page-break-after: avoid;
}}
.tg-content p {{
    margin: 0 0 4mm 0; font-size: 10.95pt; line-height: 1.78;
    text-align: left; orphans: 3; widows: 3;
    hyphens: auto; -webkit-hyphens: auto; -ms-hyphens: auto;
    overflow-wrap: break-word; word-wrap: break-word;
}}
.tg-content b, .tg-content strong {{ font-weight: 700; color: #111; }}
.tg-content i, .tg-content em {{ font-style: italic; color: #4f4f4f; }}
.tg-content blockquote {{
    margin: 5.5mm 0 6mm 0; padding: 3mm 5mm 3mm 8mm;
    border-left: 2.5px solid #c8bfb2; background: #f5f0e8;
    color: #454545; font-style: italic;
    font-size: 10.45pt; line-height: 1.70;
    page-break-inside: avoid; orphans: 3; widows: 3;
    hyphens: auto; -webkit-hyphens: auto; -ms-hyphens: auto;
    overflow-wrap: break-word; word-wrap: break-word;
}}
.tg-content blockquote p {{
    margin-bottom: 2mm; text-align: left; orphans: 3; widows: 3;
}}
.tg-content blockquote p:last-child {{ margin-bottom: 0; }}
.tg-content hr {{
    border: none; border-top: 1px solid #ece5da;
    margin: 6.5mm 0 3.5mm 0;
}}
.tg-content a {{ color: #2d5b8e; text-decoration: none; }}
.tg-content figure {{ margin: 5mm 0; page-break-inside: avoid; }}
.tg-content figure img {{ max-width: 100%; height: auto; }}
.tg-content ul, .tg-content ol {{
    margin: 2mm 0 5mm; padding-left: 6mm; margin-left: 2mm;
}}
.tg-content li {{
    margin-bottom: 2mm; font-size: 10.95pt; line-height: 1.72;
    orphans: 3; widows: 3;
    hyphens: auto; -webkit-hyphens: auto; -ms-hyphens: auto;
    overflow-wrap: break-word; word-wrap: break-word;
}}
.tg-content li p {{ margin-bottom: 1.5mm; }}
.tg-content table {{
    width: 100%; border-collapse: collapse; margin: 5mm 0;
    font-size: 10pt; page-break-inside: avoid;
}}
.tg-content th, .tg-content td {{
    border: 1px solid #ddd6ca; padding: 2.5mm 3.5mm;
    text-align: left; vertical-align: top;
    overflow-wrap: break-word; word-wrap: break-word;
}}
.tg-content th {{
    background: #f5f0e8;
    font-family: "{sans_font}", Arial, sans-serif;
    font-weight: 700; font-size: 9.5pt;
}}
.tg-content tr:nth-child(even) td {{ background: #faf7f2; }}

a.tc {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 9pt; font-weight: 700;
    color: #8c3d00; background: #fef3e8;
    padding: 0.15mm 1.8mm; border: 1px solid #f0d5b8;
    text-decoration: none; white-space: nowrap; letter-spacing: .2px;
}}
a.bt {{
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 7.8pt; font-weight: 600;
    color: #cec9c1; text-decoration: none; margin-left: 7px;
}}

.doc-end-vignette {{
    margin-top: 14mm; text-align: center;
    font-size: 9pt; color: #cec9c1; letter-spacing: 8px;
}}
.doc-end {{
    margin-top: 10mm; border-top: 1px solid #e8e2d8;
    padding-top: 4mm;
    font-family: "{sans_font}", Arial, sans-serif;
    font-size: 8pt; color: #9a9a9a;
}}
"""


# ══════════════════════════════════════════════════════════════
#  10. Text helpers
# ══════════════════════════════════════════════════════════════

def _esc(t: str) -> str:
    """Экранирует спецсимволы HTML (&, <, >, \", ')."""
    return (
        (t or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _esc_safe(t: str) -> str:
    """unescape → escape: безопасно для вставки в HTML-атрибуты и текст."""
    return _esc(html_module.unescape(t or ""))


def _sanitize_cli(t: str) -> str:
    """Очищает строку для безопасной передачи в CLI-аргумент.

    Раньше это делалось regex-ом. Там не было практического ReDoS
    (обычный character class), но translate() проще, быстрее и исключает
    сам класс regex-рисков для CLI sanitizing.
    """
    return (t or "").translate(_CLI_UNSAFE_TRANSLATION).strip()[:120]


def _safe_url(url: str) -> str:
    """Возвращает URL только если начинается с http(s)://."""
    if url and _RE_URL_SCHEME.match(url):
        return url
    return ""


def _is_bible_ref_before(text_before: str) -> bool:
    """Проверяет, заканчивается ли текст ссылкой на библейскую книгу."""
    return bool(_BIBLE_REF_BEFORE_TC_RE.search(text_before.strip()))


def _find_all_hrefs(html_str: str) -> List[str]:
    """Извлекает все href из HTML, поддерживая оба типа кавычек."""
    return (
        _HREF_DOUBLE_RE.findall(html_str or "")
        + _HREF_SINGLE_RE.findall(html_str or "")
    )


def _clean_text_node(text: str) -> str:
    """Удаляет невидимые символы, рамки, стрелки; нормализует пробелы."""
    text = _STRIP_INVISIBLE_RE.sub("", text)
    text = _BOX_DRAWING_RE.sub("", text)
    text = _ARROWS_RE.sub("", text)
    text = _SPECIAL_SPACES_RE.sub(" ", text)
    return text


def _strip_decorations_from_text_nodes(html_str: str) -> str:
    """Чистит текстовые узлы HTML, сохраняя теги нетронутыми."""
    parts = _RE_TAG_SPLIT.split(html_str)
    return "".join(
        part if part.startswith("<") else _clean_text_node(part)
        for part in parts
    )


def _normalize_spaces(text: str) -> str:
    """Нормализует пробелы, пунктуацию, тире и переносы строк."""
    if not text:
        return ""
    text = text.replace("\u00A0", " ")
    text = _SPECIAL_SPACES_RE.sub(" ", text)
    text = _RE_MULTI_SPACE.sub(" ", text)
    text = _RE_SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _RE_PUNCT_GLUE.sub(r"\1 \2", text)
    text = _RE_PAREN_OPEN.sub(r"\1 (", text)
    text = _RE_PAREN_CLOSE.sub(r") \1", text)
    text = _RE_EM_DASH.sub(" \u2014 ", text)
    text = _RE_EN_DASH.sub(" \u2014 ", text)
    text = _RE_MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


def _repair_glued_words(text: str) -> str:
    """Вставляет пробел между предлогом и словом (наИсход → на Исход)."""
    if not text:
        return text
    return _GLUED_WORDS_RE.sub(r"\1 \2", text)


def _clean_text_plain(text: str) -> str:
    """Полная очистка текста: символы → пробелы → склейки → пробелы."""
    text = _clean_text_node(text or "")
    text = _normalize_spaces(text)
    text = _repair_glued_words(text)
    return _normalize_spaces(text)


def _clean_text_with_html(text: str) -> str:
    """Чистит текстовые фрагменты внутри HTML, сохраняя теги."""
    if not text:
        return ""
    parts = _RE_TAG_SPLIT.split(text)
    return "".join(
        part if part.startswith("<") else _clean_text_plain(part)
        for part in parts
    )


def _clean_heading(text: str) -> str:
    """Очищает заголовок: boilerplate, лишние пробелы, висячие предлоги."""
    if not text:
        return ""
    text = _clean_text_plain(text)
    for p in _HEADING_BOILERPLATE:
        text = text.replace(p, "")
    text = _RE_MULTI_SPACE_SIMPLE.sub(" ", text).strip(" \u00B7\u2014-")
    text = _RE_TRAILING_PREP.sub("", text)
    return text.strip()


# ══════════════════════════════════════════════════════════════
#  11. Timecode / URL helpers
# ══════════════════════════════════════════════════════════════

def _tc_to_seconds(tc: str) -> int:
    """Конвертирует таймкод '1:23' или '1:23:45' в секунды."""
    parts = tc.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        pass
    return 0


def _replace_query_param(url: str, key: str, value: str) -> str:
    """Заменяет / добавляет query-параметр, сохраняя остальные."""
    try:
        sp = urlsplit(url)
        params = [
            (qk, qv)
            for qk, qv in parse_qsl(sp.query, keep_blank_values=True)
            if qk != key
        ]
        params.append((key, value))
        return urlunsplit((
            sp.scheme, sp.netloc, sp.path,
            urlencode(params), sp.fragment,
        ))
    except Exception:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{key}={value}"


def _video_ts_url(base: str, seconds: int) -> str:
    """Добавляет таймкод к видео-URL (YouTube / RuTube / VK)."""
    base = _safe_url(base)
    if not base:
        return ""
    if "youtube.com" in base or "youtu.be" in base:
        return _replace_query_param(base, "t", f"{seconds}s")
    if "rutube.ru" in base:
        return _replace_query_param(base, "t", str(seconds))
    if "vk.com/" in base or "vkvideo.ru/" in base:
        return _replace_query_param(base, "t", f"{seconds}s")
    return base


def _enrich_url_with_time(url: str, time_str: str) -> str:
    """Обогащает URL таймкодом, если таймкод валидный и > 0."""
    if not url or not time_str:
        return url
    secs = _tc_to_seconds(time_str)
    return _video_ts_url(url, secs) if secs > 0 else url


def _primary_video_url(source_links: Dict[str, str]) -> str:
    """Возвращает приоритетную ссылку: YouTube → RuTube → VK."""
    return (
        source_links.get("youtube")
        or source_links.get("rutube")
        or source_links.get("vk")
        or ""
    )


def _linkify_timecodes(html_str: str, video_url: str) -> str:
    """Оборачивает таймкоды (вне тегов <a>) в ссылки на видео."""
    if not html_str or not video_url:
        return html_str

    parts = _RE_TAG_SPLIT.split(html_str)
    depth_a = 0
    result: List[str] = []
    # V3-P0: раньше здесь накапливался весь plain-text документа
    # (cumulative_text), что давало лишнюю память на больших Telegraph/PDF.
    # Для проверки Bible-ref перед таймкодом достаточно короткого хвоста.
    context_tail = ""

    def _remember_text(text: str) -> None:
        nonlocal context_tail
        if text:
            context_tail = (context_tail + text)[-120:]

    for seg in parts:
        if seg.startswith("<"):
            low = seg.lower()
            if _RE_A_OPEN.match(low):
                depth_a += 1
            elif low.startswith("</a"):
                depth_a = max(0, depth_a - 1)
            result.append(seg)
        elif depth_a > 0:
            _remember_text(seg)
            result.append(seg)
        else:
            ctx = context_tail

            def _repl(m: re.Match, _ctx=ctx) -> str:
                tc = m.group(1)
                local_pre = m.string[max(0, m.start() - 50):m.start()]
                pre = (_ctx + local_pre)[-50:]
                if _is_bible_ref_before(pre):
                    return tc
                secs = _tc_to_seconds(tc)
                url = _video_ts_url(video_url, secs)
                if url:
                    return (
                        f'<a class="tc" href="{_esc(url)}" title="{tc}">'
                        f'{tc}</a>'
                    )
                return tc

            result.append(_TIMECODE_RE.sub(_repl, seg))
            _remember_text(seg)
    return "".join(result)


# ══════════════════════════════════════════════════════════════
#  12. HTTP fetch
# ══════════════════════════════════════════════════════════════

def _fetch_url(
    url: str, retries: int = 1,
) -> Optional[requests.Response]:
    """Загружает URL с повторами. Устанавливает encoding явно."""
    for attempt in range(retries + 1):
        try:
            r = requests.get(
                url, timeout=20,
                headers={"User-Agent": "ReadingPDF/2.8.1"},
            )
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r
        except Exception as exc:
            if attempt >= retries:
                logger.warning(
                    "HTTP fetch %s: %s (после %d попыток)",
                    url, exc, retries + 1,
                )
                return None
            # #104: time.sleep в sync-функции внутри executor блокирует поток пула.
            # Используем минимальную задержку (0.1s) — только чтобы дать сети восстановиться;
            # длинный sleep (1s) не нужен, т.к. повторная попытка — последняя (retries=1).
            _time.sleep(0.1)
    return None  # pragma: no cover


# ══════════════════════════════════════════════════════════════
#  13. Telegraph HTML polishing (decomposed)
# ══════════════════════════════════════════════════════════════

def _extract_h3_parts(text: str) -> Tuple[str, str, str]:
    """
    Из raw-содержимого <h3> извлекает:
      - title: чистый текст без платформ и таймкодов
      - time_str: таймкод (если не библейская ссылка)
      - platforms_str: 'YouTube · RuTube'
    """
    raw = html_module.unescape(_RE_STRIP_TAGS.sub(" ", text or ""))
    raw = _RE_MULTI_INLINE_SPACE.sub(" ", raw).strip()
    raw = _clean_text_plain(raw)

    time_str = ""
    for m in _TIMECODE_RE.finditer(raw):
        pre_ctx = raw[:m.start()]
        if not _is_bible_ref_before(pre_ctx):
            time_str = m.group(1)
            break

    platforms: List[str] = []
    if _RE_PLATFORM_YT.search(raw):
        platforms.append("YouTube")
    if _RE_PLATFORM_RT.search(raw):
        platforms.append("RuTube")
    if _RE_PLATFORM_VK.search(raw):
        platforms.append("VK")

    sep = _RE_H3_SEPARATOR.search(raw)
    cleaned = raw[:sep.start()].strip() if sep else raw
    if time_str:
        cleaned = re.sub(
            r"\s+" + re.escape(time_str) + r"\s*$", "", cleaned,
        ).strip()
    cleaned = _clean_text_plain(cleaned)
    return cleaned, time_str, " \u00B7 ".join(platforms)


def _strip_dangerous_tags(html_str: str) -> str:
    """Удаляет <script> и <style> теги с содержимым."""
    html_str = _RE_SCRIPT.sub("", html_str)
    html_str = _RE_STYLE.sub("", html_str)
    return html_str


def _strip_soft_hyphens(html_str: str) -> str:
    """Удаляет мягкие переносы и артефакты «звёздочка у таймкода»."""
    html_str = html_str.replace("&shy;", "").replace("\u00ad", "")
    html_str = _RE_TIMECODE_STAR_PREFIX.sub(r" \1", html_str)
    html_str = _RE_TIMECODE_STAR_SUFFIX.sub(r"\1", html_str)
    html_str = _RE_TIMECODE_STAR_AFTER.sub(r"\1", html_str)
    return html_str


def _strip_navigation_links(html_str: str) -> str:
    """Удаляет ссылки «К содержанию», back-to-toc, пустые <p>."""
    html_str = _RE_EMPTY_P.sub("", html_str)
    html_str = _RE_BACK_TO_TOC_CLASS.sub("", html_str)
    html_str = _RE_BACK_TO_TOC_TEXT.sub("", html_str)
    html_str = _RE_BACK_TO_TOC_BARE.sub("><", html_str)
    return html_str


def _strip_leading_ol(html_str: str) -> str:
    """Удаляет <ol> перед первым <h3> (Telegraph-оглавление)."""
    hl = html_str.lower()
    h3p, olp = hl.find("<h3"), hl.find("<ol")
    if olp != -1 and (h3p == -1 or olp < h3p):
        depth = 1
        pos = olp + 3
        while pos < len(hl) and depth > 0:
            next_open = hl.find("<ol", pos)
            next_close = hl.find("</ol>", pos)
            if next_close == -1:
                break
            if next_open != -1 and next_open < next_close:
                depth += 1
                pos = next_open + 3
            else:
                depth -= 1
                if depth == 0:
                    html_str = html_str[:olp] + html_str[next_close + 5:]
                else:
                    pos = next_close + 5
    return html_str


def _fix_inline_spacing(html_str: str) -> str:
    """Добавляет пробелы между закрывающими/открывающими тегами и текстом."""
    html_str = _strip_decorations_from_text_nodes(html_str)
    html_str = _RE_CLOSE_TAG_GLUE.sub(r"\1 \2", html_str)
    html_str = _RE_OPEN_TAG_GLUE.sub(r"\1 \2", html_str)
    return html_str


def _enrich_h3_headings(html_str: str) -> str:
    """
    Обогащает <h3>: извлекает ссылки на видео, таймкоды.
    Сохраняет чистый заголовок в data-title для однократного
    использования в _enrich_section.
    Если заголовок пуст после очистки — тег удаляется.
    """
    def _fix_h3(m: re.Match) -> str:
        inner = m.group(1) or ""
        yt = rt = vk = ""
        for href in _find_all_hrefs(inner):
            href_safe = _safe_url(href)
            if not href_safe:
                continue
            if not yt and ("youtube.com" in href or "youtu.be" in href):
                yt = href_safe
            elif not rt and "rutube.ru" in href:
                rt = href_safe
            elif not vk and ("vk.com/" in href or "vkvideo.ru/" in href):
                vk = href_safe

        title, time_str, _ = _extract_h3_parts(inner)
        title = _clean_heading(title)
        if not title:
            return "<hr>"

        yt = _enrich_url_with_time(yt, time_str)
        rt = _enrich_url_with_time(rt, time_str)
        vk = _enrich_url_with_time(vk, time_str)

        pp: List[str] = []
        if yt and time_str:
            pp.append(
                f'<a class="pl" href="{_esc(yt)}">'
                f'YouTube\u00a0{_esc_safe(time_str)}</a>'
            )
        if rt and time_str:
            pp.append(
                f'<a class="pl" href="{_esc(rt)}">'
                f'RuTube\u00a0{_esc_safe(time_str)}</a>'
            )
        if vk and time_str:
            pp.append(
                f'<a class="pl" href="{_esc(vk)}">'
                f'VK\u00a0{_esc_safe(time_str)}</a>'
            )

        if pp:
            sep = " \u00B7 "
            built = f'{_esc_safe(title)} \u2014 {sep.join(pp)}'
        elif time_str:
            built = f"{_esc_safe(title)} \u2014 {_esc_safe(time_str)}"
        else:
            built = _esc_safe(title)

        dt = _esc(title)
        return f'<h3 data-title="{dt}">{built}</h3>'

    return _RE_H3.sub(_fix_h3, html_str)


def _clean_block_elements(html_str: str) -> str:
    """Чистит текст внутри <p>, <h4>, <blockquote>."""
    def _fix_block(m: re.Match) -> str:
        tag = m.group(1)
        attrs = m.group(2) or ""
        inner = m.group(3) or ""
        return f"<{tag}{attrs}>{_clean_text_with_html(inner)}</{tag}>"

    html_str = _RE_BLOCK_P_H4.sub(_fix_block, html_str)
    prev = ""
    while prev != html_str:
        prev = html_str
        html_str = _RE_BLOCKQUOTE.sub(_fix_block, html_str)
    return html_str


def _polish_html(html_str: str) -> str:
    """
    Полная очистка и обогащение HTML из Telegraph.

    Pipeline:
      1. Удаление опасных тегов (<script>, <style>)
      2. Удаление мягких переносов и артефактов
      3. Удаление навигационных ссылок
      4. Удаление Telegraph-оглавления (<ol> до первого <h3>)
      5. Исправление inline-пробелов
      6. Обогащение заголовков h3 (ссылки, таймкоды, data-title)
      7. Очистка блочных элементов
    """
    if not html_str:
        return ""
    html_str = _strip_dangerous_tags(html_str)
    html_str = _strip_soft_hyphens(html_str)
    html_str = _strip_navigation_links(html_str)
    html_str = _strip_leading_ol(html_str)
    html_str = _fix_inline_spacing(html_str)
    html_str = _enrich_h3_headings(html_str)
    html_str = _clean_block_elements(html_str)
    return html_str.strip()


# ══════════════════════════════════════════════════════════════
#  14. Telegraph fetching
# ══════════════════════════════════════════════════════════════

def _fetch_telegraph_bs4(url: str) -> str:
    """Загружает и парсит Telegraph-страницу через BeautifulSoup."""
    url = _safe_url(url)
    if not url:
        return ""
    r = _fetch_url(url)
    if not r:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")  # type: ignore[arg-type]
    article = soup.find("article")
    if not article:
        return ""
    for tag in article.find_all(["address", "header"]):
        tag.decompose()
    raw = "".join(str(child) for child in article.children)
    return _polish_html(raw)


def _fetch_telegraph_regex(url: str) -> str:
    """Загружает и парсит Telegraph-страницу regex-fallback."""
    url = _safe_url(url)
    if not url:
        return ""
    r = _fetch_url(url)
    if not r:
        return ""
    m = _RE_ARTICLE.search(r.text)
    if not m:
        return ""
    article = m.group(1)
    article = _RE_ADDRESS.sub("", article)
    article = _RE_HEADER.sub("", article)
    return _polish_html(article)


def _fetch_telegraph(url: str) -> str:
    """
    Загружает и кеширует Telegraph-страницу.
    Возвращает отполированный HTML (без TOC — он строится позже).
    """
    cached = _TELEGRAPH_CACHE.get_cached(url)
    if cached is not None:
        return cached
    content = (
        _fetch_telegraph_bs4(url) if _HAS_BS4
        else _fetch_telegraph_regex(url)
    )
    if content.strip():
        _TELEGRAPH_CACHE.put(url, content)
    return content


# ══════════════════════════════════════════════════════════════
#  15. Source links
# ══════════════════════════════════════════════════════════════

def _find_video_links(html_str: str) -> Dict[str, str]:
    """Ищет первые ссылки YouTube / RuTube / VK в HTML."""
    out: Dict[str, str] = {"youtube": "", "rutube": "", "vk": ""}
    for href in _find_all_hrefs(html_str):
        href = _safe_url(href)
        if not href:
            continue
        if not out["youtube"] and (
            "youtube.com" in href or "youtu.be" in href
        ):
            out["youtube"] = href
        elif not out["rutube"] and "rutube.ru" in href:
            out["rutube"] = href
        elif not out["vk"] and (
            "vk.com/" in href or "vkvideo.ru/" in href
        ):
            out["vk"] = href
    return out


def _collect_sources(sections: List[Dict]) -> Dict[str, str]:
    """Собирает ссылки на видео из всех секций (первые найденные)."""
    out: Dict[str, str] = {"youtube": "", "rutube": "", "vk": ""}
    for sec in sections:
        found = _find_video_links(sec.get("content_html") or "")
        for k in out:
            if not out[k] and found.get(k):
                out[k] = found[k]
    return out


# ══════════════════════════════════════════════════════════════
#  16. Single-pass enrichment
# ══════════════════════════════════════════════════════════════

def _enrich_section(
    content_html: str,
    key: str,
    video_url: str,
) -> Tuple[str, List[Dict[str, str]]]:
    """
    Единый проход по секции:
      1. Каждый <h3> получает уникальный id (якорь)
      2. Заголовок читается из data-title (без повторной обработки)
         или извлекается заново при отсутствии data-title
      3. Непустые заголовки попадают в toc_items
      4. Добавляются стрелки «К содержанию»
      5. Линкуются таймкоды

    Returns:
        (enriched_html, toc_items)
        toc_items: [{"title": str, "id": str}, ...]
    """
    toc_items: List[Dict[str, str]] = []
    counter = 0

    def _repl(m: re.Match) -> str:
        nonlocal counter
        counter += 1
        attrs = m.group(1) or ""
        inner = m.group(2)
        anchor_id = f"{key}_h{counter}"

        # Читаем data-title (уже очищенный в _enrich_h3_headings)
        dt_match = _RE_DATA_TITLE.search(attrs)
        if dt_match:
            title = html_module.unescape(dt_match.group(1))
        else:
            # Fallback для h3 без data-title
            raw_title, _, _ = _extract_h3_parts(inner)
            title = _clean_heading(raw_title)

        if title:
            toc_items.append({"title": title, "id": anchor_id})

        arrow = (
            ' <a class="bt" href="#toc" '
            'title="К содержанию">&#8593;</a>'
        )
        return f'<h3 id="{anchor_id}">{inner}{arrow}</h3>'

    enriched = _RE_H3_WITH_ATTRS.sub(_repl, content_html)
    enriched = _linkify_timecodes(enriched, video_url)
    return enriched, toc_items


# ══════════════════════════════════════════════════════════════
#  17. HTML assembly
# ══════════════════════════════════════════════════════════════

def _build_cover(
    title: str, performer: str, dur: str,
    available: List[Dict],
) -> str:
    """Генерирует HTML обложки PDF-документа."""
    rows = "".join(
        '<li class="cover-includes-item">'
        f'{_esc_safe(SECTIONS[s["key"]]["label"])}</li>'
        for s in available
    )
    return f"""
    <div class="cover">
      <div class="cover-inner">
        <div class="cover-rule"></div>
        <div class="cover-title">{_esc_safe(title)}</div>
        <div class="cover-divider"></div>
        <div class="cover-author">{_esc_safe(performer)}</div>
        <div class="cover-duration">{_esc_safe(dur)}</div>
        <div class="cover-blurb">
          Единый документ для вдумчивого чтения: структурный конспект,
          богословский разбор и практическое применение материала.
        </div>
        <div class="cover-includes">
          <div class="cover-includes-label">Состав документа</div>
          <ul class="cover-includes-list">{rows}</ul>
        </div>
      </div>
    </div>"""


def _build_toc(
    available: List[Dict], src: Dict[str, str],
) -> str:
    """Генерирует HTML глобального оглавления. Использует it['id']."""
    parts_html: List[str] = []
    for sec in available:
        key = sec["key"]
        meta = SECTIONS[key]
        items: List[str] = []
        for it in sec.get("toc_items", []):
            t = it.get("title", "")
            aid = it.get("id", "")
            if t and aid:
                items.append(
                    f'<li class="toc-item">'
                    f'<a class="toc-link" href="#{aid}">'
                    f'<span class="toc-item-title">'
                    f'{_esc_safe(t)}</span></a></li>'
                )
        parts_html.append(
            f'<div class="toc-part">'
            f'<div class="toc-part-head">'
            f'<div class="toc-part-kicker">'
            f'Часть {meta["roman"]}</div>'
            f'<div class="toc-part-title">'
            f'{_esc_safe(meta["label"])}</div></div>'
            f'<ol class="toc-list">{"".join(items)}</ol></div>'
        )

    sl: List[str] = []
    for k, lb in [
        ("youtube", "YouTube"), ("rutube", "RuTube"), ("vk", "VK"),
    ]:
        if src.get(k):
            sl.append(f'<a href="{_esc(src[k])}">{lb}</a>')
    src_block = (
        f'<div class="source-block">'
        f'<div class="source-label">Исходный материал</div>'
        f'<div class="source-links">{" ".join(sl)}</div></div>'
        if sl else ""
    )
    return (
        f'<div class="toc-page" id="toc"><div class="toc-inner">'
        f'<div class="toc-heading">Содержание</div>'
        f'{"".join(parts_html)}{src_block}'
        f'</div></div>'
    )


def _build_local_toc(sec: Dict) -> str:
    """Генерирует HTML локального оглавления внутри части."""
    rows: List[str] = []
    for it in sec.get("toc_items", []):
        t = it.get("title", "")
        aid = it.get("id", "")
        if t and aid:
            rows.append(
                f'<li><a href="#{aid}">{_esc_safe(t)}</a></li>'
            )
    if not rows:
        return ""
    return f'<ol class="local-toc">{"".join(rows)}</ol>'


def _build_part(sec: Dict, performer: str, title: str) -> str:
    """Рендерит одну «Часть» из уже обогащённой секции."""
    key = sec["key"]
    meta = SECTIONS[key]
    content = sec["content_html"]
    ltoc = _build_local_toc(sec)

    return f"""
    <div class="part-page" id="{key}">
      <div class="part-opener"><div class="part-opener-inner">
        <div class="part-line" style="background:{meta['color']}"></div>
        <div class="part-kicker">Часть {meta['roman']}</div>
        <div class="part-title">{_esc_safe(meta['label'])}</div>
        <div class="part-intro">{_esc_safe(meta['intro'])}</div>
        <div class="part-meta">
          {_esc_safe(title)} \u2014 {_esc_safe(performer)}
        </div>
      </div></div>
      <div class="section-content-wrap">
        {ltoc}
        <div class="tg-content">
          {content}
          <div class="doc-end"></div>
        </div>
      </div>
    </div>"""


def _build_html(
    title: str,
    performer: str,
    dur: str,
    sections: List[Dict],
) -> str:
    """
    Главная сборка HTML-документа.

    Порядок операций (критичен для корректности):
      1. Отфильтровать пустые секции
      2. Собрать ссылки на источники (из raw HTML, ДО обогащения)
      3. Обогатить каждую секцию (якоря + TOC + таймкоды)
      4. Построить обложку, оглавление, части из обогащённых данных
    """
    raw_available = [
        s for s in sections if (s.get("content_html") or "").strip()
    ]
    src = _collect_sources(raw_available)
    vid = _primary_video_url(src)

    # Single-pass enrichment — создаём новые dict, не мутируя входные
    enriched: List[Dict] = []
    for sec in raw_available:
        html_out, toc = _enrich_section(
            sec["content_html"], sec["key"], vid,
        )
        enriched.append({
            "key": sec["key"],
            "content_html": html_out,
            "toc_items": toc,
        })

    font_css = _build_font_face_css()
    logger.info(
        "PDF style: body_font=%s sans_font=%s",
        PDF_BODY_FONT, PDF_SANS_FONT,
    )
    css = CSS_TEMPLATE.format(
        font_face_css=font_css,
        body_font=_esc_safe(PDF_BODY_FONT),
        sans_font=_esc_safe(PDF_SANS_FONT),
    )
    cover = _build_cover(title, performer, dur, enriched)
    toc_html = _build_toc(enriched, src)
    parts = "".join(
        _build_part(s, performer, title) for s in enriched
    )
    return (
        f'<!DOCTYPE html>\n<html lang="ru">\n<head>\n'
        f'<meta charset="UTF-8">\n'
        f'<title>{_esc_safe(title)}</title>\n'
        f'<style>{css}</style>\n'
        f'</head>\n<body class="reading-pdf">\n'
        f'{cover}{toc_html}{parts}\n'
        f'<div class="doc-end-vignette">'
        f'&#9670; &#9670; &#9670;</div>\n'
        f'</body></html>'
    )


# ══════════════════════════════════════════════════════════════
#  18. PDF generation
# ══════════════════════════════════════════════════════════════

_MIN_PDF_SIZE: int = 200
_WKHTMLTOPDF_TIMEOUT: int = 120
_ASYNC_FETCH_TIMEOUT: int = 90
# FIX AUDIT R4: _POPEN_PATCH_LOCK удалён вместе с monkeypatch subprocess.Popen.


def _pdfkit_options(title: str, performer: str) -> Dict[str, str]:
    """Формирует опции для wkhtmltopdf CLI."""
    hdr = _sanitize_cli(
        f"{performer} \u2014 {title}" if performer else title,
    )
    return {
        "page-size": "A4",
        "margin-top": "16mm",
        "margin-bottom": "18mm",
        "margin-left": "16mm",
        "margin-right": "16mm",
        "encoding": "UTF-8",
        "enable-local-file-access": "",
        "enable-internal-links": "",
        "print-media-type": "",
        "background": "",
        "quiet": "",
        "zoom": "1.04",
        "disable-javascript": "",
        "load-error-handling": "ignore",
        "load-media-error-handling": "ignore",
        "header-left": hdr,
        "header-font-size": "7",
        "header-font-name": "Arial",
        "header-spacing": "5",
        "footer-center": "[page] / [topage]",
        "footer-font-size": "8",
        "footer-font-name": "Arial",
        "footer-spacing": "5",
        "outline": "",
        "outline-depth": "3",
    }


def _gen_wkhtmltopdf(
    html_str: str,
    out: str,
    title: str,
    performer: str,
    wp: Optional[str] = None,
) -> Optional[str]:
    """
    Генерирует PDF прямым вызовом wkhtmltopdf (CLI-опции — как у pdfkit).

    При таймауте убивает СВОЙ дочерний процесс (локальный Popen), не трогая
    чужие subprocess'ы других потоков.

    Returns:
        Путь к файлу или None.
    """
    if not _HAS_PDFKIT:
        return None
    wp = wp or WKHTMLTOPDF_PATH
    if not wp or not Path(wp).exists():
        return None

    tmp: Optional[str] = None
    try:
        opts = _pdfkit_options(title, performer)
        with tempfile.NamedTemporaryFile(
            suffix=".html", mode="w", encoding="utf-8", delete=False,
        ) as f:
            f.write(html_str)
            tmp = f.name

        # FIX AUDIT R4: раньше wkhtmltopdf запускался через pdfkit с ГЛОБАЛЬНЫМ
        # monkeypatch subprocess.Popen. Патч ловил ЛЮБОЙ процесс, запущенный в
        # это время из других потоков (ffmpeg-рендер шорта!), и по таймауту
        # убивал его вместо зависшего wkhtmltopdf; два параллельных PDF могли
        # навсегда оставить Popen подменённым. Теперь wkhtmltopdf вызывается
        # напрямую локальным Popen.
        args = [wp]
        for _k, _v in opts.items():
            args.append(f"--{_k}")
            if _v != "":
                args.append(str(_v))
        args += [tmp, out]
        proc = _subprocess.Popen(
            args, stdout=_subprocess.DEVNULL, stderr=_subprocess.PIPE,
        )
        try:
            _, _stderr = proc.communicate(timeout=_WKHTMLTOPDF_TIMEOUT)
        except _subprocess.TimeoutExpired:
            logger.error(
                "wkhtmltopdf: таймаут %ds, убиваем процесс",
                _WKHTMLTOPDF_TIMEOUT,
            )
            try:
                proc.kill()
                proc.communicate(timeout=5)
            except Exception:
                pass
            return None

        if proc.returncode != 0:
            # load-error-handling=ignore: ненулевой код может сопровождать
            # пригодный PDF — решает финальная проверка размера ниже.
            _err_txt = (_stderr or b"").decode("utf-8", errors="replace")[-400:]
            logger.warning("wkhtmltopdf: rc=%s: %s", proc.returncode, _err_txt)

        out_path = Path(out)
        sz = out_path.stat().st_size if out_path.exists() else 0
        if sz < _MIN_PDF_SIZE:
            logger.error("wkhtmltopdf: PDF пустой (%d bytes)", sz)
            out_path.unlink(missing_ok=True)
            return None
        logger.info("PDF (wkhtmltopdf): %s (%d bytes)", out, sz)
        return out

    except Exception as exc:
        logger.error("wkhtmltopdf: %s", exc)
        try:
            Path(out).unlink(missing_ok=True)
        except Exception:
            pass
        return None
    finally:
        if tmp:
            try:
                Path(tmp).unlink()
            except Exception:
                pass


def _gen_weasyprint(html_str: str, out: str) -> Optional[str]:
    """
    Генерирует PDF через WeasyPrint.

    Returns:
        Путь к файлу или None.
    """
    if not _HAS_WEASYPRINT:
        return None
    try:
        WeasyprintHTML(  # type: ignore[union-attr]
            string=html_str,
        ).write_pdf(out)
        out_path = Path(out)
        sz = out_path.stat().st_size if out_path.exists() else 0
        if sz < _MIN_PDF_SIZE:
            logger.error("weasyprint: PDF пустой (%d bytes)", sz)
            out_path.unlink(missing_ok=True)
            return None
        logger.info("PDF (weasyprint): %s (%d bytes)", out, sz)
        return out
    except Exception as exc:
        logger.error("weasyprint: %s", exc)
        try:
            Path(out).unlink(missing_ok=True)
        except Exception:
            pass
        return None


# ══════════════════════════════════════════════════════════════
#  19. Public API
# ══════════════════════════════════════════════════════════════

def generate_sermon_pdf(
    output_path: str,
    title: str,
    performer: str,
    duration_str: str,
    urls: Dict[str, str],
    wkhtmltopdf_path: Optional[str] = None,
) -> Optional[str]:
    """
    Синхронная генерация PDF.

    Args:
        output_path: путь для сохранения PDF.
        title: название проповеди.
        performer: имя служителя.
        duration_str: длительность ('1:23:45').
        urls: dict с ключами 'synopsis', 'study', 'reflection', 'terms'
              и значениями — URL Telegraph-страниц.
        wkhtmltopdf_path: путь к бинарнику (опционально).

    Returns:
        Путь к созданному PDF или None при ошибке.
    """
    if not urls:
        return None
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    sections: List[Dict] = []
    for key in _SECTION_KEYS:
        url = urls.get(key)
        if not url:
            continue
        logger.info("PDF: fetch %s", key)
        content = _fetch_telegraph(url)
        if content.strip():
            sections.append({"key": key, "content_html": content})
            logger.info("PDF: %s OK", key)

    if not sections:
        logger.warning("PDF: нет контента")
        return None

    sections.sort(key=lambda s: _SECTION_ORDER.get(s["key"], 999))
    html = _build_html(title, performer, duration_str, sections)

    result = _gen_wkhtmltopdf(
        html, output_path, title, performer, wkhtmltopdf_path,
    )
    if result:
        return result

    logger.info("PDF: fallback → weasyprint")
    result = _gen_weasyprint(html, output_path)
    if result:
        return result

    logger.error("PDF: ни один движок не доступен")
    return None


async def generate_sermon_pdf_async(
    output_path: str,
    title: str,
    performer: str,
    duration_str: str,
    urls: Dict[str, str],
    wkhtmltopdf_path: Optional[str] = None,
    progress_callback: ProgressCallback = None,
) -> Optional[str]:
    """
    Асинхронная генерация PDF с параллельной загрузкой Telegraph.

    Включает общий таймаут на загрузку (_ASYNC_FETCH_TIMEOUT).
    Если часть страниц не загрузится — генерируется PDF из того,
    что успело загрузиться.

    Args:
        output_path: путь для сохранения PDF.
        title: название проповеди.
        performer: имя служителя.
        duration_str: длительность ('1:23:45').
        urls: dict с ключами секций и URL Telegraph-страниц.
        wkhtmltopdf_path: путь к бинарнику (опционально).
        progress_callback: async callable(stage: str, pct: int).

    Returns:
        Путь к созданному PDF или None при ошибке.
    """
    loop = asyncio.get_running_loop()
    await _safe_progress(progress_callback, "Подготовка", 5)
    await loop.run_in_executor(
        None,
        lambda: Path(output_path).parent.mkdir(parents=True, exist_ok=True),
    )

    keys = [k for k in _SECTION_KEYS if urls.get(k)]
    if not keys:
        logger.warning("generate_sermon_pdf_async: нет URL секций в urls=%r — возвращаем None", list(urls.keys()))
        await _safe_progress(progress_callback, "Нет контента", 100)
        return None

    await _safe_progress(progress_callback, "Загрузка страниц", 10)

    # ── Параллельная загрузка с общим таймаутом ──
    async def _fetch_one(k: str) -> Tuple[str, str]:
        content = await loop.run_in_executor(
            None, _fetch_telegraph, urls[k],
        )
        return k, content

    tasks = [asyncio.create_task(_fetch_one(k)) for k in keys]
    sections: List[Dict] = []
    completed = 0
    total = len(keys)

    try:
        for coro in asyncio.as_completed(
            tasks, timeout=_ASYNC_FETCH_TIMEOUT,
        ):
            try:
                key, content = await coro
                completed += 1
                pct = 10 + int((completed / total) * 40)
                await _safe_progress(
                    progress_callback,
                    f"Загрузка: {SECTIONS[key]['label']}",
                    pct,
                )
                if content.strip():
                    sections.append({
                        "key": key, "content_html": content,
                    })
                    logger.info("PDF: %s OK", key)
                else:
                    logger.warning("PDF: %s — пустой контент", key)
            except Exception as exc:
                logger.warning(
                    "PDF: ошибка загрузки Telegraph: %s", exc,
                )
    except asyncio.TimeoutError:
        logger.warning(
            "PDF: таймаут загрузки %ds, используем %d/%d секций",
            _ASYNC_FETCH_TIMEOUT, len(sections), total,
        )
        # Незавершённые executor-потоки нельзя прервать через cancel() —
        # они завершатся сами по HTTP-таймауту (_fetch_url timeout=20s).
        # cancel() лишь предотвращает обработку результата в event loop.
        for task in tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    if not sections:
        logger.warning("generate_sermon_pdf_async: все секции вернули пустой контент (keys=%r) — возвращаем None", keys)
        await _safe_progress(progress_callback, "Нет контента", 100)
        return None

    sections.sort(key=lambda s: _SECTION_ORDER.get(s["key"], 999))

    # ── Сборка и рендер ──
    await _safe_progress(progress_callback, "Сборка PDF", 60)
    html = await loop.run_in_executor(
        None, _build_html, title, performer, duration_str, sections,
    )

    await _safe_progress(progress_callback, "Конвертация", 75)
    result = await loop.run_in_executor(
        None,
        lambda: (
            _gen_wkhtmltopdf(
                html, output_path, title, performer, wkhtmltopdf_path,
            )
            or _gen_weasyprint(html, output_path)
        ),
    )

    status = "Готово" if result else "Ошибка"
    await _safe_progress(progress_callback, status, 100)
    if not result:
        logger.warning(
            "generate_sermon_pdf_async: _gen_wkhtmltopdf и _gen_weasyprint оба вернули None/False "
            "для output_path=%r — возвращаем None",
            output_path,
        )
        return None
    return result