#!/usr/bin/env python3
"""
Progress helpers — PROGRESS_STEPS, _progress_bar, set_progress.
Извлечено из bot.py строки 12433–12469.
"""
import logging

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

async def set_progress(status_msg, step: int, info: dict | None = None, prefix: str = ""):
    # Защита от выхода за границы: step должен быть в диапазоне [1, len(PROGRESS_STEPS)]
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
    try:
        await status_msg.edit_text("\n".join(lines))
    except Exception:
        pass


# ─── Обработка одного видео ───────────────────────────────────

