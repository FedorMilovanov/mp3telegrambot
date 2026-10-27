#!/usr/bin/env python3
"""
Caption builder — формирование подписей к аудио.
Извлечено из bot.py строки 4214–4437.
"""

from typing import Any

from core.text_utils import (
    _scrub_inline,
    _strip_meta_lines,
    normalize_author_name,
    normalize_title_text,
    title_case_fragment,
    BAD_META_PATTERNS,
    normalize_common_typos,  # FIX #7
)
from core.russian_style import polish_public_russian_text
from core.url_utils import get_youtube_timestamp_url, get_youtube_video_url  # FIX #7

# time_to_seconds и _fix_rtl_in_text перенесены в core_utils (разрыв циклов)
from core.core_utils import time_to_seconds  # FIX: circular imports

import html as html_mod
import re


_TRAILING_EMOJI_PUNCT_RE = re.compile(
    r"(?P<space>[ \t]+)"
    r"(?P<emoji>[\U0001F300-\U0001FAFF\u2600-\u27BF]"
    r"(?:\ufe0f|\u200d[\U0001F300-\U0001FAFF\u2600-\u27BF]\ufe0f?)*)"
    r"(?P<punct>[.!?…])(?=\s|$)"
)


def _polish_caption_sentence_punctuation(text: str) -> str:
    """Place sentence punctuation before a trailing emoji.

    Gemini sometimes emits ``испытаниям ⚔️.``. In Russian editorial text the
    punctuation belongs to the sentence and the emoji follows it:
    ``испытаниям. ⚔️``. The rule is deliberately narrow: it only touches a
    sentence-final emoji immediately followed by ``.!?…`` and leaves ordinary
    symbols, links and emoji inside a sentence unchanged.
    """
    value = str(text or "")
    return _TRAILING_EMOJI_PUNCT_RE.sub(
        lambda match: f"{match.group('punct')}{match.group('space')}{match.group('emoji')}",
        value,
    )


def _polish_public_caption_text(text: str) -> str:
    """Apply narrow Russian style fixes, then punctuation polish."""
    return _polish_caption_sentence_punctuation(polish_public_russian_text(text))


