#!/usr/bin/env python3
"""
apply_audit_fixes.py — фиксит баги, найденные при полном аудите репо.

КРИТИЧЕСКИЕ БАГИ (бот упадёт без этого фикса):

[BUG 1] services/telegraph.py:85 вызывает _polish_timestamps_in_text(s),
        но импорт НЕ добавлен → NameError при первом синопсисе.
        Лечим: добавляем импорт правильно (одной строкой, не tuple).

[BUG 2] converters/caption.py: DEEP-QUALITY FIX [C] добавил только комментарий,
        но не заменил код. Жирные слова в таймкодах НЕ работают (** экранируется).
        Лечим: правильно вставляем _ts_md_to_html конвертацию.

ВТОРОСТЕПЕННЫЕ:

[BUG 3] main.py whitelist — нет gemini-3.5-flash → warning при старте.
        Лечим: добавляем 3.5-flash в _KNOWN_LIVE_MODELS.

[BUG 4] Дубликат скриптов в /scripts/ — мусор от старых патчей.
        Лечим: удаляем /scripts/ полностью (правильное место — /dev-tools/scripts/).

Идемпотентен. Проверяет синтаксис.
"""
from pathlib import Path
import shutil
import sys
import os
import ast

print("=" * 70)
print("apply_audit_fixes.py — фиксит критические баги из аудита")
print("=" * 70)

ROOT = Path(__file__).parent.parent.parent.resolve()
os.chdir(ROOT)
print(f"\n[cwd] {ROOT}\n")

CHANGED, SKIPPED, ERRORS = [], [], []


def safe_patch(path, replacements, marker):
    """Применяет список замен с проверкой синтаксиса."""
    p = Path(path)
    if not p.exists():
        ERRORS.append(f"{path}: не найден")
        return False
    text = p.read_text(encoding="utf-8")
    if marker in text:
        SKIPPED.append(f"{path}: уже пропатчен ({marker})")
        return False
    original = text
    backup = p.with_suffix(p.suffix + ".bak_af")
    if not backup.exists():
        shutil.copy2(p, backup)
    applied = 0
    for old, new, name in replacements:
        if old in text:
            text = text.replace(old, new, 1)
            applied += 1
            print(f"    [✓] {name}")
        else:
            print(f"    [⚠️] {name} — паттерн не найден (возможно уже исправлено)")
    if text == original:
        SKIPPED.append(f"{path}: ничего не применилось")
        return False
    p.write_text(text, encoding="utf-8")
    if p.suffix == ".py":
        try:
            ast.parse(text)
        except SyntaxError as e:
            ERRORS.append(f"{path}: SyntaxError line {e.lineno}: {e.msg}")
            shutil.copy2(backup, p)
            print(f"    [❌ ОТКАЧЕНО] SyntaxError {e.msg}")
            return False
    CHANGED.append(f"{path}: {applied} изменений")
    return True


# ═══════════════════════════════════════════════════════════════════════
# BUG 1: services/telegraph.py — добавить импорт _polish_timestamps_in_text
# ═══════════════════════════════════════════════════════════════════════
print("[BUG 1] services/telegraph.py — добавляю отсутствующий импорт")

safe_patch(
    "services/telegraph.py",
    [(
        "from core.core_utils import _fix_rtl_in_text, _md_parse_inline  # FIX: circular imports",
        "from core.core_utils import _fix_rtl_in_text, _md_parse_inline, _polish_timestamps_in_text  # FIX: circular imports + AUDIT-FIX BUG 1",
        "импорт _polish_timestamps_in_text в одну строку"
    )],
    marker="AUDIT-FIX BUG 1"
)


# ═══════════════════════════════════════════════════════════════════════
# BUG 2: converters/caption.py — жирные слова в таймкодах
# ═══════════════════════════════════════════════════════════════════════
print("\n[BUG 2] converters/caption.py — правильная конвертация **bold** → <b>")

# Заменяем строку, которая просто escape'ит ВСЁ, на функцию которая:
#   1. Снимает ** → расщепляет на куски bold/не-bold
#   2. Экранирует каждый кусок отдельно
#   3. Bold-куски оборачивает в <b>...</b>
#   4. Применяет zero-width space после двоеточия + цифра
old_caption = '''            # DEEP-QUALITY FIX [C]: НЕ снимаем **жирный** из топиков таймкодов,
            # а конвертируем в HTML <b> для визуального акцента в Telegram caption.
            # Юзер: 'жирным в важных словах - это норм, даже круто, более визуально считываемы'
            _p = line.split(' ', 1)
            time_str = _p[0]
            topic_str = _p[1].strip() if len(_p) == 2 else ""
            # Вставляем zero-width space после ":" в топике чтобы Telegram не делал ссылку
            topic_escaped = re.sub(r':(\\d)', r':\u200b\\1', html_mod.escape(topic_str)) if topic_str else ""'''

