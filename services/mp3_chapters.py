#!/usr/bin/env python3
"""
MP3 Chapters — вшивает таймкоды Gemini-анализа в ID3-главы (CHAP/CTOC).

Зачем: бот уже знает структуру материала (таймкоды тем из ai_data).
ID3v2-главы делают её НАВИГАЦИЕЙ внутри самого файла: подкаст-плееры
(AIMP, Foobar2000, Apple Podcasts-совместимые, Telegram X частично)
показывают список глав и позволяют прыгать по темам проповеди.

Используется mutagen (уже в зависимостях через yt-dlp[default]).
Никогда не бросает исключений наружу — главы это enhancement,
их сбой не должен ломать доставку mp3.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_TS_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")


def _ts_to_ms(ts: str) -> int | None:
    """'MM:SS' / 'H:MM:SS' → миллисекунды."""
    m = _TS_RE.match(str(ts or "").strip())
    if not m:
        return None
    h = int(m.group(1) or 0)
    return ((h * 3600 + int(m.group(2)) * 60 + int(m.group(3))) * 1000)


def _clean_title(topic: str, max_len: int = 120) -> str:
    """Убирает markdown-болд и лишнее из названия темы."""
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", str(topic or ""))
    t = re.sub(r"[*_`]", "", t).strip()
    return t[:max_len] if t else ""


def embed_chapters(mp3_path: Path, timestamps: list, duration_sec: int = 0) -> bool:
    """Вшивает ID3v2 CHAP/CTOC-главы в mp3.

    timestamps: список dict'ов вида {"time": "MM:SS", "topic": "..."}
    (формат ai_data["timestamps"] из Gemini-анализа).
    Возвращает True при успехе; любые проблемы — False без исключений.
    """
    try:
        if not timestamps or len(timestamps) < 2:
            return False  # одна глава — не навигация
        from mutagen.id3 import ID3, CHAP, CTOC, TIT2, CTOCFlags
        from mutagen.id3._util import ID3NoHeaderError

        # Парсим и валидируем точки
        points: list[tuple[int, str]] = []
        for item in timestamps:
            if not isinstance(item, dict):
                continue
            ms = _ts_to_ms(item.get("time", ""))
            title = _clean_title(item.get("topic", ""))
            if ms is None or not title:
                continue
            points.append((ms, title))
        points.sort(key=lambda p: p[0])
        # дедуп по времени (Gemini изредка дублирует)
        dedup: list[tuple[int, str]] = []
        for ms, title in points:
            if dedup and ms <= dedup[-1][0]:
                continue
            dedup.append((ms, title))
        if len(dedup) < 2:
            return False

        end_ms = duration_sec * 1000 if duration_sec else dedup[-1][0] + 60_000

        try:
            tags = ID3(str(mp3_path))
        except ID3NoHeaderError:
            tags = ID3()

        # Убираем старые главы (повторный прогон)
        tags.delall("CHAP")
        tags.delall("CTOC")

        child_ids = []
        for i, (ms, title) in enumerate(dedup):
            next_ms = dedup[i + 1][0] if i + 1 < len(dedup) else end_ms
            if next_ms <= ms:
                next_ms = ms + 1000
            cid = f"ch{i:02d}"
            child_ids.append(cid)
            tags.add(CHAP(
                element_id=cid, start_time=ms, end_time=next_ms,
                sub_frames=[TIT2(encoding=3, text=[title])],
            ))
        tags.add(CTOC(
            element_id="toc", flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
            child_element_ids=child_ids, sub_frames=[],
        ))
        tags.save(str(mp3_path))
        logger.info("MP3 chapters: вшито %d глав", len(child_ids))
        return True
    except Exception as e:
        logger.warning("MP3 chapters: не удалось (%s) — отправка без глав", e)
        return False