def build_caption(
    performer: str,
    title: str,
    duration: int | float,
    file_size_mb: float,
    ai_data: dict[str, Any] | None = None,
    bitrate: str = "128",
    url: str = "",
    telegraph_url: str = "",
    rutube_url: str = "",
    vk_url: str = "",
    quotes_tg_url: str = "",
    questions_tg_url: str = "",
    terms_tg_url: str = "",
    study_tg_url: str = "",
    reflection_tg_url: str = "",
    full_mode: bool = False,
) -> str:
    parts = []

    # Шапка — используем данные от Gemini если есть, иначе из yt-dlp
    real_author = normalize_common_typos(normalize_author_name((ai_data or {}).get("real_author", "") or performer))
    real_title = normalize_common_typos(
        title_case_fragment(normalize_title_text((ai_data or {}).get("real_title", "") or title) or title)
    )
    real_event = normalize_common_typos(_scrub_inline((ai_data or {}).get("real_event", "")))

    # PATCH V2 FIX: показываем real_event только если это реальный бренд-серии,
    # а не описание площадки/аудитории ("Конференция для семей..." → скрываем).
    if real_event:
        _re_lower = real_event.lower()
        _rt_lower = real_title.lower() if real_title else ""
        _GENERIC_MARKERS = (
            "конференция для",
            "встреча для",
            "собрание для",
            "семинар для",
            "съезд для",
            "конгресс для",
            "conference for",
            "meeting for",
            "seminar for",
            "homeschool",
            "homeschooling",
            "для семей",
            "для пасторов",
            "для молодёжи",
            "для студентов",
            "для служителей",
        )
        _is_generic = any(marker in _re_lower for marker in _GENERIC_MARKERS)
        _is_duplicate = (_re_lower in _rt_lower) or (_rt_lower in _re_lower)
        _too_long = len(real_event) > 60
        if _is_generic or _is_duplicate or _too_long:
            real_event = ""

    if real_event:
        parts.append(f'<b><tg-emoji emoji-id="5283043763898328413">📜</tg-emoji> {html_mod.escape(real_event)}</b>')
    safe_title = html_mod.escape(real_title)
    title_emoji = "📖 " if real_event else '<tg-emoji emoji-id="5283043763898328413">📜</tg-emoji> '
    parts.append(f"<b>{title_emoji}{safe_title}</b>")
    if real_author:
        parts.append(f"<b>👤 {html_mod.escape(real_author)}</b>")

    if ai_data and ai_data.get("_ai_unavailable_warning"):
        parts.append("")
        parts.append("⚠️ " + html_mod.escape(str(ai_data.get("_ai_unavailable_warning"))))

    if ai_data and ai_data.get("_partial_publication_warning"):
        parts.append("")
        parts.append("⚠️ " + html_mod.escape(str(ai_data.get("_partial_publication_warning"))))

    if ai_data and ai_data.get("_timestamps_source") == "youtube_transcript":
        parts.append("")
        parts.append("⏱ Таймкоды взяты из субтитров YouTube без AI-обобщения.")

    if ai_data and ai_data.get("timestamp_coverage_warning"):
        ratio = ai_data.get("timestamp_coverage_ratio")
        try:
            ratio_txt = f"{float(ratio):.0%}" if ratio else "неполную часть"
        except (TypeError, ValueError):
            ratio_txt = "неполную часть"
        parts.append("")
        parts.append(f"⚠️ Таймкоды покрывают только {html_mod.escape(ratio_txt)} материала.")

    if ai_data and ai_data.get("_caption_timestamps_trimmed"):
        parts.append("")
        parts.append("⏱ Таймкоды сокращены; полный список в конспекте.")

    # Краткое описание (main_topic) — между шапкой и таймкодами
    main_topic = _polish_public_caption_text(_scrub_inline((ai_data or {}).get("main_topic", "")))
    if main_topic:

        def _topic_to_html(text: str) -> str:
            """Конвертирует **жирный** из Gemini в <b>жирный</b> для Telegram HTML."""
            _count = text.count("**")
            if _count % 2 == 1:
                # #19: rfind находит ** внутри *** и «не тот» маркер при нескольких вхождениях.
                # Собираем только настоящие ** (не часть ***) и удаляем последний непарный.
                _positions = [m.start() for m in re.finditer(r"(?<!\*)\*\*(?!\*)", text)]
                idx = _positions[-1] if _positions else text.rfind("**")
                text = text[:idx] + text[idx + 2 :]
            parts_t = []
            last = 0
            for m in re.finditer(r"\*\*(.+?)\*\*", text):
                if m.start() > last:
                    parts_t.append(html_mod.escape(text[last : m.start()]))
                parts_t.append(f"<b>{html_mod.escape(m.group(1))}</b>")
                last = m.end()
            if last < len(text):
                parts_t.append(html_mod.escape(text[last:]))
            return "".join(parts_t)

        if full_mode:
            # В полном режиме — весь main_topic без обрезки, без курсива
            parts.append("")
            parts.append(_topic_to_html(main_topic.strip()))
        else:
            # Обрезаем до трёх предложений, без курсива
            sentences = re.split(r"(?<=[.!?])\s+", main_topic.strip())
            short_topic = " ".join(sentences[:3])
            parts.append("")
            parts.append(_topic_to_html(short_topic))

    # Таймкоды
    if ai_data and ai_data.get("timestamps"):
        timestamp_lines = []
        for line in ai_data["timestamps"].split("\n"):
            line = line.strip()
            if not line:
                continue
            # Зачищаем мусор ДО html.escape
            line = _scrub_inline(line)
            if not line or any(re.search(p, line, re.IGNORECASE) for p in BAD_META_PATTERNS):
                continue
            # AUDIT-FIX BUG 2: правильно конвертируем **bold** в HTML <b>
            # для визуального акцента в темах таймкодов Telegram caption.
            # (DEEP-QUALITY FIX [C] добавил только комментарий, но не заменил код — исправлено)
            _p = line.split(" ", 1)
            time_str = _p[0]
            topic_str = _polish_public_caption_text(_p[1].strip() if len(_p) == 2 else "")

            def _topic_bold_to_html(t: str) -> str:
                """Конвертирует **bold** → <b>bold</b>, остальное экранирует.
                Это безопаснее чем html_mod.escape потом regex по escape'нутым символам.
                """
                if not t:
                    return ""
                # Чиним непарные ** (если есть)
                _count = t.count("**")
                if _count % 2 == 1:
                    # Удаляем последний непарный
                    _last = t.rfind("**")
                    t = t[:_last] + t[_last + 2 :]
                # Расщепляем по **bold**, чередующиеся куски: text, bold, text, bold, ...
                parts = re.split(r"\*\*([^*\n]+)\*\*", t)
                out = []
                for i, p in enumerate(parts):
                    if i % 2 == 0:
                        # обычный текст — экранируем
                        out.append(html_mod.escape(p))
                    else:
                        # жирный — экранируем содержимое и оборачиваем
                        out.append(f"<b>{html_mod.escape(p)}</b>")
                result = "".join(out)
                # zero-width space после двоеточия + цифра (чтобы Telegram не сделал ссылку)
                return re.sub(r":(\d)", r":​\1", result)

            topic_escaped = _topic_bold_to_html(topic_str)
            # Делаем таймкод кликабельной ссылкой на YouTube если передан url
            if url and topic_escaped:
                secs = time_to_seconds(time_str)
                if secs is not None:
                    yt_link = get_youtube_timestamp_url(url, secs)
                    escaped = f'<a href="{yt_link}">{html_mod.escape(time_str)}</a> {topic_escaped}'
                else:
                    escaped = html_mod.escape(time_str) + (" " + topic_escaped if topic_escaped else "")
            else:
                escaped = html_mod.escape(time_str) + (" " + topic_escaped if topic_escaped else "")
            timestamp_lines.append(escaped)
        # Добавляем блок только если есть непустые таймкоды
        if timestamp_lines:
            parts.append("")
            parts.append("<b>⏱ Таймкоды:</b>")
            parts.append("\n".join(timestamp_lines))

    # Платформы + Telegraph ссылки — никогда не убираются
    platform_links = build_platform_links(url, rutube_url, vk_url)
    tg_links = build_telegraph_links(
        telegraph_url,
        quotes_tg_url,
        questions_tg_url,
        terms_tg_url,
        study_tg_url=study_tg_url,
        reflection_tg_url=reflection_tg_url,
    )
    if tg_links:
        parts.append("")
        parts.append("<b>📂 Читать Подробный Разбор:</b>")
        parts.append(tg_links)
    if platform_links:
        parts.append("")
        parts.append("<b>Смотреть Видео:</b>")
        parts.append(platform_links)

    # Хэштеги — убираются первыми при обрезке
    if ai_data and ai_data.get("hashtags"):
        # PATCH V2 FIX: нормализуем хэштеги — убираем # если Gemini добавил вопреки инструкции
        _raw_tags = ai_data["hashtags"]
        if isinstance(_raw_tags, list):
            _tags_str = " ".join(f"#{str(t).strip().lstrip('#')}" for t in _raw_tags if t and str(t).strip())
        elif isinstance(_raw_tags, str):
            _tag_words = [w.strip() for w in _raw_tags.split() if w.strip()]
            _tags_str = " ".join(f"#{t.lstrip('#')}" for t in _tag_words if t)
        else:
            _tags_str = ""
        if _tags_str:
            parts.append("")
            parts.append(html_mod.escape(_tags_str))

    result = "\n".join(parts)
    # Финальная чистка — только строки-мусор, без редактирования пунктуации
    return _strip_meta_lines(result)


