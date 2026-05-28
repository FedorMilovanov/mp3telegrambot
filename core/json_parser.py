#!/usr/bin/env python3
"""
JSON Parser — парсинг и восстановление обрезанного JSON от Gemini.
Извлечено из bot.py строки 1846–2260.
"""
import json
import logging
import re

from core.timestamp_quality import audit_timestamp_coverage
from core.title_topic_audit import audit_title_topic_consistency
from core.text_utils import (   # FIX json_parser
    _clean_field, _clean_meta_line, _filter_times_str,
    _scrub_inline, _strip_meta_lines, is_meta_garbage,
    normalize_author_name, normalize_title_text,
    normalize_hashtag,
)

logger = logging.getLogger(__name__)

def _recover_truncated_json(chunk: str) -> dict | None:
    """Восстанавливает обрезанный JSON-объект верхнего уровня (MAX_TOKENS hit от Gemini).

    Алгоритм: посимвольный трекер глубины скобок (игнорирует содержимое строк).
    Строит карту last_close[depth_after] = последняя позиция '}' которая снизила depth до этого значения.
    Перебирает кандидатов от самого глубокого к поверхностному — добавляет нужное число
    закрывающих ']' и финальный '}', пробует json.loads().

    Пример для timestamps (depth цепочка: 1→2→3→2 при закрытии элемента):
      chunk[: pos_of_last_complete_element + 1] + "\\n  ]\\n}"
    """
    depth = 0
    in_str = False
    escape = False
    last_close: dict[int, int] = {}  # depth_after_closing → last index of '}'

    for idx, ch in enumerate(chunk):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in ("{", "["):
            depth += 1
        elif ch in ("}", "]"):
            depth -= 1
            if ch == "}":
                last_close[depth] = idx

    # Перебираем от самого глубокого уровня к поверхностному
    for target_d in sorted(last_close, reverse=True):
        if target_d < 1:
            continue
        pos = last_close[target_d]
        # Нужно закрыть (target_d - 1) массивов и 1 внешний объект
        closing = "\n  ]" * (target_d - 1) + "\n}"
        fixed = chunk[: pos + 1] + closing
        try:
            data = json.loads(fixed)
            if isinstance(data, dict) and data:
                return data
        except json.JSONDecodeError:
            continue

    return None


