#!/usr/bin/env python3
"""
URL утилиты — YouTube URL helpers.
Извлечено из bot.py строки 2262–2297.
"""
import urllib.parse

def get_youtube_video_url(url: str) -> str:
    """Извлекает чистый URL с video ID."""
    parsed = urllib.parse.urlparse(url)
    if "youtu.be" in parsed.netloc:
        video_id = parsed.path.strip("/").split("/")[0]
        return f"https://youtu.be/{video_id}"
    # /live/VIDEO_ID и /shorts/VIDEO_ID
    for prefix in ("/live/", "/shorts/"):
        if parsed.path.startswith(prefix):
            video_id = parsed.path[len(prefix):].split("/")[0].split("?")[0]
            if video_id:
                return f"https://www.youtube.com/watch?v={video_id}"
    params = urllib.parse.parse_qs(parsed.query)
    video_id = params.get("v", [None])[0]
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return url


def get_youtube_timestamp_url(url: str, seconds: int, offset: int = 0) -> str:
    """Ссылка на YouTube с таймкодом. offset — сдвиг назад в секундах (для глоссария и т.п.)."""
    seconds = max(0, seconds - offset)
    parsed = urllib.parse.urlparse(url)
    if "youtu.be" in parsed.netloc:
        video_id = parsed.path.strip("/").split("/")[0]
        return f"https://youtu.be/{video_id}?t={seconds}"
    params = urllib.parse.parse_qs(parsed.query)
    video_id = params.get("v", [None])[0]
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}&t={seconds}"
    return url


# ─── Synopsis v2.x: промпты (format-aware) ──────────────────

# Стандартный конспект для sermon / lecture / interview / discussion / other