# ─── Markdown → Telegraph nodes ─────────────────────────────
# Функции перенесены в core_utils.py для разрыва цикла markdown ↔ caption.
# Импортируются выше через: from core.core_utils import _fix_unclosed_bold, ...
# Re-export здесь не нужен: все внешние вызывающие модули (telegraph.py,
# markdown.py) уже обновлены и импортируют напрямую из core_utils.


def _split_into_sentences(text: str) -> list[str]:
    """Разбивает текст на предложения по концу предложения (.!?).
    Не разрывает сокращения типа 'т.е.', 'т.д.', инициалы, числа."""
    # Разбиваем по границе предложения: символ конца + пробел + заглавная/цифра/«
    parts = re.split(r'(?<=[.!?])\s+(?=[А-ЯЁA-Z«""\d\-—])', text)
    return [p.strip() for p in parts if p.strip()]


def build_telegraph_links(
    telegraph_url="",
    quotes_tg_url="",
    questions_tg_url="",
    terms_tg_url="",
    study_tg_url="",
    reflection_tg_url="",
) -> str:
    items = []
    if telegraph_url:
        items.append(
            f'├ <tg-emoji emoji-id="5204459709356069827">📝</tg-emoji> <a href="{telegraph_url}">Читать конспект</a>'
        )
    if study_tg_url:
        items.append(
            f'├ <tg-emoji emoji-id="5328226699593142053">📖</tg-emoji> <a href="{study_tg_url}">Разбор материала</a>'
        )
    elif quotes_tg_url:
        # Legacy: показываем Аналитику только если нет новой страницы Разбора
        items.append(f'├ 🧠 <a href="{quotes_tg_url}">Аналитика</a>')
    if reflection_tg_url:
        items.append(
            f'├ <tg-emoji emoji-id="5283180056095527519">🙏</tg-emoji> <a href="{reflection_tg_url}">Размышление и применение</a>'
        )
    elif questions_tg_url:
        # Legacy: показываем Вопросы только если нет новой страницы Размышления
        items.append(
            f'├ <tg-emoji emoji-id="5283180056095527519">❓</tg-emoji> <a href="{questions_tg_url}">Размышление и применение</a>'
        )
    if terms_tg_url:
        items.append(f'├ 📚 <a href="{terms_tg_url}">Термины</a>')
    if not items:
        return ""
    items[-1] = items[-1].replace("├", "└", 1)
    return "\n".join(items)


def build_platform_links(url: str = "", rutube_url: str = "", vk_url: str = "") -> str:
    # Премиум emoji-id для справки:
    # YouTube:  5463206079913533096  (оставлен как tg-emoji)
    # RuTube:   5321388265549373570  (заменён на обычный 🎬)
    # VK:       5278229754099540071  (заменён на обычный 🎦)
    parts = []
    if url:
        yt = get_youtube_video_url(url)
        parts.append(f'<tg-emoji emoji-id="5463206079913533096">🎥</tg-emoji> <a href="{yt}">YouTube</a>')
    if rutube_url:
        parts.append(f'<tg-emoji emoji-id="5321388265549373570">🎬</tg-emoji> <a href="{rutube_url}">RuTube</a>')
    if vk_url:
        parts.append(f'<tg-emoji emoji-id="5278229754099540071">🎦</tg-emoji> <a href="{vk_url}">VK</a>')
    if not parts:
        return ""
    return "\n".join(parts)
