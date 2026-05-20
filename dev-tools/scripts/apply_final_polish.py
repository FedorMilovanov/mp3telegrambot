#!/usr/bin/env python3
"""
apply_final_polish.py — финальные точечные фиксы по реальным скриншотам Telegraph.

Проблемы из реального лога и скриншотов (20:50:00):

[FIX 1] gemini-3-flash не существует как имя модели → 404 NOT_FOUND
        Правильное имя — gemini-3-flash-preview (или вообще убрать)
        Лечим: убираем 3-flash, оставляем только 3.5-flash → 3.1-flash-lite

[FIX 2] Слипание текста и эмодзи-таймкода: 'надежды⏱ 19:50' вместо 'надежды ⏱ 19:50'
        Лечим: regex-фикс на всех Telegraph-страницах перед публикацией

[FIX 3] Точка ПОСЛЕ таймкода вместо ДО: 'Богом ⏱ 15:55.' вместо 'Богом. ⏱ 15:55'
        Лечим: regex переносит . перед ⏱

[FIX 4] Слипание лидер-bold и текста: 'тлении.**В материале:**' вместо 'тлении. **В материале:**'
        Лечим: regex добавляет пробел между . и **

[FIX 5] Невалидный иврит ʼěnôš ʿiʾŠ — это от промпта, оставляем как есть
        Промпт уже учит правильно. Если станет хуже — отдельным патчем поправим prompts.py

Идемпотентен. После применения скрипт остаётся (для возможной перепроверки).
Удалить вручную после успешной проверки.
"""
from pathlib import Path
import shutil
import sys
import os

print("=" * 70)
print("apply_final_polish.py — финальные фиксы Telegraph + multi-model")
print("=" * 70)

ROOT = Path(__file__).parent.parent.parent.resolve()
os.chdir(ROOT)
print(f"\n[cwd] {ROOT}\n")

CHANGED, SKIPPED, ERRORS = [], [], []


def patch_file(rel_path, replacements, check_marker):
    p = Path(rel_path)
    if not p.exists():
        ERRORS.append(f"{rel_path}: не найден")
        return False
    text = p.read_text(encoding="utf-8")
    if check_marker in text:
        SKIPPED.append(f"{rel_path}: уже пропатчен")
        return False
    original = text
    backup = p.with_suffix(p.suffix + ".bak_fp")
    if not backup.exists():
        shutil.copy2(p, backup)
    applied = 0
    for old, new, name in replacements:
        if old in text:
            text = text.replace(old, new, 1)
            applied += 1
            print(f"    [✓] {name}")
        else:
            print(f"    [⚪] {name} — не найдено")
    if text == original:
        SKIPPED.append(f"{rel_path}: нет изменений")
        return False
    p.write_text(text, encoding="utf-8")
    if p.suffix == ".py":
        import ast
        try:
            ast.parse(text)
        except SyntaxError as e:
            ERRORS.append(f"{rel_path}: SyntaxError line {e.lineno}: {e.msg}")
            shutil.copy2(backup, p)
            return False
    CHANGED.append(f"{rel_path}: {applied} изменений")
    return True


# ═══════════════════════════════════════════════════════════════════════
# FIX 1: убрать несуществующий gemini-3-flash из fallback (404)
# ═══════════════════════════════════════════════════════════════════════
print("[FIX 1] Убираю gemini-3-flash (404 NOT_FOUND) из fallback")

# Файл 1: services/telegraph_pages.py
tp_old = '''    _models = [GEMINI_MODEL]
    for _m in ("gemini-3-flash", "gemini-3.1-flash-lite"):
        if _m not in _models:
            _models.append(_m)'''

tp_new = '''    # FINAL-POLISH FIX 1: gemini-3-flash не существует как имя API → 404.
    # У этого аккаунта в free tier доступны: gemini-3.5-flash, gemini-2.5-flash-lite,
    # gemini-3.1-flash-lite. Последняя — самая стабильная (500 RPD free).
    _models = [GEMINI_MODEL]
    for _m in ("gemini-3.1-flash-lite", "gemini-2.5-flash-lite"):
        if _m not in _models:
            _models.append(_m)'''

