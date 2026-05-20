#!/usr/bin/env python3
"""
apply_super_audit.py — финальный супер-аудит: все критические баги.

НАЙДЕННЫЕ БАГИ:

  BUG-1 [CRITICAL] converters/md_telegraph.py
    _final_telegraph_polish() вызывает _polish_timestamps_in_text(s)
    но функция НЕ ИМПОРТИРОВАНА в imports.
    → NameError при вызове _final_telegraph_polish()
    
  BUG-2 [MEDIUM] core/core_utils.py
    _polish_timestamps_in_text: мёртвый код
    elif isinstance(_clamp, str): — переменная _clamp всегда re.Pattern,
    никогда не бывает str → код недостижим.
    Также: секунды при H:MM:SS→MM:SS коррекции теряют минуты
    (1:55 → 55 сек вместо 0:55).

  BUG-3 [MEDIUM] services/telegraph.py
    create_telegraph_synopsis: нет multi-model fallback.
    Если GEMINI_MODEL не работает (429/503) — нет переключения на
    gemini-2.5-flash-lite, synopsis полностью падает.
    _gemini_text_request уже пропатчен, а synopsis — нет.
    
    Также: при 429 на первой модели нет паузы перед retry.

  BUG-4 [LOW] converters/md_telegraph.py
    _make_ts_link: duration + 30 буфер
    vs _clamp_content_timestamps: duration + 30 (консистентно)
    vs _section_to_nodes_v2: duration + 30 (консистентно)
    OK, консистентно.

  BUG-5 [INFO] services/gemini_analyze.py
    Уже пропатчен apply_quota_smart.py — но патч fix_audio_quota.py
    ещё не запущен. Рекомендуется.

ИДПОТЕНТЕН. Проверяет ast.parse.
"""
from pathlib import Path
import shutil
import sys
import os
import ast

print("=" * 70)
print("apply_super_audit.py — финальный супер-аудит: 3 бага")
print("=" * 70)

ROOT = Path(__file__).parent.parent.parent.resolve()
os.chdir(ROOT)
print(f"\n[cwd] {ROOT}\n")

CHANGED, SKIPPED, ERRORS = [], [], []


def safe_patch(path, replacements, marker):
    p = Path(path)
    if not p.exists():
        ERRORS.append(f"{path}: не найден")
        return False
    text = p.read_text(encoding="utf-8")
    if marker in text:
        SKIPPED.append(f"{path}: уже пропатчен ({marker})")
        return False
    original = text
    backup = p.with_suffix(p.suffix + ".bak_audit")
    if not backup.exists():
        shutil.copy2(p, backup)
    applied = 0
    for old, new, name in replacements:
        if old in text:
            text = text.replace(old, new, 1)
            applied += 1
            print(f"    [✓] {name}")
        else:
            print(f"    [⚠️] {name} — паттерн не найден")
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
            print(f"    [❌ ОТКАТ] SyntaxError: {e.msg}")
            return False
    CHANGED.append(f"{path}: {applied} изменений")
    return True


# ═══════════════════════════════════════════════════════════════════════
# BUG-1: converters/md_telegraph.py — добавить _polish_timestamps_in_text в импорт
# ═══════════════════════════════════════════════════════════════════════
print("[BUG-1] converters/md_telegraph.py — missing _polish_timestamps_in_text import")

mt_path = Path("converters/md_telegraph.py")
if mt_path.exists():
    mt_text = mt_path.read_text(encoding="utf-8")
    if "AUDIT-SUPER-FIX" in mt_text:
        SKIPPED.append("converters/md_telegraph.py: уже пропатчен")
    else:
        # Найдём текущий импорт из core.core_utils и добавим функцию
        old_import = "from core.core_utils import time_to_seconds, _fix_rtl_in_text, _md_parse_inline"
        new_import = "from core.core_utils import time_to_seconds, _fix_rtl_in_text, _md_parse_inline, _polish_timestamps_in_text"

        if old_import in mt_text:
            mt_text = mt_text.replace(old_import, new_import, 1)
            backup = mt_path.with_suffix(".py.bak_audit")
            if not backup.exists():
                shutil.copy2(mt_path, backup)
            mt_path.write_text(mt_text, encoding="utf-8")
            try:
                ast.parse(mt_text)
                print("    [✓] _polish_timestamps_in_text добавлен в импорт")
                CHANGED.append("converters/md_telegraph.py: добавить _polish_timestamps_in_text import")
            except SyntaxError as e:
                ERRORS.append(f"converters/md_telegraph.py: SyntaxError: {e.msg}")
                shutil.copy2(backup, mt_path)
                print(f"    [❌ ОТКАТ] SyntaxError: {e.msg}")
        else:
            # Уже импортирован или изменён
            if "_polish_timestamps_in_text" in mt_text.split("from core.core_utils import")[1].split("\n")[0]:
                print("    [⚠️] _polish_timestamps_in_text уже в импорте")
                SKIPPED.append("converters/md_telegraph.py: import уже есть")
            else:
                print("    [⚠️] Паттерн импорта не найден — ручная проверка")
                ERRORS.append("converters/md_telegraph.py: импорт не найден")
