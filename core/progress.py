#!/usr/bin/env python3
"""
Progress helpers — PROGRESS_STEPS, _progress_bar, set_progress.

AUDIT M11: set_progress теперь корректно обрабатывает RetryAfter (FloodWait)
и BadRequest("message is not modified") вместо тихого `except Exception: pass`.
Дополнительно — дедупликация по тексту, чтобы не отправлять одинаковые edit'ы.
"""
import asyncio
import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)

PROGRESS_STEPS = [
    (10,  "🔍 Получаю информацию..."),
    (20,  "🖼  Скачиваю обложку..."),
    (40,  "📥 Скачиваю аудио..."),
    (60,  "🧠 AI анализирует материал..."),
    (80,  "📝 Собираю разбор и ссылки..."),
    (100, "📤 Отправляю аудиофайл..."),
]


def _progress_bar(pct: int) -> str:
    filled = pct // 10
    return "🟦" * filled + "⬜" * (10 - filled) + f" {pct}%"


# AUDIT M11/PART5: дедупликация прогресса.
# Был plain dict + clear() при >1000 записей: память формально не текла,
# но на больших плейлистах возникал cache thrashing и терялась дедупликация.
# OrderedDict даёт настоящий LRU с ограниченным размером.
_PROGRESS_CACHE_MAX = 500
_last_text_cache: OrderedDict[int, str] = OrderedDict()


def _progress_cache_get(message_id: int) -> str | None:
    value = _last_text_cache.get(message_id)
    if value is not None:
        _last_text_cache.move_to_end(message_id)
    return value


def _progress_cache_put(message_id: int, text: str) -> None:
    _last_text_cache[message_id] = text
    _last_text_cache.move_to_end(message_id)
    while len(_last_text_cache) > _PROGRESS_CACHE_MAX:
        _last_text_cache.popitem(last=False)


async def set_progress(status_msg, step: int, info: dict | None = None, prefix: str = ""):
    step = max(1, min(step, len(PROGRESS_STEPS)))
    lines = []
    if prefix:
        lines.append(prefix.strip())
    for i, (pct, label) in enumerate(PROGRESS_STEPS):
        if i < step - 1:
            lines.append(f"✅ {label.rstrip('.')}")
        elif i == step - 1:
            lines.append(f"⏳ {label}")
            lines.append(_progress_bar(pct))
            break
    if info:
        lines.append("")
        for val in info.values():
            if val:
                lines.append(val)

    text = "\n".join(lines)
    msg_id = getattr(status_msg, "message_id", None)

    # AUDIT M11: дедупликация — Telegram отвечает BadRequest("message is not modified")
    if msg_id is not None and _progress_cache_get(msg_id) == text:
        return

    try:
        await status_msg.edit_text(text)
        if msg_id is not None:
            _progress_cache_put(msg_id, text)
    except Exception as e:
        # AUDIT M11: специально обрабатываем RetryAfter (FloodWait)
        cls_name = type(e).__name__
        msg_text = str(e).lower()
        if "retryafter" in cls_name.lower() or "flood" in msg_text:
            wait = getattr(e, "retry_after", 0) or 5
            logger.warning(f"set_progress: FloodWait {wait}s — ждём и повторяем")
            try:
                await asyncio.sleep(float(wait) + 0.5)
                await status_msg.edit_text(text)
                if msg_id is not None:
                    _progress_cache_put(msg_id, text)
            except Exception as e2:
                logger.debug(f"set_progress retry: {e2}")
        elif "not modified" in msg_text:
            # Telegram сказал что текст не изменился — заносим в кэш чтобы не повторять
            if msg_id is not None:
                _progress_cache_put(msg_id, text)
        else:
            logger.debug(f"set_progress: {cls_name}: {e}")


async def safe_edit_text(msg, text: str, **kwargs) -> bool:
    """Edit message text, swallowing Telegram errors (deleted msg, not modified, etc.).

    Returns True if edit succeeded, False otherwise.
    Use instead of raw msg.edit_text() in handlers/pipelines where the message
    may have been deleted by the user or expired by Telegram.
    """
    try:
        await msg.edit_text(text, **kwargs)
        return True
    except Exception as e:
        _s = str(e).lower()
        if "not modified" not in _s:
            logger.debug("safe_edit_text: %s: %s", type(e).__name__, str(e)[:120])
        return False