patch_file("services/telegraph_pages.py", [
    (tp_old, tp_new, "telegraph_pages: 3-flash → 3.1-flash-lite + 2.5-flash-lite"),
], check_marker="FINAL-POLISH FIX 1")

# Файл 2: services/gemini_analyze.py (если есть тот же список)
ga_path = Path("services/gemini_analyze.py")
if ga_path.exists():
    ga_text = ga_path.read_text(encoding="utf-8")
    if "FINAL-POLISH FIX 1" not in ga_text:
        # Ищем вхождение списка fallback моделей
        for old_pattern, new_pattern, desc in [
            ('"gemini-3-flash",', '"gemini-3.1-flash-lite", "gemini-2.5-flash-lite",  # FINAL-POLISH FIX 1', "gemini_analyze: 3-flash → 3.1-flash-lite + 2.5-flash-lite"),
            ("'gemini-3-flash',", "'gemini-3.1-flash-lite', 'gemini-2.5-flash-lite',  # FINAL-POLISH FIX 1", "gemini_analyze: 3-flash → 3.1-flash-lite (alt quotes)"),
        ]:
            if old_pattern in ga_text:
                ga_text2 = ga_text.replace(old_pattern, new_pattern, 1)
                if ga_text2 != ga_text:
                    backup = ga_path.with_suffix(".py.bak_fp")
                    if not backup.exists():
                        shutil.copy2(ga_path, backup)
                    ga_path.write_text(ga_text2, encoding="utf-8")
                    CHANGED.append(f"services/gemini_analyze.py: {desc}")
                    print(f"    [✓] {desc}")
                    break
        else:
            SKIPPED.append("services/gemini_analyze.py: паттерн '3-flash' не найден")


# ═══════════════════════════════════════════════════════════════════════
# FIX 2+3+4: пост-обработка текста на стороне кода
# Добавляем функцию _polish_timestamps_in_text в core_utils и применяем её
# к каждой строке перед парсингом в Telegraph nodes.
# ═══════════════════════════════════════════════════════════════════════
print("\n[FIX 2+3+4] Пост-обработка таймкодов и слипаний (regex-фиксы)")

cu_path = Path("core/core_utils.py")
if cu_path.exists():
    cu_text = cu_path.read_text(encoding="utf-8")
    if "FINAL-POLISH FIX 2-4" in cu_text:
        SKIPPED.append("core/core_utils.py: уже пропатчен")
    else:
        # Добавим helper в конец файла перед последней функцией
        # Найдём место — перед def _md_parse_inline или после неё
        marker = "def _md_parse_inline(text: str) -> list:"
        if marker in cu_text:
            # Вставляем нашу функцию ПЕРЕД _md_parse_inline
            inject = '''def _polish_timestamps_in_text(text: str) -> str:
    """FINAL-POLISH FIX 2-4: фиксы реальных багов Gemini в inline-таймкодах.

    Применять к каждой строке текста ДО _md_parse_inline:

    FIX 2: слипание текст-эмодзи     'надежды⏱ 19:50' → 'надежды ⏱ 19:50'
    FIX 3: точка после таймкода       'Богом ⏱ 15:55.' → 'Богом. ⏱ 15:55'
    FIX 4: слипание точка-bold       'тлении.**В материале:**' → 'тлении. **В материале:**'
    FIX 4b: слипание bold-bold       '**А****B**' → '**A** **B**'
    """
    if not text:
        return text

    # FIX 4: пробел между .!?:; и **bold-лидером**
    # Не трогаем уже корректные: '. **' остаётся '. **'
    text = re.sub(r'([\\.\\!\\?\\:\\;])(\\*\\*[^\\*\\n])', r'\\1 \\2', text)

    # FIX 4b: пробел между ** и ** если идут вплотную (закрывающий + открывающий)
    text = re.sub(r'(\\*\\*)(\\*\\*[^\\*\\n])', r'\\1 \\2', text)

    # FIX 2: пробел перед эмодзи-таймкодом если его нет
    # Маркеры таймкодов: ⏱, 📌, 🎯, 🔔
    text = re.sub(r'([а-яА-ЯёЁa-zA-Z0-9\\)\\]\\»\\"])([⏱📌🎯🔔])', r'\\1 \\2', text)

    # FIX 3: точка после таймкода → точка ДО таймкода
    # 'текст ⏱ M:SS.' → 'текст. ⏱ M:SS'
    # 'текст ⏱ **M:SS**.' → 'текст. ⏱ **M:SS**'
    # Только если после таймкода идёт КОНЕЦ строки или новый пробел
    text = re.sub(
        r'(\\s)([⏱📌🎯🔔]\\s*\\**\\d{1,2}:\\d{2}(?::\\d{2})?\\**)\\s*\\.(\\s|$)',
        r'.\\1\\2\\3',
        text,
    )
    # Та же история для запятой и точки с запятой
    text = re.sub(
        r'(\\s)([⏱📌🎯🔔]\\s*\\**\\d{1,2}:\\d{2}(?::\\d{2})?\\**)\\s*([,;])(\\s|$)',
        r'\\3\\1\\2\\4',
        text,
    )

    return text


'''
            cu_text2 = cu_text.replace(marker, inject + marker, 1)
            backup = cu_path.with_suffix(".py.bak_fp")
            if not backup.exists():
                shutil.copy2(cu_path, backup)
            cu_path.write_text(cu_text2, encoding="utf-8")
            CHANGED.append("core/core_utils.py: добавлен helper _polish_timestamps_in_text")
            print("    [✓] добавлен _polish_timestamps_in_text в core_utils.py")

            # Syntax check
            import ast
            try:
                ast.parse(cu_text2)
                print("    [✅ SYNTAX OK]")
            except SyntaxError as e:
                ERRORS.append(f"core_utils.py: SyntaxError {e.msg}")
                shutil.copy2(backup, cu_path)
        else:
            ERRORS.append("core/core_utils.py: не найден маркер 'def _md_parse_inline'")


