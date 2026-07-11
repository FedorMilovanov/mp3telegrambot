#!/usr/bin/env python3
"""
Search — поиск перезаливов на RuTube и VK.
Извлечено из bot.py строки 7805–8364.
"""
import asyncio
import logging
import os
import re
import requests
import time

from core.text_utils import normalize_author_name, normalize_title_text  # FIX search
from core.database import get_channel_mapping                             # FIX search
from core.url_utils import get_youtube_video_url                         # FIX search

logger = logging.getLogger(__name__)

# AUDIT R14 (лог 2026-07-09): третий элемент кортежа — complete-флаг.
# Ранний выход грузит листинг лишь до страницы с уверенным совпадением ЭТОГО
# видео. Раньше такой ЧАСТИЧНЫЙ листинг кэшировался и переиспользовался для
# следующих видео плейлиста — и их ролики за границей загруженных страниц
# «не находились», хотя реально есть на канале (VK ловил, RuTube терял).
_RUTUBE_LISTING_CACHE: dict[str, tuple[float, list[dict], bool]] = {}
_RUTUBE_LISTING_CACHE_TTL = int(os.getenv("RUTUBE_LISTING_CACHE_TTL", "900"))


def _get_rutube_listing_cache(cid: str) -> tuple[list[dict], bool] | None:
    """Возвращает (results, complete) или None. complete=False — листинг был
    обрезан ранним выходом, ему нельзя доверять «не найдено»."""
    item = _RUTUBE_LISTING_CACHE.get(str(cid or ""))
    if not item:
        return None
    ts, results, complete = item
    if time.time() - ts > _RUTUBE_LISTING_CACHE_TTL:
        _RUTUBE_LISTING_CACHE.pop(str(cid or ""), None)
        return None
    return list(results), complete


def _set_rutube_listing_cache(cid: str, results: list[dict], complete: bool) -> None:
    if not (cid and results):
        return
    # Полный листинг не даём затирать частичным (другое видео могло рано выйти).
    prev = _RUTUBE_LISTING_CACHE.get(str(cid))
    if prev and prev[2] and not complete and time.time() - prev[0] <= _RUTUBE_LISTING_CACHE_TTL:
        return
    _RUTUBE_LISTING_CACHE[str(cid)] = (time.time(), list(results), complete)