new_caption = '''            # AUDIT-FIX BUG 2: правильно конвертируем **bold** в HTML <b>
            # для визуального акцента в темах таймкодов Telegram caption.
            # (DEEP-QUALITY FIX [C] добавил только комментарий, но не заменил код — исправлено)
            _p = line.split(' ', 1)
            time_str = _p[0]
            topic_str = _p[1].strip() if len(_p) == 2 else ""

            def _topic_bold_to_html(t: str) -> str:
                """Конвертирует **bold** → <b>bold</b>, остальное экранирует.
                Это безопаснее чем html_mod.escape потом regex по escape'нутым символам.
                """
                if not t:
                    return ""
                # Чиним непарные ** (если есть)
                _count = t.count('**')
                if _count % 2 == 1:
                    # Удаляем последний непарный
                    _last = t.rfind('**')
                    t = t[:_last] + t[_last + 2:]
                # Расщепляем по **bold**, чередующиеся куски: text, bold, text, bold, ...
                parts = re.split(r'\\*\\*([^*\\n]+)\\*\\*', t)
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
                result = re.sub(r':(\\d)', r':\u200b\\1', result)
                return result

            topic_escaped = _topic_bold_to_html(topic_str)'''

safe_patch(
    "converters/caption.py",
    [(old_caption, new_caption, "_topic_bold_to_html с правильной обработкой **bold**")],
    marker="AUDIT-FIX BUG 2"
)


# ═══════════════════════════════════════════════════════════════════════
# BUG 3: main.py — обновить whitelist моделей
# ═══════════════════════════════════════════════════════════════════════
print("\n[BUG 3] main.py — обновляю whitelist (добавляю 3.5-flash и 3-flash)")

old_main = '''    _KNOWN_LIVE_MODELS = {
        # Gemini 3
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview",
        # Gemini 2.5 (stable до 16 окт 2026, 2.5-flash/pro до 17 июня 2026)
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.5-pro-preview",
    }'''

new_main = '''    _KNOWN_LIVE_MODELS = {
        # Gemini 3.5 (GA с 19 мая 2026 — AUDIT-FIX BUG 3)
        "gemini-3.5-flash",
        # Gemini 3
        "gemini-3-flash",
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite-preview",
        # Gemini 2.5 (stable до 16 окт 2026, 2.5-flash/pro до 17 июня 2026)
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.5-pro-preview",
    }'''

safe_patch(
    "main.py",
    [(old_main, new_main, "добавлены gemini-3.5-flash + gemini-3-flash в whitelist")],
    marker="AUDIT-FIX BUG 3"
)


# ═══════════════════════════════════════════════════════════════════════
# BUG 4: удалить дубликат /scripts/ (правильное место — /dev-tools/scripts/)
# ═══════════════════════════════════════════════════════════════════════
print("\n[BUG 4] Удаляю дубликат /scripts/ (он лишний — правильное место /dev-tools/scripts/)")

scripts_dir = ROOT / "scripts"
if scripts_dir.exists() and scripts_dir.is_dir():
    # Проверяем, что в нём нет уникальных файлов
    duplicate = True
    for f in scripts_dir.iterdir():
        dev_equiv = ROOT / "dev-tools" / "scripts" / f.name
        if not dev_equiv.exists():
            print(f"    [⚠️] {f.name} есть в /scripts/ но НЕТ в /dev-tools/scripts/ — НЕ удаляю папку")
            duplicate = False
            break
    if duplicate:
        try:
            shutil.rmtree(scripts_dir)
            print(f"    [✓] удалена папка /scripts/ (всё было дубликатом)")
            CHANGED.append("удалена дубликатная папка /scripts/")
        except Exception as e:
            ERRORS.append(f"не удалось удалить /scripts/: {e}")
else:
    SKIPPED.append("/scripts/ уже не существует")


# ═══════════════════════════════════════════════════════════════════════
# Итог
# ═══════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("ИТОГ АУДИТА")
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
    print("\n❌ ОШИБКИ:")
    for e in ERRORS:
        print(f"   • {e}")
    sys.exit(1)

if not CHANGED:
    print("\n⚪ Все аудит-фиксы уже применены.")
    sys.exit(0)

print("""
📋 Дальше:
   1. py -3.13 bot_new.py
      Проверь что НЕТ NameError: _polish_timestamps_in_text (был критический баг!)
      Проверь что НЕТ warning о 'модель не в списке проверенных'

   2. Сделай тестовый прогон одного видео — должны увидеть:
      • В Telegram caption: жирные акценты в темах таймкодов (типа **слово**)
      • В Telegraph: нет слипаний 'текст⏱', нет 'текст ⏱ M:SS.'
      • Никаких NameError

   3. git add -A
   4. git commit -m "fix: audit-fixes — critical NameError in telegraph.py, bold timestamps in caption, whitelist 3.5-flash, remove duplicate /scripts/"
   5. git push origin main

После проверки — очистка .bak_af:
   .\\dev-tools\\scripts\\cleanup_backups.ps1
""")