# ═══════════════════════════════════════════════════════════════════════
# FIX 2+3+4 (apply): подключаем _polish_timestamps_in_text в _md_to_telegraph_nodes
# ═══════════════════════════════════════════════════════════════════════
print("\n[FIX 2+3+4 apply] Подключаю polish к Telegraph nodes")

tel_old = """from core.core_utils import ("""
tel_new = """from core.core_utils import (  # FINAL-POLISH FIX 2-4
    _polish_timestamps_in_text,"""

tel_old2 = """        # Инлайн-очистка мусора внутри строки
        s = _scrub_inline(s)
        if not s:
            continue
        # DEEP-QUALITY FIX [D]: понижаем жирные абзацы (>70% жирного → обычный текст)
        s = _demote_paragraph_bold(s)"""

tel_new2 = """        # Инлайн-очистка мусора внутри строки
        s = _scrub_inline(s)
        if not s:
            continue
        # DEEP-QUALITY FIX [D]: понижаем жирные абзацы (>70% жирного → обычный текст)
        s = _demote_paragraph_bold(s)
        # FINAL-POLISH FIX 2-4: чистим слипания таймкодов и точек
        s = _polish_timestamps_in_text(s)"""

# Если ещё не было [D] (старый файл) — fallback на простой вариант
tel_old2_alt = """        # Инлайн-очистка мусора внутри строки
        s = _scrub_inline(s)
        if not s:
            continue"""
tel_new2_alt = """        # Инлайн-очистка мусора внутри строки
        s = _scrub_inline(s)
        if not s:
            continue
        # FINAL-POLISH FIX 2-4: чистим слипания таймкодов и точек
        s = _polish_timestamps_in_text(s)"""