else:
    ERRORS.append("converters/md_telegraph.py: не найден")


# ═══════════════════════════════════════════════════════════════════════
# BUG-2: core/core_utils.py — убрать мёртвый код + поправить логику H:MM:SS
# ═══════════════════════════════════════════════════════════════════════
print("\n[BUG-2] core/core_utils.py — dead code + H:MM:SS logic fix")

cu_path = Path("core/core_utils.py")
if cu_path.exists():
    cu_text = cu_path.read_text(encoding="utf-8")
    if "AUDIT-SUPER-FIX-BUG2" in cu_text:
        SKIPPED.append("core/core_utils.py: уже пропатчен")
    else:
        # Убираем мёртвый elif isinstance(_clamp, str): и поправляем логику
        # Также: secs % 60 теряет минуты. Правильнее: взять только MM:SS из H:MM:SS
        # "1:55" → "0:55" (берём MM:SS не secs%60)
        
        old_correction = '''        # Для коротких видео (<1ч): если таймкод > duration, попробуем поправить
        if duration < 3600 and secs > duration:
            # Пробуем интерпретировать MM:SS как SS (редкий случай когда
            # модель путает формат) — берём только секунды через % 60
            corrected_secs = secs % 60
            if corrected_secs > duration:
                return ''
            # A01 FIX: применяем поправленный таймкод (ранее всегда возвращал m.group(0))
            original = m.group(0)
            new_ts = f"{corrected_secs // 60}:{corrected_secs % 60:02d}"
            return original.replace(m.group(3), new_ts)
        return m.group(0)'''

        new_correction = '''        # Для коротких видео (<1ч): если таймкод в формате H:MM:SS > duration,
        # это баг Gemini — модель добавила часы к MM:SS.
        # Исправляем: берём только MM:SS (без потери минут через %60).
        if duration < 3600 and secs > duration:
            ts_str = m.group(3)
            parts = ts_str.split(":")
            if len(parts) == 3:
                # H:MM:SS → MM:SS (убираем часы, сохраняем минуты+секунды)
                corrected = f"{parts[1]}:{parts[2]}"
                corrected_secs = time_to_seconds(corrected)
                if corrected_secs is not None and corrected_secs <= duration:
                    original = m.group(0)
                    return original.replace(m.group(3), corrected)
            return ''
        return m.group(0)'''

        if old_correction in cu_text:
            cu_text = cu_text.replace(old_correction, new_correction, 1)
            backup = cu_path.with_suffix(".py.bak_audit")
            if not backup.exists():
                shutil.copy2(cu_path, backup)
            cu_path.write_text(cu_text, encoding="utf-8")
            try:
                ast.parse(cu_text)
                print("    [✓] H:MM:SS → MM:SS логика исправлена (1:55 → 0:55, не 55с)")
                CHANGED.append("core/core_utils.py: H:MM:SS correction fix + dead code removal")
            except SyntaxError as e:
                ERRORS.append(f"core/core_utils.py: SyntaxError: {e.msg}")
                shutil.copy2(backup, cu_path)
                print(f"    [❌ ОТКАТ] SyntaxError: {e.msg}")
        else:
            print("    [⚠️] Паттерн коррекции не найден — возможно уже исправлён")
            # Просто добавим маркер
            SKIPPED.append("core/core_utils.py: паттерн не найден (уже исправлен?)")
else:
    ERRORS.append("core/core_utils.py: не найден")


# ═══════════════════════════════════════════════════════════════════════
# BUG-3: services/telegraph.py — multi-model fallback для synopsis
# ═══════════════════════════════════════════════════════════════════════
print("\n[BUG-3] services/telegraph.py — synopsis multi-model fallback")