def _parse_gemini_response(text: str, duration: int = 0) -> dict | None:
    """v3.2 — парсит плоский JSON от Gemini + terms_data. При ошибке возвращает None.

    FIX 2026-05-21 #11 P2: убираем code-fence ```json / ``` перед поиском JSON,
    иначе s.find('{') не учитывает текст fence и логи замусориваются 'JSON не найден'.
    """
    # FIX 2026-05-21 #11: убираем code-fence префикс/суффикс если есть
    if isinstance(text, str):
        _t = text.strip()
        if _t.startswith('```'):
            # ```json\n{...}\n``` → {...}
            _t = re.sub(r'^```[a-zA-Z]*\s*', '', _t)
            _t = re.sub(r'\s*```\s*$', '', _t)
            text = _t

    def _valid_time(t: str) -> bool:
        if not duration or not t:
            return True
        secs = time_to_seconds(t)
        return secs is not None and secs <= duration + 30

    def _clean_times(times_raw) -> list:
        out = []
        if not isinstance(times_raw, list):
            return out
        for t in times_raw:
            t = str(t or "").strip()
            if t and _valid_time(t):
                out.append(t)
        out = list(dict.fromkeys(out))
        out.sort(key=lambda x: (time_to_seconds(x) or 0))
        return out

    try:
        clean = text.strip()
        clean = re.sub(r"^```[a-z]*\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean).strip()
        start = clean.find("{")
        end   = clean.rfind("}")
        if start == -1 or end <= start:
            logger.warning("_parse_gemini_response: JSON не найден в ответе")
            return None
        clean = clean[start:end + 1]
        data  = json.loads(clean)
    except json.JSONDecodeError as e:
        logger.warning(f"_parse_gemini_response JSONDecodeError: {e} | текст: {text[:300]}")
        # Попытка восстановить обрезанный JSON (MAX_TOKENS от Gemini)
        _chunk = text.strip()
        _chunk = re.sub(r"^```[a-z]*\s*", "", _chunk)
        _chunk = re.sub(r"\s*```$", "", _chunk).strip()
        _s = _chunk.find("{")
        if _s != -1:
            _recovered = _recover_truncated_json(_chunk[_s:])
            if _recovered is not None:
                logger.info(
                    f"_parse_gemini_response: восстановлен обрезанный JSON "
                    f"(ключей в ответе: {len(_recovered)})"
                )
                data = _recovered
            else:
                logger.warning("_parse_gemini_response: восстановление не удалось — возвращаю None")
                return None
        else:
            return None

    result: dict = {
        "real_author":      "",
        "real_title":       "",
        "real_event":       "",
        "format":           "other",
        "main_topic":       "",
        "timestamps":       "",
        "hashtags":         "",
        "analysis_summary": "",
        "argument_arc":     "",
        "key_categories":   [],
        "questions":        [],
        "terms_data": {
            "concepts":      [],
            "scripture":     [],
            "translations":  [],
            "lexicon_notes": [],
        },
        "whisper_hints": [],
    }

    result["real_author"] = normalize_author_name(_clean_meta_line(data.get("real_author", "")))
    result["real_title"]  = normalize_title_text(_clean_meta_line(data.get("real_title", "")))
    _fmt = str(data.get("format", "") or "").strip().lower()
    result["format"] = _fmt if _fmt in ("sermon", "lecture", "qa", "interview", "discussion") else "other"
    result["real_event"]  = _clean_meta_line(data.get("real_event", ""))
    result["main_topic"]  = _scrub_inline(_strip_meta_lines(_clean_field(data.get("main_topic", ""))))

    # timestamps → строка "MM:SS описание\n..."
    ts_list = data.get("timestamps", [])
    if isinstance(ts_list, list):
        lines = []
        dropped = []
        for ts in ts_list[:35]:
            if not isinstance(ts, dict):
                continue
            t_str = (ts.get("time") or "").strip()
            topic = _scrub_inline(_strip_meta_lines((ts.get("topic", "") or "").strip()))
            if not t_str or not topic:
                continue
            if not _valid_time(t_str):
                dropped.append(t_str)
                continue
            parts_t = t_str.split(":")
            try:
                if len(parts_t) == 2:
                    t_str = f"{int(parts_t[0])}:{parts_t[1].zfill(2)}"
                elif len(parts_t) == 3:
                    t_str = f"{int(parts_t[0])}:{int(parts_t[1]):02d}:{parts_t[2].zfill(2)}"
            except ValueError:
                pass
            lines.append(f"{t_str} {topic}")
        if dropped:
            logger.warning(f"Отброшены таймкоды > длительности ({duration}s): {dropped}")
        result["timestamps"] = "\n".join(lines)
        _coverage_issue = audit_timestamp_coverage(result["timestamps"], duration)
        if _coverage_issue:
            logger.warning(
                "Timestamp coverage warning: %s last=%ss duration=%ss ratio=%.2f",
                _coverage_issue.code,
                _coverage_issue.last_seconds,
                _coverage_issue.duration_seconds,
                _coverage_issue.coverage_ratio,
            )
            result["timestamp_coverage_warning"] = _coverage_issue.message

    _title_issue = audit_title_topic_consistency(result.get("real_title", ""), result.get("main_topic", ""), ts_list)
    if _title_issue:
        logger.warning(
            "Title/topic warning: %s overlap=%.2f title_terms=%s",
            _title_issue.code, _title_issue.overlap, ",".join(_title_issue.title_terms),
        )
        result["title_topic_warning"] = _title_issue.message

    # hashtags → строка "#Тег #Тег ..." через normalize_hashtag (core/text_utils).
    # Gemini иногда возвращает строку "Тег1 Тег2 Тег3" вместо массива.
    ht_raw = data.get("hashtags", [])
    if isinstance(ht_raw, str):
        ht_list = [w for w in ht_raw.split() if w]
    elif isinstance(ht_raw, list):
        ht_list = ht_raw
    else:
        ht_list = []

    if ht_list:
        tags = []
        seen: set[str] = set()
        for raw_tag in ht_list[:8]:
            if raw_tag is None:
                continue
            raw_tag = str(raw_tag).strip()
            if not raw_tag or raw_tag.lower() == "none":
                continue
            camel = normalize_hashtag(raw_tag)
            if not camel:
                continue
            key = camel.lstrip("#").lower()
            if key in seen:
                continue
            seen.add(key)
            tags.append(camel)
        result["hashtags"] = " ".join(tags)

    result["analysis_summary"] = _scrub_inline(_strip_meta_lines(
        _clean_field(data.get("analysis_summary", ""))
    ))
    result["argument_arc"] = _scrub_inline(_strip_meta_lines(
        _clean_field(data.get("argument_arc", ""))
    ))

    # key_categories — массив строк "Понятие — объяснение"
    kc_raw = data.get("key_categories", [])
    if isinstance(kc_raw, list):
        result["key_categories"] = [
            _scrub_inline(_strip_meta_lines(_clean_field(str(item))))
            for item in kc_raw
            if _clean_field(str(item))
        ][:10]

    # questions — плоский массив строк (могут начинаться с 🟢/🔵)
    q_raw = data.get("questions", [])
    if isinstance(q_raw, list):
        result["questions"] = [
            _scrub_inline(_strip_meta_lines(_clean_field(str(q))))
            for q in q_raw
            if _clean_field(str(q))
        ][:18]

    # terms_data — плоские массивы строк с || разделителем
    try:
        td = data.get("terms_data", {})
        if isinstance(td, dict):
            # limits по разделам
            section_limits = {
                "concepts": 10,
                "scripture": 10,
                "translations": 6,
                "lexicon_notes": 6,
            }

            for key, limit in section_limits.items():
                raw_list = td.get(key, [])
                if not isinstance(raw_list, list):
                    continue

                cleaned_items = []

                for item in raw_list:
                    # #77: НЕ применяем _clean_field ко всей ||-строке — она добавила бы точку
                    # к последнему полю (times_str), ломая все таймкоды. Каждое поле чистится
                    # отдельно после split("||") ниже. Здесь только strip + meta-фильтр.
                    s = _scrub_inline(_strip_meta_lines(str(item).strip()))
                    if not s:
                        continue
                    if is_meta_garbage(s):
                        continue

                    # Для lexicon_notes ожидаем 8 полей:
                    # ref || ru_key || original_word || source_label || note || why || times || context_mention
                    if key == "lexicon_notes":
                        parts = [p.strip() for p in s.split("||")]
                        while len(parts) < 8:
                            parts.append("")
                        parts = parts[:8]

                        ref, ru_key, original_word, source_label, note, why, times_str, context_mention = parts

                        ref = _scrub_inline(_clean_field(ref))
                        ru_key = _scrub_inline(_clean_field(ru_key))
                        original_word = _scrub_inline(_clean_field(original_word))
                        source_label = _scrub_inline(_clean_field(source_label))
                        note = _scrub_inline(_clean_field(note))
                        why = _scrub_inline(_clean_field(why))
                        times_str = _scrub_inline(times_str.strip())
                        times_str = _filter_times_str(times_str, duration)
                        context_mention = _scrub_inline(_clean_field(context_mention))

                        # Минимальная валидность: должно быть либо место Писания, либо оригинальное слово,
                        # и обязательно словарное пояснение
                        if not note:
                            continue
                        if not ref and not original_word:
                            continue

                        # Чистим поля и собираем обратно в каноническом виде (8 полей)
                        normalized_parts = [
                            ref[:120],
                            ru_key[:120],
                            original_word[:120],
                            source_label[:160],
                            note[:260],
                            why[:220],
                            times_str[:120],
                            context_mention[:300],
                        ]
                        s = " || ".join(normalized_parts)

                    # Для translations ожидаем 6 полей:
                    # ref || focus || ru_raw || en_raw || observation || times
                    elif key == "translations":
                        parts = [p.strip() for p in s.split("||")]
                        while len(parts) < 6:
                            parts.append("")
                        parts = parts[:6]

                        ref, focus, ru_raw, en_raw, observation, times_str = parts
                        ref = _scrub_inline(_clean_field(ref))
                        focus = _scrub_inline(_clean_field(focus))
                        ru_raw = _scrub_inline(_clean_field(ru_raw))
                        en_raw = _scrub_inline(_clean_field(en_raw))
                        observation = _scrub_inline(_clean_field(observation))
                        times_str = _scrub_inline(times_str.strip())
                        times_str = _filter_times_str(times_str, duration)

                        if not ref:
                            continue
                        if not observation and not (ru_raw or en_raw):
                            continue

                        normalized_parts = [
                            ref[:120],
                            focus[:120],
                            ru_raw[:260],
                            en_raw[:260],
                            observation[:320],
                            times_str[:120],
                        ]
                        s = " || ".join(normalized_parts)

                    # Для scripture ожидаем 4 поля:
                    # ref || use || phrase || times
                    elif key == "scripture":
                        parts = [p.strip() for p in s.split("||")]
                        while len(parts) < 4:
                            parts.append("")
                        parts = parts[:4]

                        ref, use, phrase, times_str = parts
                        ref = _scrub_inline(_clean_field(ref))
                        # PATCH V2 FIX: убираем точку которую _clean_field добавил к scripture ссылке
                        ref = re.sub(r'(\d+:\d+[^.]*)\.+$', r'\1', ref.strip())
                        use = _scrub_inline(_clean_field(use))
                        phrase = _scrub_inline(_clean_field(phrase))
                        times_str = _scrub_inline(times_str.strip())
                        times_str = _filter_times_str(times_str, duration)

                        if not ref:
                            continue

                        normalized_parts = [
                            ref[:120],
                            use[:260],
                            phrase[:220],
                            times_str[:120],
                        ]
                        s = " || ".join(normalized_parts)

                    # Для concepts ожидаем 4 поля:
                    # term || explanation || why || times
                    elif key == "concepts":
                        parts = [p.strip() for p in s.split("||")]
                        while len(parts) < 4:
                            parts.append("")
                        parts = parts[:4]

                        term, explanation, why, times_str = parts
                        term = _scrub_inline(_clean_field(term))
                        # PATCH V2 FIX: убираем точку в конце имени термина
                        term = term.rstrip('.').strip() if term else term
                        explanation = _scrub_inline(_clean_field(explanation))
                        why = _scrub_inline(_clean_field(why))
                        times_str = _scrub_inline(times_str.strip())
                        times_str = _filter_times_str(times_str, duration)

                        if not term:
                            continue

                        normalized_parts = [
                            term[:120],
                            explanation[:260],
                            why[:220],
                            times_str[:120],
                        ]
                        s = " || ".join(normalized_parts)

                    cleaned_items.append(s[:700])

                result["terms_data"][key] = cleaned_items[:limit]

    except Exception as e:
        logger.warning(f"_parse_gemini_response: terms_data parsing error ({e}), skipping")
        result["terms_data"] = {
            "concepts": [],
            "scripture": [],
            "translations": [],
            "lexicon_notes": [],
        }

    # whisper_hints — плоский массив строк для initial_prompt Whisper
    wh_raw = data.get("whisper_hints", [])
    if isinstance(wh_raw, list):
        result["whisper_hints"] = [
            str(w).strip() for w in wh_raw
            if isinstance(w, str) and str(w).strip()
        ][:80]
    else:
        result["whisper_hints"] = []

    # hermeneutic_method — тип проповеди (expository/topical/narrative/...)
    _valid_methods = {
        "expository", "topical", "narrative", "typological",
        "redemptive_historical", "catechetical", "apologetic",
        "evangelistic", "practical", "mixed"
    }
    _hm_raw = data.get("hermeneutic_method", "")
    result["hermeneutic_method"] = _hm_raw if _hm_raw in _valid_methods else "mixed"

    has_content = any([
        result["main_topic"], result["timestamps"],
        result["real_author"], result["real_title"],
        result["questions"], result["analysis_summary"],
        any([
            result["terms_data"]["concepts"],
            result["terms_data"]["scripture"],
            result["terms_data"]["translations"],
            result["terms_data"]["lexicon_notes"],
        ]),
    ])
    return result if has_content else None


# Перенесено в core_utils.py для разрыва цикла json_parser → text_utils → json_parser.
# Re-export для обратной совместимости: все модули, импортирующие time_to_seconds
# из json_parser, продолжают работать без изменений.
from core.core_utils import time_to_seconds  # noqa: F401  (re-export)


# Alias для совместимости
_try_parse_synopsis_json = _parse_gemini_response
