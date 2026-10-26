#!/usr/bin/env python3
"""Отправка результата: свежий путь и cache-hit с instant resend.

Порядок — часть пользовательского контракта: сначала обязательный MP3, затем
необязательные дополнения. Ошибка загрузки MP3 проходит в общий обработчик
пайплайна; главы ID3, полное описание, PDF и quiz не должны из-за собственной
ошибки отменять уже готовый результат.

Cache-hit сначала пытается отдать сохранённый Telegram file_id, а при его
отказе очищает ID и использует файловый upload. Его исторические отличия от
свежего пути (ID3-комментарий, PDF boundaries/messages, отсутствие quiz)
явно управляются ``cache_hit`` и закреплены тестами, а не стёрты реюзом.

Внешние зависимости собираются в `DeliveryDeps`. PDF и quiz импортируются
лениво в момент вызова: необязательный генератор не может помешать отправке
аудио ещё до того, как соответствующая настройка запрошена.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable

from core.text_utils import normalize_author_name, normalize_title_text, title_case_fragment
from core.utils import format_timestamp

logger = logging.getLogger(__name__)

MAX_UPLOAD_ATTEMPTS = 3
MIN_PDF_BYTES = 200
MAX_PDF_BYTES = 49 * 1024 * 1024

RETRYABLE_NAMES = ("Timeout", "NetworkError", "TimedOut", "ReadError", "ConnectError")
RETRYABLE_STRINGS = ("internal server error", "server error", "bad gateway", "gateway timeout")

SettingFn = Callable[[str], Awaitable[Any]]
SaveFileIdFn = Callable[[str, str], Awaitable[None]]
ChapterParserFn = Callable[[Any], list[dict[str, Any]]]
EmbedChaptersFn = Callable[..., bool]
PdfFn = Callable[..., Awaitable[str | None]]
QuizFn = Callable[..., Awaitable[list[dict[str, Any]] | None]]
SendQuizFn = Callable[..., Awaitable[int]]
SleepFn = Callable[[float], Awaitable[Any]]
TokenFn = Callable[[], str]


def _pdf_token() -> str:
    """Короткий суффикс, чтобы одновременные PDF не делили один путь."""
    return uuid.uuid4().hex[:6]


async def _generate_pdf(*args: Any, **kwargs: Any) -> str | None:
    """Ленивая граница необязательного PDF-генератора."""
    from services.pdf_generator import generate_sermon_pdf_async

    return await generate_sermon_pdf_async(*args, **kwargs)


async def _generate_quiz(*args: Any, **kwargs: Any) -> list[dict[str, Any]] | None:
    """Ленивая граница генератора вопросов."""
    from services.quiz_generator import generate_quiz

    return await generate_quiz(*args, **kwargs)


async def _send_quiz(*args: Any, **kwargs: Any) -> int:
    """Ленивая граница последовательной quiz-сессии."""
    from services.quiz_generator import send_quiz_polls

    return await send_quiz_polls(*args, **kwargs)


@dataclass(frozen=True)
class DeliveryRequest:
    """Всё, что нужно отправке; никаких значений из замыканий пайплайна."""

    update: Any
    context: Any
    audio_delivery: Any
    mp3_path: Path
    media_id: str
    source_url: str
    title: str
    performer: str
    duration: int
    file_size_mb: float
    caption: str
    full_caption: str
    download_dir: Path
    thumbs_dir: Path
    ai_data: dict[str, Any] | None = None
    thumbnail: Any = None
    synopsis_url: str = ""
    study_url: str = ""
    reflection_url: str = ""
    terms_url: str = ""
    used_audio_part: Any = None
    used_client: Any = None
    announce_upload: bool = True
    upload_log_label: str = "Upload"
    cache_hit: bool = False
    cached_file_id: str = ""

    def pdf_urls(self) -> dict[str, str]:
        """URL секций для PDF в стабильном порядке, без пустых значений."""
        return {
            key: url
            for key, url in (
                ("synopsis", self.synopsis_url),
                ("study", self.study_url),
                ("reflection", self.reflection_url),
                ("terms", self.terms_url),
            )
            if url
        }


@dataclass(frozen=True)
class DeliveryDeps:
    """Внешние границы стадии; в тестах — фейки без Telegram и Gemini."""

    get_setting: SettingFn
    save_file_id: SaveFileIdFn
    parse_chapters: ChapterParserFn
    embed_chapters: EmbedChaptersFn
    generate_pdf: PdfFn
    generate_quiz: QuizFn
    send_quiz: SendQuizFn
    token: TokenFn = _pdf_token
    sleep: SleepFn = asyncio.sleep


@dataclass(frozen=True)
class DeliveryOutcome:
    """Что реально произошло на каждом необязательном шаге."""

    chapters_embedded: bool
    audio_message: Any
    full_caption_sent: bool
    pdf_status: str
    quiz_sent: int
    audio_via_file_id: bool = False


def production_deps() -> DeliveryDeps:
    """Боевые зависимости; тяжёлые генераторы остаются за lazy-обёртками."""
    from core.database import adb_set_audio_file_id, asettings_get
    from services.mp3_chapters import embed_chapters, timestamps_to_chapter_list

    return DeliveryDeps(
        get_setting=asettings_get,
        save_file_id=adb_set_audio_file_id,
        parse_chapters=timestamps_to_chapter_list,
        embed_chapters=embed_chapters,
        generate_pdf=_generate_pdf,
        generate_quiz=_generate_quiz,
        send_quiz=_send_quiz,
    )


def is_retryable_upload(error: Exception) -> bool:
    """Сетевая ли это ошибка, после которой повтор имеет смысл."""
    error_name = type(error).__name__
    error_text = str(error).lower()
    return any(name in error_name for name in RETRYABLE_NAMES) or any(text in error_text for text in RETRYABLE_STRINGS)


def safe_filename(value: str) -> str:
    """Безопасная часть имени PDF: без служебных символов, максимум 80."""
    return re.sub(r'[\\/:*?"<>|\n\r\t]', "", value).strip()[:80] or "doc"


async def embed_audio_chapters(request: DeliveryRequest, deps: DeliveryDeps) -> bool:
    """Вшить ID3-главы; ошибка метаданных не мешает отправке MP3."""
    try:
        ai = request.ai_data or {}
        chapters = deps.parse_chapters(ai.get("timestamps"))
        thumb_path = None
        thumb_candidates = list(request.thumbs_dir.glob(f"{request.media_id}_thumb*"))
        if thumb_candidates:
            thumb_path = thumb_candidates[0]

        comment = f"Source: {request.source_url}"
        if request.synopsis_url:
            comment += f"\nSynopsis: {request.synopsis_url}"
        if not request.cache_hit:
            if request.study_url:
                comment += f"\nStudy: {request.study_url}"
            if request.reflection_url:
                comment += f"\nReflection: {request.reflection_url}"

        if not chapters:
            return False
        return await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: deps.embed_chapters(
                request.mp3_path,
                chapters,
                request.duration,
                title=ai.get("real_title") or request.title,
                performer=ai.get("real_author") or request.performer,
                thumb_path=thumb_path,
                comment=comment,
            ),
        )
    except Exception as err:
        if request.cache_hit:
            logger.warning("MP3 chapters (кэш) skip: %s", err)
        else:
            logger.warning("MP3 chapters skip: %s", err)
        return False


async def send_audio(request: DeliveryRequest, deps: DeliveryDeps) -> Any:
    """Отправить MP3 с тремя попытками и сохранить file_id после первого успеха."""
    if request.announce_upload and not request.cache_hit:
        try:
            await request.update.message.chat.send_action("upload_voice")
        except Exception:
            pass

    audio_title = title_case_fragment((request.ai_data or {}).get("real_title") or request.title)
    audio_performer = normalize_author_name((request.ai_data or {}).get("real_author") or request.performer)
    with request.mp3_path.open("rb") as audio_file:
        attempt = 0
        while True:
            try:
                audio_file.seek(0)
                sent = await request.audio_delivery.send_or_defer(
                    request.update.message,
                    audio=audio_file,
                    fallback_path=request.mp3_path,
                    title=audio_title,
                    performer=audio_performer,
                    thumbnail=request.thumbnail,
                    duration=request.duration,
                    caption=request.caption,
                    parse_mode="HTML",
                    write_timeout=180,
                    read_timeout=180,
                    connect_timeout=60,
                )
                try:
                    file_id = getattr(getattr(sent, "audio", None), "file_id", "")
                    if file_id:
                        await deps.save_file_id(request.media_id, file_id)
                except Exception:
                    pass
                return sent
            except Exception as upload_err:
                retry_after = getattr(upload_err, "retry_after", None)
                if retry_after is not None and attempt < MAX_UPLOAD_ATTEMPTS - 1:
                    logger.warning("Flood control: ждём %sс перед повтором", retry_after)
                    await deps.sleep(float(retry_after) + 1.0)
                    attempt += 1
                    continue
                if attempt < MAX_UPLOAD_ATTEMPTS - 1 and is_retryable_upload(upload_err):
                    if request.cache_hit:
                        logger.warning(
                            "Кэш upload попытка %d/3 (%s: %s), повтор...",
                            attempt + 1,
                            type(upload_err).__name__,
                            str(upload_err)[:120],
                        )
                    else:
                        logger.warning(
                            "%s попытка %d/3 не удалась (%s: %s), повтор...",
                            request.upload_log_label,
                            attempt + 1,
                            type(upload_err).__name__,
                            str(upload_err)[:120],
                        )
                    await deps.sleep(5 * (attempt + 1))
                    attempt += 1
                    continue
                raise


async def send_full_caption(request: DeliveryRequest, deps: DeliveryDeps) -> bool:
    """Отправить полный текст отдельным сообщением, если он не совпадает с подписью."""
    enabled = await deps.get_setting("caption_full_text")
    if enabled and request.full_caption and request.full_caption != request.caption:
        try:
            await request.update.message.reply_text(request.full_caption, parse_mode="HTML")
            return True
        except Exception as err:
            if request.cache_hit:
                logger.warning("Кэш full caption send error: %s", err)
            else:
                logger.warning("Full caption send error: %s", err)
    return False


async def send_pdf(request: DeliveryRequest, deps: DeliveryDeps) -> str:
    """Сгенерировать и отправить PDF; вернуть машинный итог шага."""
    enabled = await deps.get_setting("generate_pdf")
    if not enabled:
        return "disabled"
    if not request.ai_data:
        return "no_analysis"

    status_message: Any = None
    pdf_path: str | None = None
    outcome = "no_urls"
    try:
        request.download_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = str(request.download_dir / f"{request.media_id}_{deps.token()}.pdf")

        raw_title = (request.ai_data.get("real_title") or "").strip() or (request.title or "").strip()
        raw_author = (request.ai_data.get("real_author") or "").strip() or (request.performer or "").strip()
        pdf_title = normalize_title_text(raw_title) if raw_title else "Без названия"
        pdf_author = normalize_author_name(raw_author) if raw_author else "Неизвестный"
        duration_str = format_timestamp(request.duration) if request.duration else ""
        urls = request.pdf_urls()

        if urls:
            log_prefix = "PDF (кэш)" if request.cache_hit else "PDF"
            logger.info("%s: генерирую (%s)", log_prefix, list(urls.keys()))
            status_message = await request.update.message.reply_text("📄 Генерирую PDF…")

            async def pdf_progress(stage: str, pct: int) -> None:
                try:
                    await status_message.edit_text(f"📄 PDF: {stage} ({pct}%)")
                except Exception:
                    pass

            generated = await deps.generate_pdf(
                output_path=pdf_path,
                title=pdf_title,
                performer=pdf_author,
                duration_str=duration_str,
                urls=urls,
                progress_callback=pdf_progress,
            )
            if generated and Path(generated).exists():
                size = Path(generated).stat().st_size
                if request.cache_hit and not (MIN_PDF_BYTES < size < MAX_PDF_BYTES):
                    logger.warning("PDF (кэш): размер вне допустимого диапазона: %d байт", size)
                    await request.update.message.reply_text("❌ Не удалось создать PDF.")
                    outcome = "invalid_size"
                elif not request.cache_hit and size < MIN_PDF_BYTES:
                    logger.warning("PDF пустой: %d байт", size)
                    await request.update.message.reply_text("❌ Не удалось создать PDF (файл пустой).")
                    outcome = "empty"
                elif not request.cache_hit and size > MAX_PDF_BYTES:
                    logger.warning("PDF слишком большой: %d байт", size)
                    await request.update.message.reply_text("❌ PDF слишком большой для отправки.")
                    outcome = "too_large"
                else:
                    filename = f"{safe_filename(pdf_author)} — {safe_filename(pdf_title)}.pdf"
                    with Path(generated).open("rb") as pdf_file:
                        await request.update.message.reply_document(
                            document=pdf_file,
                            filename=filename,
                            caption="📄 <b>PDF-версия материала</b>",
                            parse_mode="HTML",
                        )
                    logger.info("%s: отправлен", log_prefix)
                    outcome = "sent"
            else:
                logger.warning("%s: generate_sermon_pdf_async вернул None или файл не создан", log_prefix)
                await request.update.message.reply_text("❌ Не удалось создать PDF.")
                outcome = "missing"
    except Exception as err:
        if request.cache_hit and isinstance(err, ImportError):
            outcome = "unavailable"
        elif request.cache_hit:
            logger.warning("PDF (кэш) не удался: %s", err)
            outcome = "error"
        else:
            logger.warning("PDF генерация не удалась: %s", err, exc_info=True)
            outcome = "error"
    finally:
        if pdf_path:
            try:
                Path(pdf_path).unlink(missing_ok=True)
            except Exception:
                pass
        if status_message:
            try:
                await status_message.delete()
            except Exception:
                pass
    return outcome


async def send_quiz(request: DeliveryRequest, deps: DeliveryDeps) -> int:
    """Сгенерировать и запустить quiz; ошибка необязательного шага гасится."""
    enabled = await deps.get_setting("generate_quiz")
    if enabled and request.ai_data:
        try:
            logger.info("Quiz: generating questions")
            questions = await deps.generate_quiz(
                ai_data=request.ai_data,
                title=request.title,
                performer=request.performer,
                duration=request.duration,
                existing_audio_part=request.used_audio_part,
                existing_client=request.used_client,
            )
            if questions:
                quiz_title = request.ai_data.get("real_title") or request.title or ""
                await request.update.message.reply_text(
                    f"🧠 <b>Quiz: {html.escape(quiz_title[:80])}</b>\n"
                    f"Проверь понимание материала — {len(questions)} вопросов:",
                    parse_mode="HTML",
                )
                sent = await deps.send_quiz(questions, request.update, request.context, title=quiz_title)
                logger.info("Quiz: started sequential session (%d questions)", sent)
                return sent
            logger.info("Quiz: no questions generated (Gemini returned None)")
            return 0
        except Exception as err:
            logger.warning("Quiz generation failed: %s", err)
            return 0
    logger.info("Quiz: skipped (feat=%s, ai_data=%s)", enabled, "yes" if request.ai_data else "no")
    return 0


async def _send_cached_file_id(request: DeliveryRequest, deps: DeliveryDeps) -> tuple[bool, Any]:
    """Попробовать instant resend; протухший ID очистить и разрешить fallback."""
    if not request.cached_file_id or request.audio_delivery.enabled:
        return False, None
    try:
        sent = await request.update.message.reply_audio(
            audio=request.cached_file_id,
            caption=request.caption,
            parse_mode="HTML",
        )
        logger.info("Кэш mp3: отправлен по file_id (%s)", request.media_id)
        return True, sent
    except Exception as err:
        logger.warning("Кэш mp3 file_id протух: %s", err)
        try:
            await deps.save_file_id(request.media_id, "")
        except Exception:
            pass
        return False, None


async def deliver_cached_result(request: DeliveryRequest, deps: DeliveryDeps | None = None) -> DeliveryOutcome:
    """Cache-hit: instant file_id → файловый fallback → полный текст → PDF."""
    deps = deps or production_deps()
    request = replace(request, cache_hit=True)
    via_file_id, audio_message = await _send_cached_file_id(request, deps)
    chapters_embedded = False
    if not via_file_id:
        chapters_embedded = await embed_audio_chapters(request, deps)
        audio_message = await send_audio(request, deps)
    full_caption_sent = await send_full_caption(request, deps)
    pdf_status = await send_pdf(request, deps)
    return DeliveryOutcome(
        chapters_embedded=chapters_embedded,
        audio_message=audio_message,
        full_caption_sent=full_caption_sent,
        pdf_status=pdf_status,
        quiz_sent=0,
        audio_via_file_id=via_file_id,
    )


async def deliver_result(request: DeliveryRequest, deps: DeliveryDeps | None = None) -> DeliveryOutcome:
    """Пройти свежую отправку целиком в прежнем порядке."""
    deps = deps or production_deps()
    request = replace(request, cache_hit=False)
    chapters_embedded = await embed_audio_chapters(request, deps)
    audio_message = await send_audio(request, deps)
    logger.info(
        "Done: [%s] %s (%.1f MB, 128kbps)",
        normalize_author_name(request.performer) or request.performer,
        normalize_title_text(request.title) or request.title,
        request.file_size_mb,
    )
    full_caption_sent = await send_full_caption(request, deps)
    pdf_status = await send_pdf(request, deps)
    quiz_sent = await send_quiz(request, deps)
    return DeliveryOutcome(
        chapters_embedded=chapters_embedded,
        audio_message=audio_message,
        full_caption_sent=full_caption_sent,
        pdf_status=pdf_status,
        quiz_sent=quiz_sent,
    )


__all__ = [
    "MAX_PDF_BYTES",
    "MAX_UPLOAD_ATTEMPTS",
    "MIN_PDF_BYTES",
    "DeliveryDeps",
    "DeliveryOutcome",
    "DeliveryRequest",
    "deliver_cached_result",
    "deliver_result",
    "embed_audio_chapters",
    "is_retryable_upload",
    "production_deps",
    "safe_filename",
    "send_audio",
    "send_full_caption",
    "send_pdf",
    "send_quiz",
]