def _normalize(s: str) -> str:
    """Нормализует строку для сравнения: lower, ё→е, убрать пунктуацию/скобки/|."""
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[|()\[\]{}<>\"'«».,!?;:\-–—]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _normalize_duration(d) -> int:
    """Нормализует duration — может быть int, float, str ('3542' или '00:59:02')."""
    if isinstance(d, (int, float)):
        return int(d)
    if isinstance(d, str):
        d = d.strip()
        if not d:
            return 0
        if ":" in d:
            parts = d.split(":")
            try:
                if len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                elif len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
            except ValueError:
                return 0
        try:
            return int(float(d))
        except ValueError:
            return 0
    return 0

# Стоп-слова для религиозного контента — встречаются в каждом втором видео
# и не несут идентификационной нагрузки при поиске по RuTube/VK
_SEARCH_STOPWORDS: frozenset = frozenset({
    # Русские
    "вера", "благодать", "покаяние", "суд", "истина", "бог", "господь", "христос",
    "иисус", "библия", "писание", "церковь", "молитва", "грех", "спасение",
    "евангелие", "проповедь", "слово", "дух", "святой", "жизнь", "любовь",
    "свидетельство", "служение", "братья", "сестры", "боже", "святого",
    # Английские
    "faith", "grace", "truth", "god", "lord", "christ", "jesus", "bible",
    "prayer", "sin", "salvation", "gospel", "church", "sermon", "spirit",
    "holy", "love", "life", "word",
    # Частые имена авторов/канала: важны для поиска, но не должны тащить
    # ложные совпадения только по "Джон МакАртур" при одинаковой длительности.
    "джон", "макартур", "макартер", "john", "macarthur", "mac",
    "пол", "вошер", "paul", "washer", "том", "пеннингтон", "tom", "pennington",
    "алексей", "коломийцев", "евгений", "бахмутский", "рбц", "рбц_москва",
    "bible", "q&a", "qa", "вопросы", "ответы", "panel", "дискуссия",
})


def _score_candidate_match(
    meaningful_words: set[str],
    item_meaningful: set[str],
    rare_words: set[str],
    *,
    duration: int = 0,
    item_duration: int = 0,
) -> tuple[float, float, float, float, int]:
    """Return (score, f1_word_score, dur_score, recall, intersection_count).

    Patch 68/69 search safety: the old denominator ``min(query,candidate)`` made
    one-word/author-only candidates look perfect.  F1 keeps both precision and
    recall visible, and hard gates below prevent RuTube/VK false positives from
    being published as alternative links.
    """
    if not meaningful_words or not item_meaningful:
        return 0.0, 0.0, 0.0, 0.0, 0
    intersection = meaningful_words & item_meaningful
    inter_count = len(intersection)
    precision = inter_count / max(len(item_meaningful), 1)
    recall = inter_count / max(len(meaningful_words), 1)
    word_score = 0.0 if (precision + recall) <= 0 else (2 * precision * recall / (precision + recall))

    # AUDIT FIX: if both durations are unknown, don't penalize; treat as unknown (0.5)
    _both_unknown = not duration and not item_duration
    if duration and item_duration:
        dur_diff = abs(item_duration - duration)
    elif duration or item_duration:
        dur_diff = 999  # one is unknown → mismatch
    else:
        dur_diff = 0  # both unknown
    if _both_unknown:
        # AUDIT R39: оба unknown = НЕЙТРАЛЬНО (0.5), а не полный буст 1.0 —
        # иначе слабое текстовое совпадение проскакивало пороги только за счёт
        # «идеального» дефолтного dur_score.
        dur_score = 0.5
    else:
        dur_score = (
            1.0 if dur_diff < 30 else
            0.7 if dur_diff < 120 else
            0.4 if dur_diff < 300 else
            0.15 if dur_diff < 600 else
            0.0
        )

    # Hard safety gates for long materials. One matched author/event word must
    # never beat a real sermon title just because duration is close.
    if duration >= 600:
        if inter_count < 2:
            return 0.0, word_score, dur_score, recall, inter_count
        if recall < 0.30:
            return 0.0, word_score, dur_score, recall, inter_count

    # Short clips can have 1-2 meaningful words, but still need at least one
    # real token match.
    if inter_count <= 0:
        return 0.0, word_score, dur_score, recall, inter_count

    rare_bonus = 0.1 * len(rare_words & item_meaningful) / max(len(rare_words), 1) if rare_words else 0.0
    score = word_score * 0.60 + dur_score * 0.30 + rare_bonus * 0.10

    # Candidate is much longer than query: likely a conference/session umbrella.
    excess_ratio = len(item_meaningful) / max(len(meaningful_words), 1)
    if excess_ratio > 3.0:
        score *= 0.7

    # Duration mismatch must cap long-video candidates below the public return
    # threshold. This specifically blocks VK hits with dur_score=0.0.
    if duration >= 600 and item_duration and dur_score == 0.0:
        score = min(score, 0.49)

    return score, word_score, dur_score, recall, inter_count

# Порог досрочного выхода: score >= этого значения считается надёжным совпадением
_EARLY_EXIT_SCORE  = 0.80
# Сколько лучших кандидатов выводить в лог (остальные молча отбрасываются)
_LOG_TOP_N         = 5

def _best_match(results: list, title: str, duration: int, platform: str,
                early_exit: bool = True, log: bool = True) -> tuple[str | None, float]:
    norm_title  = _normalize(title)
    title_words = set(norm_title.split())
    # Убираем стоп-слова из scoring: они встречаются в каждом религиозном видео
    # и дают ложные совпадения по частым словам
    meaningful_words = title_words - _SEARCH_STOPWORDS if len(title_words) > 2 else title_words
    rare_words  = {w for w in meaningful_words if len(w) > 5}
    best_score, best_url = 0.0, None
    threshold = 0.15 if len(title_words) <= 3 else 0.18
    candidates: list[tuple[float, str, str]] = []   # (score, url, log_line)

    for item in results:
        # AUDIT M13: пропускаем элементы без id для RuTube — иначе URL "https://rutube.ru/video//"
        # пройдёт скоринг и попадёт в кэш как "найденная альт-ссылка".
        if platform == "rutube":
            _rid = (item.get("id") or "").strip()
            if not _rid:
                continue
            item_url = f"https://rutube.ru/video/{_rid}/"
        else:
            item_url = item.get("url") or item.get("player", "")
            if not item_url:
                continue
        item_title    = item.get("title", "") or item.get("name", "")
        item_duration = _normalize_duration(item.get("duration", 0))
        if duration > 300 and 0 < item_duration < 60:
            continue
        norm_item  = _normalize(item_title)
        item_words = set(norm_item.split())
        item_meaningful = item_words - _SEARCH_STOPWORDS if len(item_words) > 2 else item_words
        score, word_score, dur_score, recall, inter_count = _score_candidate_match(
            meaningful_words, item_meaningful, rare_words,
            duration=duration, item_duration=item_duration,
        )
        intersection = meaningful_words & item_meaningful

        if score > threshold:
            log_line = (
                f"  [{platform}] '{item_title[:55]}' dur={item_duration}s "
                f"words={len(intersection)}/{len(title_words)}∩{len(item_words)} "
                f"word_f1={word_score:.2f} recall={recall:.2f} dur={dur_score:.1f} → {score:.2f}"
            )
            candidates.append((score, item_url, log_line))

        if score > best_score and score > threshold:
            best_score, best_url = score, item_url

        # Досрочный выход при очень хорошем совпадении
        if early_exit and best_score >= _EARLY_EXIT_SCORE:
            break

    # Логируем только топ-N кандидатов, отсортированных по убыванию score
    if log:
        top = sorted(candidates, key=lambda x: x[0], reverse=True)[:_LOG_TOP_N]
        for _, _, log_line in top:
            logger.info(log_line)
        if len(candidates) > _LOG_TOP_N:
            logger.info(f"  [{platform}] ... и ещё {len(candidates) - _LOG_TOP_N} кандидатов с score > threshold (не показаны)")

    return best_url, best_score

def _best_match_confident(results: list, title: str, duration: int) -> bool:
    """Возвращает True если в results уже есть кандидат с score >= _EARLY_EXIT_SCORE.
    Используется для страничного раннего выхода в search_rutube."""
    norm_title  = _normalize(title)
    title_words = set(norm_title.split())
    meaningful_words = title_words - _SEARCH_STOPWORDS if len(title_words) > 2 else title_words
    rare_words  = {w for w in meaningful_words if len(w) > 5}
    threshold   = _EARLY_EXIT_SCORE
    for item in results:
        item_title    = item.get("title", "") or item.get("name", "")
        item_duration = _normalize_duration(item.get("duration", 0))
        if duration > 300 and 0 < item_duration < 60:
            continue
        norm_item  = _normalize(item_title)
        item_words = set(norm_item.split())
        item_meaningful = item_words - _SEARCH_STOPWORDS if len(item_words) > 2 else item_words
        score, _word_score, _dur_score, _recall, _inter_count = _score_candidate_match(
            meaningful_words, item_meaningful, rare_words,
            duration=duration, item_duration=item_duration,
        )
        if score >= threshold:
            return True
    return False


def _clean_search_title(title: str) -> str:
    """Убираем номер серии, имя автора в скобках — для поиска на RuTube/VK."""
    t = re.sub(r"^\d+\s*[|:]\s*", "", title)   # '7 | Название' → 'Название'
    t = re.sub(r"\s*\([^)]+\)\s*$", "", t)       # 'Название (Автор)' → 'Название'
    return t.strip()

def _extract_search_keywords(title: str) -> str:
    """Извлекает ключевые слова для поиска на RuTube — убирает ссылки на Писание и лишние дефисы."""
    t = _clean_search_title(title)
    # Убираем ссылки на Писание: "Даниила 4:4-37", "Матфея 11:28-30"
    t = re.sub(r'\b\w+\s+\d+[:\-]\d+(?:[:\-]\d+)?\b', '', t)
    # Убираем одиночные разделители-дефисы
    t = re.sub(r'\s+-\s+', ' ', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    return t or _clean_search_title(title)  # fallback если всё вырезало

def _build_search_title(ai_data: dict | None, fallback_title: str) -> str:
    """Строит оптимальный поисковый заголовок для RuTube/VK.
    Приоритет: real_event + real_title + real_author → full_title из yt-dlp.
    Не использует короткий real_title в одиночку если он < 20 символов.
    """
    if not ai_data:
        return _clean_search_title(fallback_title)

    real_event  = (ai_data.get("real_event")  or "").strip()
    real_title  = normalize_title_text(ai_data.get("real_title")  or "")
    real_author = normalize_author_name(ai_data.get("real_author") or "")

    parts = []
    if real_event:
        parts.append(real_event)
    if real_title:
        parts.append(real_title)
    # AUDIT R10 (лог 2026-07-06): после отбраковки выдуманного названия в
    # real_title попадает ПОЛНЫЙ YouTube-титул, часто уже с автором
    # («Пол Вошер. Свидетельство...») — без проверки получался запрос
    # «... - Пол Вошер» с дублем автора, размывающим точность поиска.
    if real_author and real_author.lower() not in " ".join(parts).lower():
        parts.append(real_author)

    combined = " - ".join(parts) if parts else ""

    # Если combined получился осмысленным и длиннее короткого real_title — используем его
    if combined and len(combined) >= 10:
        # PATCH V2 FIX: если combined > 100 символов — используем real_title + real_author
        # Длинный real_event делает запрос неточным для API-поиска VK/RuTube
        if len(combined) > 100:
            short_parts = [p for p in [real_title, real_author] if p]
            short_combined = " - ".join(short_parts)
            if short_combined and len(short_combined) >= 10:
                return short_combined
        return combined

    # Fallback: исходный full_title из yt-dlp (наиболее точный)
    return _clean_search_title(fallback_title)


async def _load_rutube_listing(
    cid: str, headers: dict, loop, search_title: str, duration: int,
    *, start_page: int = 1, existing: list[dict] | None = None,
    allow_early_exit: bool = True,
) -> tuple[list[dict], bool]:
    """Грузит листинг канала RuTube постранично. Возвращает (results, complete).

    complete=False — загрузку оборвал ранний выход (найдено уверенное
    совпадение), листинг НЕПОЛНЫЙ. complete=True — страницы кончились
    естественно (полный листинг) либо ранний выход отключён и дошли до конца.
    """
    all_results: list[dict] = list(existing or [])
    max_pages = 100  # 100 стр × 20 = 2000 видео — покрывает большие каналы
    per_page = 20
    complete = True
    page = start_page - 1
    for page in range(start_page, max_pages + 1):
        try:
            resp = await loop.run_in_executor(
                None,
                lambda p=page: requests.get(
                    f"https://rutube.ru/api/video/person/{cid}/?page={p}&per_page={per_page}",
                    timeout=10,
                    headers=headers,
                ),
            )
            if resp.status_code != 200:
                logger.info(f"RuTube листинг: status={resp.status_code} на стр.{page}")
                break
            page_results = resp.json().get("results", []) or []
            if not page_results:
                logger.info(f"RuTube листинг: пусто на стр.{page}")
                break
            all_results.extend(page_results)
            # Ранний выход: если уже есть уверенное совпадение — не грузим дальше.
            # Листинг помечается НЕПОЛНЫМ, чтобы кэш не соврал другим видео.
            if allow_early_exit and len(all_results) >= 2 * per_page \
                    and _best_match_confident(all_results, search_title, duration):
                logger.info(f"RuTube: досрочный выход на стр.{page} ({len(all_results)} видео) — найдено уверенное совпадение")
                complete = False
                break
            if len(page_results) < per_page:
                break
        except Exception as e:
            logger.warning(f"RuTube листинг стр.{page} ошибка: {e}")
            if "timed out" in str(e).lower() or "ReadTimeout" in type(e).__name__ or "ssl" in str(e).lower() or "connection" in str(e).lower() or "eof" in str(e).lower():
                await asyncio.sleep(1.0)
                continue
            break
    return all_results, complete


async def search_rutube(title: str, channel_name: str = "", duration: int = 0,
                        fallback_title: str = "") -> str | None:
    loop = asyncio.get_running_loop()
    mapping = get_channel_mapping(channel_name)
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Bot/1.0)"}
    search_title = _extract_search_keywords(title)

    try:
        if not (mapping and mapping.get("rutube_channel_id")):
            logger.info(f"RuTube: для канала '{channel_name}' нет rutube_channel_id")
            return None

        cid = mapping["rutube_channel_id"]
        logger.info(
            f"RuTube: поиск ТОЛЬКО по листингу канала cid={cid}, "
            f"q='{search_title}' (original: '{title}')"
        )

        cached = _get_rutube_listing_cache(cid)
        listing_complete = True
        if cached is not None:
            all_results, listing_complete = cached
            logger.info("RuTube: листинг из кэша — %d видео%s",
                        len(all_results), "" if listing_complete else " (частичный)")
        else:
            all_results, listing_complete = await _load_rutube_listing(
                cid, headers, loop, search_title, duration,
            )
            if all_results:
                _set_rutube_listing_cache(cid, all_results, listing_complete)
                logger.info("RuTube: листинг загружен — %d видео%s",
                            len(all_results), "" if listing_complete else " (частичный, ранний выход)")
        if not all_results:
            logger.info("RuTube: листинг канала пуст, видео не найдено")
            return None

        def _score_listing():
            _res, _score = None, 0.0
            for i, _t in enumerate([search_title, title] + ([fallback_title] if fallback_title and fallback_title != title else [])):
                _r, _sc = _best_match(all_results, _t, duration, "rutube", log=(i == 0))
                if _r and _sc > _score:
                    _score, _res = _sc, _r
                if _score >= _EARLY_EXIT_SCORE:
                    break
            return _res, _score

        # Ищем лучшее совпадение в листинге по всем вариантам заголовка
        listing_result, listing_score = _score_listing()

        # AUDIT R14: если совпадение слабое, а кэш был ЧАСТИЧНЫМ (ранний выход
        # на ЧУЖОМ видео) — наш ролик может лежать на ещё не загруженных
        # страницах. Дозагружаем остаток листинга и ищем снова.
        if listing_score < _EARLY_EXIT_SCORE and not listing_complete:
            logger.info("RuTube: частичный листинг без уверенного совпадения — дозагружаю остаток")
            more, listing_complete = await _load_rutube_listing(
                cid, headers, loop, search_title, duration,
                start_page=len(all_results) // 20 + 1, existing=all_results,
                allow_early_exit=False,
            )
            if len(more) > len(all_results):
                all_results = more
                _set_rutube_listing_cache(cid, all_results, listing_complete)
                logger.info("RuTube: листинг дозагружен — %d видео", len(all_results))
                listing_result, listing_score = _score_listing()

        # Уверенное совпадение из листинга — сразу возвращаем
        if listing_result and listing_score >= _EARLY_EXIT_SCORE:
            logger.info(f"RuTube best_match (листинг, уверенный score={listing_score:.2f}): {listing_result}")
            return listing_result

        # Слабое совпадение — не возвращаем мусор. Для коротких роликов
        # длительность почти не различает кандидатов, поэтому нужен более
        # строгий textual score; иначе 2-минутные служебные ролики матчились
        # на чужие короткие RuTube-видео со score≈0.51.
        _MIN_RETURN_SCORE = 0.75 if duration and duration < 300 else 0.50
        if listing_result and listing_score >= _MIN_RETURN_SCORE:
            logger.info(f"RuTube: возвращаем листинговый (score={listing_score:.2f}): {listing_result}")
            return listing_result

        if listing_result:
            logger.info(f"RuTube: score={listing_score:.2f} ниже порога {_MIN_RETURN_SCORE} — видео не найдено (лучший кандидат не подходит)")
        else:
            logger.info("RuTube: видео не найдено в листинге")
        return None

    except Exception as e:
        logger.warning(f"RuTube поиск: {e}")
        return None

def _build_vk_search_title(
    full_title: str,
    real_title: str = "",
    real_event: str = "",
    real_author: str = "",
) -> str:
    """Строит короткий поисковый запрос для VK video.search (3–8 слов).
    VK плохо ищет по длинным перегруженным заголовкам.
    """
    def _cl(s):
        s = (s or "").strip()
        s = re.sub(r"\s+", " ", s)
        return s.strip(" -–—|:,.;")

    def _dedup(text):
        seen, words = set(), []
        for w in text.split():
            if w.lower() not in seen:
                seen.add(w.lower())
                words.append(w)
        return " ".join(words)

    def _shorten_authors(a):
        a = _cl(a)
        if not a:
            return ""
        parts = [p.strip() for p in re.split(r",|/|;|and|и", a, flags=re.IGNORECASE) if p.strip()]
        return " ".join(parts[:2])

    def _is_qa(t):
        t = t.lower()
        return any(m in t for m in ["вопросы и ответы", "q&a", "qa", "questions and answers",
                                     "вопрос-ответ", "panel", "панель", "дискуссия"])

    ft  = _cl(re.sub(r"\[[^\]]+\]|\([^\)]*\)$", "", full_title))
    rt  = _cl(real_title)
    rev = _cl(real_event)
    ra  = _shorten_authors(real_author)

    qa = _is_qa(ft) or _is_qa(rt) or _is_qa(rev)

    parts = []
    if qa:
        if rev and len(rev.split()) <= 4:
            parts.append(rev)
        if rt:
            parts.append(rt)
        elif "вопросы и ответы" in ft.lower():
            parts.append("Вопросы и Ответы")
        if ra:
            parts.append(ra)
    else:
        # PATCH V2 FIX: real_event включаем только если ≤ 4 слова (бренд-название)
        # "Конференция для семей на домашнем обучении" = 6 слов → не включаем
        if rev and len(rev.split()) <= 4:
            parts.append(rev)
        if rt:
            parts.append(rt)
        if ra:
            parts.append(ra)

    candidate = _dedup(_cl(" - ".join(p for p in parts if p)))
    if len(candidate.split()) < 3:
        candidate = _dedup(ft)

    words = candidate.split()
    if len(words) > 8:
        candidate = " ".join(words[:8])

    return _cl(candidate) or _cl(ft)


async def search_vk_video(title: str, channel_name: str = "", duration: int = 0,
                          ai_data: dict | None = None) -> str | None:
    loop      = asyncio.get_running_loop()
    mapping   = get_channel_mapping(channel_name)
    vk_token  = os.getenv("VK_API_TOKEN", "").strip()
    headers   = {"User-Agent": "Mozilla/5.0 (compatible; Bot/1.0)"}
    search_title = _build_vk_search_title(
        full_title=title,
        real_title=(ai_data or {}).get("real_title", ""),
        real_event=(ai_data or {}).get("real_event", ""),
        real_author=(ai_data or {}).get("real_author", ""),
    )
    logger.info(f"VK поиск: q='{search_title}', token present={bool(vk_token)}")
    try:
        params: dict = {"q": search_title, "count": 10, "v": "5.199"}
        if mapping and mapping.get("vk_owner_id"):
            params["owner_id"] = mapping["vk_owner_id"]
        elif mapping and mapping.get("vk_domain"):
            try:
                # FIX AUDIT R4: без access_token VK отвечает error 5, а в
                # v5.199 response = {"groups": [...]} (не список, как в
                # v5.126) — owner_id никогда не резолвился и поиск тихо шёл
                # по ВСЕМУ VK: чужой перезалив мог опубликоваться как
                # «официальная» VK-ссылка.
                _gid_params = {"group_id": mapping["vk_domain"], "v": "5.199"}
                if vk_token:
                    _gid_params["access_token"] = vk_token
                r = await loop.run_in_executor(None, lambda: requests.get(
                    "https://api.vk.com/method/groups.getById",
                    params=_gid_params,
                    timeout=8, headers=headers))
                _resp_json = r.json().get("response", {})
                if isinstance(_resp_json, dict):
                    _groups = _resp_json.get("groups") or []
                else:  # старый формат v<=5.126: список
                    _groups = _resp_json or []
                gid = (_groups[0] or {}).get("id") if _groups else None
                if gid:
                    params["owner_id"] = f"-{gid}"
                else:
                    logger.warning(
                        "VK groups.getById: не удалось определить owner_id для домена %s — "
                        "поиск будет НЕ ограничен каналом", mapping["vk_domain"],
                    )
            except Exception as _gid_err:
                logger.warning("VK groups.getById error: %s", str(_gid_err)[:120])
        if vk_token:
            params["access_token"] = vk_token
        resp = await loop.run_in_executor(None, lambda: requests.get(
            "https://api.vk.com/method/video.search", params=params, timeout=10, headers=headers))
        logger.info(f"VK ответ: status={resp.status_code}")
        if resp.status_code == 200:
            rjson = resp.json()
            # Логируем ошибку VK API если есть
            if "error" in rjson:
                err_msg = rjson["error"].get("error_msg", str(rjson["error"]))
                err_code = rjson["error"].get("error_code", 0)
                logger.warning(f"VK API error: {err_msg}")
                # #131/#135: video.search недоступен с Service Token VK API —
                # нужен User Token через OAuth.
                # Коды: 15 = Access denied, 5 = auth failed, 7 = permission denied
                _service_token_errors = (
                    "service token" in err_msg.lower()
                    or "this method is not available" in err_msg.lower()
                    or "access denied" in err_msg.lower()
                    or err_code in (15, 7)
                )
                if _service_token_errors and vk_token:
                    logger.warning(
                        "VK: video.search недоступен с Service Token (код %d: %s). "
                        "Необходим User Token через OAuth VK. "
                        "Получите токен на vk.com/dev/authcode_flow_user и установите VK_API_TOKEN в .env. "
                        "Поиск VK будет пропущен.",
                        err_code, err_msg,
                    )
                    return None
                # Приложение заблокировано — пробуем без токена
                if "application is blocked" in err_msg.lower():
                    logger.info("VK: app заблокирован, повтор без токена...")
                    params_noauth = {k: v for k, v in params.items() if k != "access_token"}
                    try:
                        resp2 = await loop.run_in_executor(None, lambda: requests.get(
                            "https://api.vk.com/method/video.search",
                            params=params_noauth, timeout=10, headers=headers))
                        rj2 = resp2.json()
                        if "error" not in rj2:
                            items2 = rj2.get("response", {}).get("items", [])
                            logger.info(f"VK (без токена) items: {len(items2)}")
                            for item in items2:
                                oid = item.get("owner_id", ""); vid = item.get("id", "")
                                canonical2 = f"https://vk.com/video{oid}_{vid}"
                                if mapping and mapping.get("vk_domain"):
                                    item["url"] = f"https://vkvideo.ru/@{mapping['vk_domain']}?z=video{oid}_{vid}"
                                else:
                                    item["url"] = canonical2
                                item["_canonical"] = canonical2
                            if items2:
                                res2, sc2 = _best_match(items2, search_title, duration, "vk")
                                logger.info(f"VK (без токена) score={sc2:.2f}: {res2}")
                                return res2
                        else:
                            logger.warning(
                                f"VK без токена: {rj2['error'].get('error_msg', '')}. "
                                "Зарегистрируйте новое приложение на vk.com/apps и обновите VK_API_TOKEN в .env"
                            )
                    except Exception as e2:
                        logger.warning(f"VK fallback без токена: {e2}")
                return None
            items = rjson.get("response", {}).get("items", [])
            logger.info(f"VK items: {len(items)}")

            # BUG-FIX: ранее обработка items была ошибочно завёрнута в
            # `if len(items) == 0`, из-за чего items > 0 уходили в return None.
            # Теперь корректно: 0 результатов → диагностика и return None,
            # есть результаты → собираем url и вызываем _best_match.
            if len(items) == 0:
                # FIX: if AI-generated real_title gave 0 results, retry with original YouTube title
                _fallback_q = re.sub(r"\[[^\]]+\]|\([^\)]*\)$", "", title).strip()
                if _fallback_q and _fallback_q != search_title:
                    logger.info("VK: 0 по AI-title '%s', fallback на YouTube-title '%s'", search_title, _fallback_q[:60])
                    _fb_params = dict(params, q=_fallback_q)
                    try:
                        _fb_resp = await loop.run_in_executor(None, lambda: requests.get(
                            "https://api.vk.com/method/video.search",
                            params=_fb_params, timeout=10, headers=headers))
                        _fb_rj = _fb_resp.json()
                        if "error" not in _fb_rj:
                            _fb_items = _fb_rj.get("response", {}).get("items", [])
                            if _fb_items:
                                logger.info("VK fallback: %d items по YouTube-title", len(_fb_items))
                                for item in _fb_items:
                                    oid = item.get("owner_id", ""); vid = item.get("id", "")
                                    canonical = f"https://vk.com/video{oid}_{vid}"
                                    if mapping and mapping.get("vk_domain"):
                                        item["url"] = f"https://vkvideo.ru/@{mapping['vk_domain']}?z=video{oid}_{vid}"
                                    else:
                                        item["url"] = canonical
                                    item["_canonical"] = canonical
                                result, _sc = _best_match(_fb_items, _fallback_q, duration, "vk")
                                if result and _sc >= 0.50:
                                    logger.info("VK fallback best_match (score=%.2f): %s", _sc, result)
                                    return result
                    except Exception as _fb_err:
                        logger.warning("VK fallback error: %s", _fb_err)
                logger.info(
                    "VK: 0 результатов по запросу '%s'. "
                    "Причины: 1) video.search требует User Token (OAuth), не Service Token "
                    "(vk.com/dev/authcode_flow_user); "
                    "2) видео не на VK; 3) запрос слишком специфичен.",
                    search_title,
                )
                return None

            for item in items:
                owner_id = item.get("owner_id", "")
                vid_id   = item.get("id", "")
                canonical = f"https://vk.com/video{owner_id}_{vid_id}"
                if mapping and mapping.get("vk_domain"):
                    item["url"] = f"https://vkvideo.ru/@{mapping['vk_domain']}?z=video{owner_id}_{vid_id}"
                else:
                    item["url"] = canonical
                # Всегда держим fallback на каноничный vk.com
                item["_canonical"] = canonical
            result, _sc = _best_match(items, search_title, duration, "vk")
            logger.info(f"VK best_match (score={_sc:.2f}): {result}")
            _VK_MIN_RETURN_SCORE = 0.50
            if result and _sc < _VK_MIN_RETURN_SCORE:
                logger.info(f"VK: score={_sc:.2f} ниже порога {_VK_MIN_RETURN_SCORE} — отклоняем")
                return None
            return result
    except Exception as e:
        logger.warning(f"VK Video поиск: {type(e).__name__}: {e}")
    return None

async def find_alternative_links(title: str, channel_name: str, duration: int,
                               ai_data: dict | None = None,
                               fallback_title: str = "") -> dict:
    # Ищем только если канал известен — есть в CHANNEL_MAP
    if not get_channel_mapping(channel_name):
        logger.info(f"Канал '{channel_name}' не в CHANNEL_MAP — поиск RuTube/VK пропущен")
        return {"rutube": None, "vk": None}
    logger.info(f"Ищем альт-ссылки: title='{title}', channel='{channel_name}', duration={duration}")
    try:
        rutube_url, vk_url = await asyncio.wait_for(
            asyncio.gather(
                search_rutube(title, channel_name, duration, fallback_title=fallback_title),
                search_vk_video(title, channel_name, duration, ai_data=ai_data),
                return_exceptions=True,
            ),
            timeout=120,  # 2 min hard cap on alt-link search
        )
    except asyncio.TimeoutError:
        logger.warning("find_alternative_links: общий timeout 120s — RuTube/VK поиск слишком долгий")
        return {"rutube": None, "vk": None}
    if isinstance(rutube_url, Exception): logger.warning(f"RuTube: {rutube_url}", exc_info=rutube_url); rutube_url = None
    if isinstance(vk_url,    Exception): logger.warning(f"VK: {vk_url}",          exc_info=vk_url);    vk_url    = None
    # FIX AUDIT R7: retry с ОРИГИНАЛЬНЫМ YouTube-названием был только у RuTube.
    # VK искал по AI-заголовку (Gemini переименовывает материал — «Трусливый
    # лжец…» вместо реального «Трус и лжец») и получал 0 результатов.
    # ai_data=None — иначе search_vk_video снова соберёт AI-заголовок.
    if not vk_url and fallback_title and fallback_title != title:
        try:
            logger.info(f"VK retry с оригинальным названием: '{fallback_title}'")
            vk_url = await asyncio.wait_for(
                search_vk_video(fallback_title, channel_name, duration, ai_data=None),
                timeout=45,
            )
        except Exception as _vk_retry_err:
            logger.warning(f"VK retry failed: {_vk_retry_err}")
            vk_url = None
    logger.info(f"Альт-ссылки: rutube={rutube_url}, vk={vk_url}")
    return {"rutube": rutube_url, "vk": vk_url}


# ─── Share-текст и клавиатура ─────────────────────────────────


def build_telegraph_links(
    telegraph_url="", quotes_tg_url="", questions_tg_url="",
    terms_tg_url="", study_tg_url="", reflection_tg_url="",
) -> str:
    items = []
    if telegraph_url:
        items.append(f'├ <tg-emoji emoji-id="5204459709356069827">📝</tg-emoji> <a href="{telegraph_url}">Читать конспект</a>')
    if study_tg_url:
        items.append(f'├ <tg-emoji emoji-id="5328226699593142053">📖</tg-emoji> <a href="{study_tg_url}">Разбор материала</a>')
    elif quotes_tg_url:
        # Legacy: показываем Аналитику только если нет новой страницы Разбора
        items.append(f'├ 🧠 <a href="{quotes_tg_url}">Аналитика</a>')
    if reflection_tg_url:
        items.append(f'├ <tg-emoji emoji-id="5283180056095527519">🙏</tg-emoji> <a href="{reflection_tg_url}">Размышление и применение</a>')
    elif questions_tg_url:
        # Legacy: показываем Вопросы только если нет новой страницы Размышления
        items.append(f'├ <tg-emoji emoji-id="5283180056095527519">❓</tg-emoji> <a href="{questions_tg_url}">Размышление и применение</a>')
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



# AUDIT M19: SETTINGS_GROUPS lives in core/database.py — dead alias removed.

