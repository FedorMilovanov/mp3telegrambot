#!/usr/bin/env python3
"""
MP3 Chapters & Metadata — вшивает таймкоды Gemini-анализа в ID3-главы (CHAP/CTOC)
и обогащает файл тегами (исполнитель, название, обложка).

Зачем: бот уже знает структуру материала (таймкоды тем из ai_data).
ID3v2-главы делают её НАВИГАЦИЕЙ внутри самого файла: подкаст-плееры
показывают список глав и позволяют прыгать по темам проповеди.
Обогащение тегами делает файлы "copy-ready" для личных медиатек.

Используется mutagen.
Никогда не бросает исключений наружу — метаданные это enhancement.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

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


def embed_chapters(
    mp3_path: Path, 
    timestamps: list, 
    duration_sec: int = 0,
    title: str = "",
    performer: str = "",
    thumb_path: Optional[Path] = None,
    comment: str = ""
) -> bool:
    """Вшивает ID3v2 CHAP/CTOC-главы и базовые метаданные в mp3.

    timestamps: список dict'ов вида {"time": "MM:SS", "topic": "..."}
    Возвращает True при успехе; любые проблемы — False без исключений.
    """
    try:
        from mutagen.id3 import ID3, CHAP, CTOC, TIT2, TPE1, APIC, COMM, CTOCFlags
        from mutagen.id3._util import ID3NoHeaderError

        try:
            tags = ID3(str(mp3_path))
        except ID3NoHeaderError:
            tags = ID3()

        # 1. Базовые теги
        if title:
            tags.add(TIT2(encoding=3, text=[title]))
        if performer:
            tags.add(TPE1(encoding=3, text=[performer]))
        if comment:
            tags.add(COMM(encoding=3, lang='rus', desc='desc', text=[comment]))

        # 2. Обложка
        if thumb_path and Path(thumb_path).exists():
            try:
                with open(thumb_path, "rb") as img:
                    tags.add(APIC(
                        encoding=3, mime='image/jpeg', type=3, desc='Cover',
                        data=img.read()
                    ))
            except Exception as e:
                logger.debug("MP3 cover embed failed: %s", e)

        # 3. Главы (только если > 1)
        if timestamps and len(timestamps) >= 2:
            # Парсим и валидируем точки
            points: list[tuple[int, str]] = []
            for item in timestamps:
                if not isinstance(item, dict):
                    continue
                ms = _ts_to_ms(item.get("time", ""))
                topic_title = _clean_title(item.get("topic", ""))
                if ms is None or not topic_title:
                    continue
                points.append((ms, topic_title))
            points.sort(key=lambda p: p[0])
            
            # дедуп по времени
            dedup: list[tuple[int, str]] = []
            for ms, topic_title in points:
                if dedup and ms <= dedup[-1][0]:
                    continue
                dedup.append((ms, topic_title))
            
            if len(dedup) >= 2:
                # Убираем старые главы (повторный прогон)
                tags.delall("CHAP")
                tags.delall("CTOC")

                end_ms = duration_sec * 1000 if duration_sec else dedup[-1][0] + 60_000
                child_ids = []
                for i, (ms, topic_title) in enumerate(dedup):
                    next_ms = dedup[i + 1][0] if i + 1 < len(dedup) else end_ms
                    if next_ms <= ms:
                        next_ms = ms + 1000
                    cid = f"ch{i:02d}"
                    child_ids.append(cid)
                    tags.add(CHAP(
                        element_id=cid, start_time=ms, end_time=next_ms,
                        sub_frames=[TIT2(encoding=3, text=[topic_title])],
                    ))
                tags.add(CTOC(
                    element_id="toc", flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED,
                    child_element_ids=child_ids, sub_frames=[],
                ))
                logger.info("MP3 metadata: вшито %d глав", len(child_ids))

        tags.save(str(mp3_path))
        return True
    except Exception as e:
        logger.warning("MP3 metadata embed: не удалось (%s)", e)
        return False