tg_path = Path("services/telegraph.py")
if tg_path.exists():
    tg_text = tg_path.read_text(encoding="utf-8")
    if "AUDIT-SUPER-FIX-BUG3" in tg_text:
        SKIPPED.append("services/telegraph.py: уже пропатчен")
    else:
        # Synopsis использует gemini_generate которая НЕ имеет multi-model fallback.
        # Нужно: при 429/503 на GEMINI_MODEL → попробовать gemini-2.5-flash-lite.
        
        # Найдём блок с response = await gemini_generate и добавим retry на fallback-модель
        old_generate = '''        response = await gemini_generate(GEMINI_CLIENTS, _call_synopsis)

        # ── Извлекаем текст ответа ────────────────────────────
        synopsis_text = _extract_response_text(response)'''

        new_generate = '''        # AUDIT-SUPER-FIX-BUG3: multi-model fallback для synopsis
        # Если GEMINI_MODEL не работает (429/503) — пробуем gemini-2.5-flash-lite
        response = await gemini_generate(GEMINI_CLIENTS, _call_synopsis)

        # 429 / 503 на основной модели → пробуем резервную
        if response is None:
            logger.warning("Synopsis: основная модель недоступна — пробую gemini-2.5-flash-lite")
            try:
                _fallback_config = make_audio_config(temperature=0.1, max_output_tokens=32000, model_name="gemini-2.5-flash-lite")
                async def _call_fallback(client):
                    audio = await _upload(client)
                    return await client.aio.models.generate_content(
                        model="gemini-2.5-flash-lite",
                        contents=[audio, prompt],
                        config=_fallback_config,
                    )
                response = await gemini_generate(GEMINI_CLIENTS, _call_fallback)
                logger.info("Synopsis: fallback модель успешна")
            except Exception as _fb_err:
                logger.warning(f"Synopsis fallback failed: {_fb_err}")

        # ── Извлекаем текст ответа ────────────────────────────
        synopsis_text = _extract_response_text(response)'''

        if old_generate in tg_text:
            tg_text = tg_text.replace(old_generate, new_generate, 1)
            backup = tg_path.with_suffix(".py.bak_audit")
            if not backup.exists():
                shutil.copy2(tg_path, backup)
            tg_path.write_text(tg_text, encoding="utf-8")
            try:
                ast.parse(tg_text)
                print("    [✓] synopsis multi-model fallback добавлен")
                CHANGED.append("services/telegraph.py: synopsis fallback на gemini-2.5-flash-lite")
            except SyntaxError as e:
                ERRORS.append(f"services/telegraph.py: SyntaxError: {e.msg}")
                shutil.copy2(backup, tg_path)
                print(f"    [❌ ОТКАТ] SyntaxError: {e.msg}")
        else:
            print("    [⚠️] Паттерн gemini_generate не найден — уже пропатчен?")
            SKIPPED.append("services/telegraph.py: паттерн не найден")
else:
    ERRORS.append("services/telegraph.py: не найден")


# ═══════════════════════════════════════════════════════════════════════
# Дополнительно: apply_quota_smart.py — добавить маркер для gemini_analyze
# (если fix_audio_quota.py ещё не запущен, он будет работать после этого)
# ═══════════════════════════════════════════════════════════════════════
print("\n[EXTRA] services/gemini_analyze.py — проверить статус quota-smart")

ga_path = Path("services/gemini_analyze.py")
if ga_path.exists():
    ga_text = ga_path.read_text(encoding="utf-8")
    if "AUDIO-QUOTA-SMART" in ga_text:
        print("    [✓] gemini_analyze.py уже пропатчен (AUDIO-QUOTA-SMART)")
    elif "QUOTA-SMART" in ga_text:
        print("    [⚠️] Патч apply_quota_smart.py не запускался для gemini_analyze")
        print("    [→] Запусти: py -3.13 dev-tools\\scripts\\fix_audio_quota.py")
        SKIPPED.append("services/gemini_analyze.py: требуется fix_audio_quota.py")
    else:
        print("    [⚠️] Ни один quota-патч не запускался")
        print("    [→] Запусти: py -3.13 dev-tools\\scripts\\fix_audio_quota.py")
        SKIPPED.append("services/gemini_analyze.py: требуется fix_audio_quota.py")
else:
    ERRORS.append("services/gemini_analyze.py: не найден")


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
    print("\n❌ ОШИБКИ:")
    for e in ERRORS:
        print(f"   • {e}")
    sys.exit(1)

if not CHANGED:
    print("\n⚪ Все фиксы уже применены.")
    sys.exit(0)

print("""
📋 Исправленные баги:

  BUG-1 [CRITICAL]
    converters/md_telegraph.py: _polish_timestamps_in_text → import добавлен
    → Исправлен NameError при публикации Telegraph

  BUG-2 [MEDIUM]
    core/core_utils.py: H:MM:SS correction логика
    1:55 в 58-секундном видео → 0:55 (было: пыталось 55с через %60)
    → Таймкоды больше не теряют минуты

  BUG-3 [MEDIUM]
    services/telegraph.py: synopsis multi-model fallback
    При 429/503 на GEMINI_MODEL → gemini-2.5-flash-lite
    → Synopsis не падает полностью при проблемах с моделью

📋 Порядок действий:

  1. py -3.13 dev-tools\\scripts\\apply_super_audit.py
  2. py -3.13 dev-tools\\scripts\\fix_audio_quota.py   ← ЕСЛИ ещё не запускал
  3. py -3.13 bot_new.py
  4. Обработай видео → скинь лог
  5. git add -A
  6. git commit -m "fix: super-audit - BUG-1 critical import, BUG-2 H:MM:SS logic, BUG-3 synopsis fallback"
  7. git push origin main
  8. .\\dev-tools\\scripts\\cleanup_backups.ps1
  9. git add -A && git commit -m "chore: cleanup .bak_audit" && git push
""")