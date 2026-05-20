#!/usr/bin/env python3
"""
Playlist Pipeline — handle_playlist.
Извлечено из bot.py строки 13447–13512.
"""
from core.globals import GEMINI_CLIENTS   # FIX playlist
from core.database import MAX_PLAYLIST_SIZE   # FIX playlist
from core.utils import update_rate_limit  # FIX playlist
from services.ffmpeg import COOKIES_FILE                         # FIX playlist
from pipelines.main_pipeline import process_single_video

import asyncio
import logging
import shutil     # FIX playlist
import yt_dlp    # FIX playlist

logger = logging.getLogger(__name__)

async def handle_playlist(url, update, context, user_id: int = 0):
    status_msg = await update.message.reply_text("📋 Загружаю плейлист...")
    try:
        playlist_opts = {
            "extractor_args": {"youtube": {"player_client": ["web"]}},
            "sleep_interval": 2,
            "quiet": True,
            "extract_flat": True,
            # FIXED #35: критичные опции из YTDLP_BASE_ARGS — handle_playlist использует
            # YoutubeDL(dict) напрямую, минуя subprocess, поэтому YTDLP_BASE_ARGS не применяется.
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
        for i, entry in enumerate(entries, 1):
            vid = entry.get("id") or entry.get("url")
            if not vid:
                fail += 1
                continue
            media_url = f"https://www.youtube.com/watch?v={vid}"
            prefix = f"[{i}/{total}] "
            await status_msg.edit_text(
                f"📋 {title}\n🔄 {i}/{total} | ✅ {success} | ❌ {fail}"
            )
            ok = await process_single_video(media_url, update, status_msg, prefix, context=context)
            if ok:
                success += 1
                if user_id:
                    update_rate_limit(user_id)  # списываем за каждое видео
            else:
                fail += 1
            if i < total:
                await asyncio.sleep(1)
        await status_msg.edit_text(
            f"🏁 Плейлист обработан!\n📋 {title}\n✅ {success} | ❌ {fail} | 📊 {total}"
        )
    except Exception as e:
        logger.error(f"Ошибка плейлиста: {e}")
        await status_msg.edit_text(f"❌ Ошибка: {str(e)[:300]}")


# ─── Команды бота ─────────────────────────────────────────────

