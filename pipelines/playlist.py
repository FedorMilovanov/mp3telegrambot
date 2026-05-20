#!/usr/bin/env python3
"""
Playlist Pipeline — handle_playlist.

AUDIT FIX M2: check_rate_limit вызывается ВНУТРИ цикла плейлиста
              (раньше один раз перед стартом — позволяло выкачать весь дневной
               лимит за один заход).
AUDIT FIX M3: ошибки отдельных видео не спамят пользователю — собираются
              в список и показываются в финальном сообщении.
AUDIT FIX L11: media_url вычисляется через extract_url или собирается с учётом
               domain (RuTube/VK тоже работают через yt-dlp).
AUDIT FIX L12: PLAYLIST_DELAY_SEC конфигурируется через .env.
"""
import asyncio
import logging
import os
import shutil
import yt_dlp

from core.globals import GEMINI_CLIENTS
from core.database import (
    MAX_PLAYLIST_SIZE,
    acheck_rate_limit, aupdate_rate_limit,
    WHITELIST_IDS,
)
from services.ffmpeg import COOKIES_FILE
from pipelines.main_pipeline import process_single_video

logger = logging.getLogger(__name__)

# AUDIT L12: задержка между видео в плейлисте — конфигурируется
PLAYLIST_DELAY_SEC = float(os.getenv("PLAYLIST_DELAY_SEC", "1.0"))


def _build_media_url(entry: dict) -> str | None:
    """AUDIT L11: учитываем разные платформы вместо хардкода YouTube."""
    # 1) Если есть готовый url — используем его
    explicit = entry.get("url") or entry.get("webpage_url")
    if explicit and explicit.startswith("http"):
        return explicit
    # 2) Иначе пытаемся восстановить по ie_key + id
    vid = entry.get("id")
    if not vid:
        return None
    ie = (entry.get("ie_key") or "").lower()
    if "rutube" in ie:
        return f"https://rutube.ru/video/{vid}/"
    if "vk" in ie:
        return f"https://vkvideo.ru/video{vid}"
    # 3) По умолчанию — YouTube (исторический fallback)
    return f"https://www.youtube.com/watch?v={vid}"


async def handle_playlist(url, update, context, user_id: int = 0):
    status_msg = await update.message.reply_text("📋 Загружаю плейлист...")
    failed_entries: list[str] = []  # AUDIT M3: собираем упавшие
    try:
        playlist_opts = {
            "extractor_args": {"youtube": {"player_client": ["web"]}},
            "sleep_interval": 2,
            "quiet": True,
            "extract_flat": True,
            "socket_timeout": 30,
            "retries": 3,
        }
        if COOKIES_FILE.exists():
            playlist_opts["cookiefile"] = str(COOKIES_FILE)
        elif shutil.which("firefox"):
            playlist_opts["cookiesfrombrowser"] = ("firefox",)
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(
            None,
            lambda: yt_dlp.YoutubeDL(playlist_opts).extract_info(url, download=False),
        )
        if not info:
            await status_msg.edit_text("❌ Не удалось загрузить плейлист.")
            return
        title   = info.get("title", "Плейлист")
        entries = info.get("entries", [])[:MAX_PLAYLIST_SIZE]
        total   = len(entries)
        if not entries:
            await status_msg.edit_text("❌ Плейлист пуст.")
            return
        ai_status = "✅" if GEMINI_CLIENTS else "❌"
        await status_msg.edit_text(
            f"📋 {title}\n🎬 Записей: {total}\n🧠 AI: {ai_status}\n🎵 128 kbps\n⏳ Начинаю..."
        )
        await asyncio.sleep(2)
        success = fail = 0
        is_vip = user_id in WHITELIST_IDS

        for i, entry in enumerate(entries, 1):
            # AUDIT M2: проверяем rate-limit ПЕРЕД каждым видео
            if user_id and not is_vip:
                allowed, reason = await acheck_rate_limit(user_id)
                if not allowed:
                    await status_msg.edit_text(
                        f"📋 {title}\n"
                        f"⏸ Лимит исчерпан после {i-1}/{total}\n"
                        f"✅ {success}  ❌ {fail}\n\n"
                        f"{reason}"
                    )
                    break

            media_url = _build_media_url(entry)
            if not media_url:
                fail += 1
                failed_entries.append(f"[{i}] (нет URL у entry)")
                continue

            prefix = f"[{i}/{total}] "
            try:
                await status_msg.edit_text(
                    f"📋 {title}\n🔄 {i}/{total} | ✅ {success} | ❌ {fail}"
                )
            except Exception:
                pass

            # AUDIT M3: silent_errors=True — process_single_video не шлёт reply_text
            ok = await process_single_video(
                media_url, update, status_msg, prefix,
                context=context, silent_errors=True,
            )
            if ok:
                success += 1
                if user_id:
                    await aupdate_rate_limit(user_id)
            else:
                fail += 1
                failed_entries.append(f"[{i}] {media_url}")

            if i < total:
                await asyncio.sleep(PLAYLIST_DELAY_SEC)

        # ── Финальное сообщение с деталями ──
        final = f"🏁 Плейлист обработан!\n📋 {title}\n✅ {success} | ❌ {fail} | 📊 {total}"
        if failed_entries:
            # AUDIT M3: показываем первые N упавших ссылок
            preview = "\n".join(failed_entries[:5])
            more = (
                f"\n... и ещё {len(failed_entries) - 5}"
                if len(failed_entries) > 5 else ""
            )
            final += f"\n\nНе удалось:\n{preview}{more}"
        try:
            await status_msg.edit_text(final)
        except Exception:
            # Сообщение могло устареть — отправим новым
            await update.message.reply_text(final)
    except Exception as e:
        logger.error(f"Ошибка плейлиста: {e}")
        try:
            await status_msg.edit_text(f"❌ Ошибка: {str(e)[:300]}")
        except Exception:
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:300]}")
