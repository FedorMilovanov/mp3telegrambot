import os
import re

print("🚀 Запуск Bullet-Proof патчера (Игнорирует Windows переносы строк)...\n")

def patch_file_regex(fpath, pattern, replacement):
    if not os.path.exists(fpath):
        print(f"❌ Файл не найден: {fpath}")
        return False
    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Нормализуем переносы строк для надежного regex-поиска
    normalized_content = content.replace('\r\n', '\n')
    
    if not re.search(pattern, normalized_content):
        print(f"⚠️ Текст уже пропатчен или не найден в: {fpath}")
        return False
        
    new_content = re.sub(pattern, replacement, normalized_content)
    
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(new_content)
    print(f"✅ Успешно пропатчен: {fpath}")
    return True

# 1. ЗАПРЕТ ОТ ПЕРВОГО ЛИЦА (Исправлено под Windows)
patch_file_regex("core/prompts.py",
r'❌ "Джон МакАртур открывает проповедь глубоким сопереживанием\.\.\."\n✅ "Нет на земле более тяжелого испытания для родителей, чем потеря ребенка\.\.\."\nКонспект — это речь автора от его лица или безлично, НЕ описание его действий\.\n\n%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%\nДОСЛОВНОСТЬ',
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

# 2. ЗАЩИТА ОТ ПУСТЫХ СТРАНИЦ (Исправлено под Windows)
patch_file_regex("services/telegraph_pages.py",
r'        sections = \[s for s in sections if isinstance\(s, dict\) and \(s\.get\("title"\) or s\.get\("content"\) or s\.get\("blocks"\)\)\]\n        if not sections:\n            logger\.warning\("%s: sections пуст после валидации -- fallback", label\)\n            return await fallback_fn\(\) if fallback_fn else None\n',
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
            return await fallback_fn() if fallback_fn else None
            
""")

print("\n🎉 Финальная Windows-совместимая полировка завершена!")