tel_path = Path("services/telegraph.py")
if tel_path.exists():
    tel_text = tel_path.read_text(encoding="utf-8")
    if "FINAL-POLISH FIX 2-4" in tel_text:
        SKIPPED.append("services/telegraph.py: polish уже подключён")
    elif "_polish_timestamps_in_text" in tel_text:
        SKIPPED.append("services/telegraph.py: polish уже импортирован")
    else:
        original = tel_text
        applied = 0
        # Импорт
        if tel_old in tel_text:
            tel_text = tel_text.replace(tel_old, tel_new, 1)
            applied += 1
            print("    [✓] импорт _polish_timestamps_in_text")
        else:
            print("    [⚪] импорт-паттерн не найден")
        # Вызов — пробуем сначала вариант с [D], потом без
        if tel_old2 in tel_text:
            tel_text = tel_text.replace(tel_old2, tel_new2, 1)
            applied += 1
            print("    [✓] вызов polish после _demote_paragraph_bold")
        elif tel_old2_alt in tel_text:
            tel_text = tel_text.replace(tel_old2_alt, tel_new2_alt, 1)
            applied += 1
            print("    [✓] вызов polish после _scrub_inline (alt path, без [D])")
        else:
            print("    [⚪] точка вставки вызова polish не найдена")

        if tel_text != original:
            backup = tel_path.with_suffix(".py.bak_fp")
            if not backup.exists():
                shutil.copy2(tel_path, backup)
            tel_path.write_text(tel_text, encoding="utf-8")
            # Syntax check
            import ast
            try:
                ast.parse(tel_text)
                CHANGED.append(f"services/telegraph.py: {applied} изменений")
                print("    [✅ SYNTAX OK]")
            except SyntaxError as e:
                ERRORS.append(f"telegraph.py: SyntaxError {e.msg}")
                shutil.copy2(backup, tel_path)


# ═══════════════════════════════════════════════════════════════════════
# Также применяем polish в caption.py для timestamps_lines (в caption)
# ═══════════════════════════════════════════════════════════════════════
print("\n[FIX 2 для caption] Также применяю polish к caption (на всякий)")

cap_path = Path("converters/caption.py")
if cap_path.exists():
    cap_text = cap_path.read_text(encoding="utf-8")
    if "FINAL-POLISH FIX caption" in cap_text:
        SKIPPED.append("converters/caption.py: polish уже подключён")
    elif "_polish_timestamps_in_text" in cap_text:
        SKIPPED.append("converters/caption.py: polish уже импортирован")
    else:
        # Добавим импорт в существующий блок from core.core_utils
        cap_old3 = "from core.core_utils import (  # FIX: circular imports\n    time_to_seconds,"
        cap_new3 = "from core.core_utils import (  # FIX: circular imports — FINAL-POLISH FIX caption\n    time_to_seconds,\n    _polish_timestamps_in_text,"
        if cap_old3 in cap_text:
            cap_text2 = cap_text.replace(cap_old3, cap_new3, 1)
            backup = cap_path.with_suffix(".py.bak_fp")
            if not backup.exists():
                shutil.copy2(cap_path, backup)
            cap_path.write_text(cap_text2, encoding="utf-8")
            import ast
            try:
                ast.parse(cap_text2)
                CHANGED.append("converters/caption.py: импорт polish добавлен")
                print("    [✓] импорт _polish_timestamps_in_text в caption.py")
                print("    [✅ SYNTAX OK]")
            except SyntaxError:
                shutil.copy2(backup, cap_path)
        else:
            print("    [⚪] паттерн импорта в caption.py не найден — пропуск")


# ═══════════════════════════════════════════════════════════════════════
# Итог
# ═══════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("ИТОГ")
print("=" * 70)

if CHANGED:
    print("\n✅ Применено:")
    for c in CHANGED:
        print(f"   • {c}")
if SKIPPED:
    print("\n⚪ Пропущено:")
    for s in SKIPPED:
        print(f"   • {s}")
if ERRORS:
    print("\n❌ Ошибки:")
    for e in ERRORS:
        print(f"   • {e}")
    sys.exit(1)

if not CHANGED:
    print("\n⚪ Никаких изменений (всё уже применено)")
    sys.exit(0)

print("""
📋 Следующие шаги:
   1. py -3.13 bot_new.py
   2. Проверь новый разбор в Telegraph — слипаний быть не должно
   3. git add -A
   4. git commit -m "fix: final-polish — proper model fallback + timestamp spacing + dot positioning"
   5. git push origin main

После проверки — очистка .bak_fp:
   .\\dev-tools\\scripts\\cleanup_backups.ps1
""")
