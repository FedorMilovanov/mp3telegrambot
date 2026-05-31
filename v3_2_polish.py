import os

def patch_file(fpath, old_text, new_text):
    if not os.path.exists(fpath):
        print(f"❌ Файл не найден: {fpath}")
        return False
    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()
    if old_text not in content:
        print(f"⚠️ Текст уже пропатчен или не найден в: {fpath}")
        return False
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content.replace(old_text, new_text))
    print(f"✅ Успешно пропатчен: {fpath}")
    return True

print("Запуск финальной полировки V3.2...\n")

# 1. Запрет галлюцинаций от первого лица (Я, Иоанн)
patch_file("core/prompts.py",
"""❌ "Джон МакАртур открывает проповедь глубоким сопереживанием..."
✅ "Нет на земле более тяжелого испытания для родителей, чем потеря ребенка..."
Конспект — это речь автора от его лица или безлично, НЕ описание его действий.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ДОСЛОВНОСТЬ""",
"""❌ "Джон МакАртур открывает проповедь глубоким сопереживанием..."
✅ "Нет на земле более тяжелого испытания для родителей, чем потеря ребенка..."
Конспект — это речь автора от его лица или безлично, НЕ описание его действий.

ЗАПРЕТ ВСТАВЛЯТЬ СЛОВА ОТ ЛИЦА БИБЛЕЙСКИХ ПЕРСОНАЖЕЙ:
Никогда не пиши от первого лица библейских авторов или героев.
❌ «Я, Иоанн, увидел...» — ты НЕ Иоанн, ты конспектируешь автора.
❌ «Я, Господь, говорю...» — ты НЕ Господь.
Если автор ЦИТИРУЕТ библейского героя — оформи как цитату с пояснением.

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
ДОСЛОВНОСТЬ""")

# 2. Выявление полного несовпадения заголовков
patch_file("core/json_parser.py",
"""    if _title_issue:
        logger.warning(
            "Title/topic warning: %s overlap=%.2f title_terms=%s",
            _title_issue.code, _title_issue.overlap, ",".join(_title_issue.title_terms),
        )
        result["title_topic_warning"] = _title_issue.message""",
"""    if _title_issue:
        # Если перекрытие почти нулевое - возможно Gemini галлюцинирует и взял не то видео
        _tt_overlap = getattr(_title_issue, 'overlap', None)
        if _tt_overlap is not None and _tt_overlap < 0.05:
            logger.error(
                "Title/topic MISMATCH: %s overlap=%.2f title_terms=%s — Gemini may have misidentified!",
                _title_issue.code, _title_issue.overlap, ",".join(_title_issue.title_terms),
            )
        else:
            logger.warning(
                "Title/topic warning: %s overlap=%.2f title_terms=%s",
                _title_issue.code, _title_issue.overlap, ",".join(_title_issue.title_terms),
            )
        result["title_topic_warning"] = _title_issue.message""")

# 3. Защита от пустых страниц
patch_file("services/telegraph_pages.py",
"""        sections = [s for s in sections if isinstance(s, dict) and (s.get("title") or s.get("content") or s.get("blocks"))]
        if not sections:
            logger.warning("%s: sections пуст после валидации -- fallback", label)
            return await fallback_fn() if fallback_fn else None""",
"""        sections = [s for s in sections if isinstance(s, dict) and (s.get("title") or s.get("content") or s.get("blocks"))]
        if not sections:
            logger.warning("%s: sections пуст после валидации -- fallback", label)
            return await fallback_fn() if fallback_fn else None
            
        # Quality guard: проверка, не прислала ли Gemini пустой контент везде
        _all_empty = all(
            not ((s.get("content") or "").strip() or s.get("blocks"))
            for s in sections if isinstance(s, dict)
        )
        if _all_empty:
            logger.warning("%s: все sections пусты (Gemini broken output) — fallback", label)
            return await fallback_fn() if fallback_fn else None""")

print("\n🎉 V3.2 Полировка завершена!")