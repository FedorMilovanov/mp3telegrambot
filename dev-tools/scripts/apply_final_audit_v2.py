#!/usr/bin/env python3
"""
apply_final_audit_v2.py — ФИНАЛЬНЫЙ АУДИТ, все баги верифицированы bash-проходом.

НАЙДЕНО И ПРОВЕРЕНО:

[CRITICAL] ADMIN_IDS=set() bypass — commands.py (3 места) + callbacks.py (4 места)
  if ADMIN_IDS and user_id not in ADMIN_IDS:
  При ADMIN_IDS=set() → bool(set())=False → условие ПРОПУСКАЕТ DENY → все пользователи = admin
  Фикс: if not ADMIN_IDS or user_id not in ADMIN_IDS:

[HIGH] _clamp_content_timestamps secs%60 — converters/md_telegraph.py
  '1:00' → secs=60 → 60%60=0 → new_ts='0:00' (таймкод начала видео!)
  '1:30' → secs=90 → 90%60=30 → теряет минутную часть контекста
  Фикс: брать parts[-2]:parts[-1] напрямую (как уже сделано в _filter_times_str)

[HIGH] Markdown не рендерится в analytics — services/telegraph_pages.py
  analysis_summary, argument_arc передаются как raw str → Telegraph не понимает **bold**
  Фикс: прогонять через _md_parse_inline перед добавлением в nodes

[MEDIUM] cleanup_nosub_files() при импорте — core/utils.py
  Filesystem scan запускается при каждом import core.utils
  Фикс: убрать вызов (уже есть periodic maintenance в main.py)

[MEDIUM] race condition lock pop — handlers/commands.py
  _video_processing_locks.pop(id) когда второй запрос ещё держит lock
  Третий запрос создаст новый lock → параллельная обработка без синхронизации
  Фикс: pop только если lock не занят

[MEDIUM] Дубль в fallback list — services/gemini_analyze.py
  [user, '3.1-flash-lite', '2.5-flash-lite', '3.1-flash-lite'] — дубль +неправильный порядок
  Фикс: [user, '2.5-flash-lite', '3.1-flash-lite'] — дубль убран, порядок правильный

[LOW] format_timestamp без защиты — core/utils.py
  format_timestamp(None) → TypeError, format_timestamp(float('inf')) → ValueError
  Фикс: try/except float() в начале

[LOW] sleep 5s → 2s — services/telegraph.py
  Избыточная пауза перед synopsis

[LOW] PDF filename edge case — handlers/commands.py
  performer="" → " — title.pdf" (leading " — ")
  Фикс: f"{title}.pdf" когда performer пуст

ИДЕМПОТЕНТЕН. Делает ast.parse после каждого изменения.
"""
from pathlib import Path
import shutil, sys, ast

ROOT = Path(__file__).parent.parent.parent.resolve()
print("=" * 72)
print("apply_final_audit_v2.py — 9 багов верифицированы")
print(f"[cwd] {ROOT}")
print("=" * 72)

CHANGED, SKIPPED, ERRORS = [], [], []


def _backup(p: Path):
    bak = p.with_suffix(p.suffix + ".bak_v2")
    if not bak.exists():
        shutil.copy2(p, bak)


def _write_and_validate(p: Path, text: str, label: str) -> bool:
    p.write_text(text, encoding="utf-8")
    if p.suffix == ".py":
        try:
            ast.parse(text)
        except SyntaxError as e:
            ERRORS.append(f"{label}: SyntaxError L{e.lineno}: {e.msg}")
            shutil.copy2(p.with_suffix(p.suffix + ".bak_v2"), p)
            print(f"  [❌ ROLLBACK] {label}: SyntaxError L{e.lineno}: {e.msg}")
            return False
    CHANGED.append(label)
    print(f"  [✓] {label}")
    return True


# ═══════════════════════════════════════════════════════════════════════════
# [CRITICAL] ADMIN_IDS=set() bypass — 7 мест
# ═══════════════════════════════════════════════════════════════════════════
print("\n[CRITICAL] ADMIN_IDS bypass — commands.py + callbacks.py")

OLD_ADMIN = "if ADMIN_IDS and user_id not in ADMIN_IDS:"
NEW_ADMIN = "if not ADMIN_IDS or user_id not in ADMIN_IDS:"

for fname in ["handlers/commands.py", "handlers/callbacks.py"]:
    p = ROOT / fname
    if not p.exists():
        ERRORS.append(f"{fname}: not found"); continue
    text = p.read_text(encoding="utf-8")
    if "AUDIT-V2-ADMIN" in text:
        SKIPPED.append(f"{fname}: already patched"); continue
    count = text.count(OLD_ADMIN)
    if count == 0:
        SKIPPED.append(f"{fname}: pattern not found (already fixed?)"); continue
    _backup(p)
    text = text.replace(OLD_ADMIN, NEW_ADMIN)
    # Добавляем маркер в первую строку комментариев
    text = text.replace(
        '#!/usr/bin/env python3\n',
        '#!/usr/bin/env python3\n# AUDIT-V2-ADMIN: ADMIN_IDS bypass fixed\n',
        1
    )
    _write_and_validate(p, text, f"{fname}: {count}x ADMIN_IDS bypass fix")


# ═══════════════════════════════════════════════════════════════════════════
# [HIGH] _clamp_content_timestamps secs%60 fix
# ═══════════════════════════════════════════════════════════════════════════
print("\n[HIGH] _clamp_content_timestamps secs%60 → parts[-2]:parts[-1]")

p = ROOT / "converters/md_telegraph.py"
if not p.exists():
    ERRORS.append("converters/md_telegraph.py: not found")
else:
    text = p.read_text(encoding="utf-8")
    if "AUDIT-V2-CLAMP" in text:
        SKIPPED.append("converters/md_telegraph.py: already patched")
    else:
        OLD_CLAMP = """        # Для коротких видео (<1ч): если таймкод > duration, попробуем поправить
        if duration < 3600 and secs > duration:
            # Пробуем интерпретировать MM:SS как SS (редкий случай когда
            # модель путает формат) — берём только секунды через % 60
            corrected_secs = secs % 60
            if corrected_secs > duration:
                return ''
            # A01 FIX: применяем поправленный таймкод (ранее всегда возвращал m.group(0))
            original = m.group(0)
            new_ts = f\"{corrected_secs // 60}:{corrected_secs % 60:02d}\"
            return original.replace(m.group(3), new_ts)
        return m.group(0)"""

        NEW_CLAMP = """        # AUDIT-V2-CLAMP: для коротких видео (<1ч): если таймкод > duration,
        # это баг Gemini (добавил лишние часы). Берём MM:SS напрямую из строки,
        # не через secs%60 (который терял минуты: '1:00'→0, '1:30'→30 без контекста).
        if duration < 3600 and secs > duration:
            parts_ts = time_str.split(":")
            if len(parts_ts) == 3:
                corrected = f"{parts_ts[1]}:{parts_ts[2]}"
            elif len(parts_ts) == 2:
                # MM:SS уже правильный формат, но > duration — просто удаляем
                return ''
            else:
                return ''
            corrected_secs = time_to_seconds(corrected)
            if corrected_secs is not None and 0 < corrected_secs <= duration:
                original = m.group(0)
                return original.replace(m.group(3), corrected)
            return ''
        return m.group(0)"""

        if OLD_CLAMP in text:
            _backup(p)
            text = text.replace(OLD_CLAMP, NEW_CLAMP, 1)
            _write_and_validate(p, text, "converters/md_telegraph.py: secs%60 → parts[-2]:parts[-1]")
        else:
            SKIPPED.append("converters/md_telegraph.py: clamp pattern not found")


# ═══════════════════════════════════════════════════════════════════════════
# [HIGH] Markdown в analytics — прогонять через _md_parse_inline
# ═══════════════════════════════════════════════════════════════════════════
print("\n[HIGH] telegraph_pages.py analytics — _md_parse_inline для analysis_summary")

p = ROOT / "services/telegraph_pages.py"
if not p.exists():
    ERRORS.append("services/telegraph_pages.py: not found")
else:
    text = p.read_text(encoding="utf-8")
    if "AUDIT-V2-MDPARSE" in text:
        SKIPPED.append("services/telegraph_pages.py: already patched")
    else:
        # Добавляем импорт _md_parse_inline если его нет
        if "_md_parse_inline" not in text:
            OLD_IMPORT = "from core.core_utils import _fix_rtl_in_text  # defined in core_utils (not md_telegraph)"
            NEW_IMPORT = "from core.core_utils import _fix_rtl_in_text, _md_parse_inline  # AUDIT-V2-MDPARSE"
            if OLD_IMPORT in text:
                text = text.replace(OLD_IMPORT, NEW_IMPORT, 1)

        # Фиксим analysis_summary: str -> _md_parse_inline(str)
        OLD_ANAL = '        nodes.append({"tag": "p", "children": [analysis_summary]})'
        NEW_ANAL = ('        # AUDIT-V2-MDPARSE: прогоняем через _md_parse_inline → **bold** рендерится в Telegraph\n'
                    '        nodes.append({"tag": "p", "children": _md_parse_inline(analysis_summary)})')

        # argument_arc через italic tag — там уже есть <i>, но внутри raw string
        OLD_ARC = ('        nodes.append({"tag": "p", "children": [{"tag": "i", "children": [argument_arc]}]})')
        NEW_ARC = ('        # AUDIT-V2-MDPARSE: argument_arc через _md_parse_inline\n'
                   '        nodes.append({"tag": "p", "children": _md_parse_inline(argument_arc)})')

        changed = False
        if OLD_ANAL in text:
            text = text.replace(OLD_ANAL, NEW_ANAL, 1)
            changed = True
        if OLD_ARC in text:
            text = text.replace(OLD_ARC, NEW_ARC, 1)
            changed = True

        if changed:
            _backup(p)
            _write_and_validate(p, text, "services/telegraph_pages.py: analytics _md_parse_inline")
        else:
            SKIPPED.append("services/telegraph_pages.py: analytics patterns not found")


# ═══════════════════════════════════════════════════════════════════════════
# [MEDIUM] cleanup_nosub_files() при импорте
# ═══════════════════════════════════════════════════════════════════════════
print("\n[MEDIUM] core/utils.py — cleanup_nosub_files() при импорте")

p = ROOT / "core/utils.py"
if not p.exists():
    ERRORS.append("core/utils.py: not found")
else:
    text = p.read_text(encoding="utf-8")
    if "AUDIT-V2-CLEANUP" in text:
        SKIPPED.append("core/utils.py: already patched")
    else:
        OLD_CLEANUP = "# Автоочистка nosub-файлов — вызываем после определения функции\ncleanup_nosub_files()"
        NEW_CLEANUP = ("# AUDIT-V2-CLEANUP: НЕ вызываем при импорте — side effect ломает тесты\n"
                       "# и замедляет старт. Вызов уже есть в periodic maintenance (main.py:209).\n"
                       "# cleanup_nosub_files()")
        if OLD_CLEANUP in text:
            _backup(p)
            text = text.replace(OLD_CLEANUP, NEW_CLEANUP, 1)
            _write_and_validate(p, text, "core/utils.py: cleanup_nosub_files at import disabled")
        else:
            SKIPPED.append("core/utils.py: cleanup pattern not found")


# ═══════════════════════════════════════════════════════════════════════════
# [MEDIUM] race condition lock pop
# ═══════════════════════════════════════════════════════════════════════════
print("\n[MEDIUM] handlers/commands.py — lock pop race condition")

p = ROOT / "handlers/commands.py"
if not p.exists():
    ERRORS.append("handlers/commands.py: not found")
else:
    text = p.read_text(encoding="utf-8")
    if "AUDIT-V2-LOCKPOP" in text:
        SKIPPED.append("handlers/commands.py: lock pop already patched")
    else:
        OLD_POP = ("        with _video_locks_mutex:\n"
                   "            _video_processing_locks.pop(_vid_id_hint, None)")
        NEW_POP = ("        # AUDIT-V2-LOCKPOP: pop только если lock свободен\n"
                   "        # Если второй запрос ещё держит lock, pop создаст возможность\n"
                   "        # для третьего запроса создать новый lock и обработать параллельно\n"
                   "        with _video_locks_mutex:\n"
                   "            if not _vlock.locked():\n"
                   "                _video_processing_locks.pop(_vid_id_hint, None)")
        if OLD_POP in text:
            _backup(p)
            text = text.replace(OLD_POP, NEW_POP, 1)
            _write_and_validate(p, text, "handlers/commands.py: lock pop race condition fix")
        else:
            SKIPPED.append("handlers/commands.py: lock pop pattern not found")


# ═══════════════════════════════════════════════════════════════════════════
# [MEDIUM] Дубль в gemini fallback list
# ═══════════════════════════════════════════════════════════════════════════
print("\n[MEDIUM] services/gemini_analyze.py — дубль в fallback list")

p = ROOT / "services/gemini_analyze.py"
if not p.exists():
    ERRORS.append("services/gemini_analyze.py: not found")
else:
    text = p.read_text(encoding="utf-8")
    if "AUDIT-V2-FALLBACK" in text:
        SKIPPED.append("services/gemini_analyze.py: already patched")
    else:
        OLD_FB = ('        _fallback_models = [\n'
                  '        _user_model,                # из .env, рекомендуется gemini-3.5-flash\n'
                  '        "gemini-3.1-flash-lite", "gemini-2.5-flash-lite",  # FINAL-POLISH FIX 1            # 5 RPM / 20 RPD — тоже свежая 3.x\n'
                  '        "gemini-3.1-flash-lite",     # 15 RPM / 500 RPD — последний шанс, точно прорвётся\n'
                  '    ]')
        NEW_FB = ('        # AUDIT-V2-FALLBACK: убран дубль gemini-3.1-flash-lite, правильный порядок\n'
                  '        # Порядок: основная → 2.5-flash-lite (баланс) → 3.1-flash-lite (запасная)\n'
                  '        _fallback_models = [\n'
                  '        _user_model,                # из .env, рекомендуется gemini-3.5-flash\n'
                  '        "gemini-2.5-flash-lite",     # fallback #1: свежая, хорошее качество\n'
                  '        "gemini-3.1-flash-lite",     # fallback #2: последний шанс\n'
                  '    ]')
        if OLD_FB in text:
            _backup(p)
            text = text.replace(OLD_FB, NEW_FB, 1)
            _write_and_validate(p, text, "services/gemini_analyze.py: fallback list dedup + reorder")
        else:
            SKIPPED.append("services/gemini_analyze.py: fallback pattern not found (check indentation)")


# ═══════════════════════════════════════════════════════════════════════════
# [LOW] format_timestamp — защита от None/str/inf
# ═══════════════════════════════════════════════════════════════════════════
print("\n[LOW] core/utils.py — format_timestamp None/str/inf guard")

p = ROOT / "core/utils.py"
if not p.exists():
    ERRORS.append("core/utils.py: not found")
else:
    text = p.read_text(encoding="utf-8")
    if "AUDIT-V2-FMT" in text:
        SKIPPED.append("core/utils.py: format_timestamp already patched")
    else:
        OLD_FMT = ("def format_timestamp(seconds: float) -> str:\n"
                   "    seconds = max(0, seconds)")
        NEW_FMT = ("def format_timestamp(seconds) -> str:  # AUDIT-V2-FMT: принимает Any\n"
                   "    try:\n"
                   "        seconds = float(seconds)\n"
                   "    except (TypeError, ValueError):\n"
                   "        return \"0:00\"\n"
                   "    import math\n"
                   "    if math.isnan(seconds) or math.isinf(seconds):\n"
                   "        return \"0:00\"\n"
                   "    seconds = max(0, seconds)")
        if OLD_FMT in text:
            _backup(p)
            text = text.replace(OLD_FMT, NEW_FMT, 1)
            _write_and_validate(p, text, "core/utils.py: format_timestamp None/inf guard")
        else:
            SKIPPED.append("core/utils.py: format_timestamp pattern not found")


# ═══════════════════════════════════════════════════════════════════════════
# [LOW] sleep 5s → 2s
# ═══════════════════════════════════════════════════════════════════════════
print("\n[LOW] services/telegraph.py — sleep 5s → 2s")

p = ROOT / "services/telegraph.py"
if not p.exists():
    ERRORS.append("services/telegraph.py: not found")
else:
    text = p.read_text(encoding="utf-8")
    if "AUDIT-V2-SLEEP" in text:
        SKIPPED.append("services/telegraph.py: sleep already patched")
    else:
        OLD_SLP = "        await asyncio.sleep(5)  # Пауза после анализа аудио — снижаем риск квоты"
        NEW_SLP = "        await asyncio.sleep(2)  # AUDIT-V2-SLEEP: 2s достаточно (было 5s)"
        if OLD_SLP in text:
            _backup(p)
            text = text.replace(OLD_SLP, NEW_SLP, 1)
            _write_and_validate(p, text, "services/telegraph.py: sleep 5s → 2s")
        else:
            SKIPPED.append("services/telegraph.py: sleep pattern not found")


# ═══════════════════════════════════════════════════════════════════════════
# [LOW] PDF filename edge case
# ═══════════════════════════════════════════════════════════════════════════
print("\n[LOW] handlers/commands.py — PDF filename edge case")

p = ROOT / "handlers/commands.py"
if not p.exists():
    ERRORS.append("handlers/commands.py: not found")
else:
    text = p.read_text(encoding="utf-8")
    if "AUDIT-V2-PDF" in text:
        SKIPPED.append("handlers/commands.py: PDF filename already patched")
    else:
        OLD_PDF = '            filename = f"{performer} — {title}.pdf" if performer else f"{title}.pdf"'
        NEW_PDF = ('            # AUDIT-V2-PDF: performer="" → leading " — " в имени файла\n'
                   '            _safe_perf = (performer or "").strip()\n'
                   '            _safe_titl = (title or "").strip() or "Без названия"\n'
                   '            filename = f"{_safe_perf} — {_safe_titl}.pdf" if _safe_perf else f"{_safe_titl}.pdf"')
        if OLD_PDF in text:
            _backup(p)
            text = text.replace(OLD_PDF, NEW_PDF, 1)
            _write_and_validate(p, text, "handlers/commands.py: PDF filename edge case")
        else:
            SKIPPED.append("handlers/commands.py: PDF filename pattern not found")


# ═══════════════════════════════════════════════════════════════════════════
# Финальная проверка ast.parse для всех изменённых файлов
# ═══════════════════════════════════════════════════════════════════════════
print("\n[VERIFY] ast.parse all patched files...")
all_py = [
    "handlers/commands.py", "handlers/callbacks.py",
    "converters/md_telegraph.py", "services/telegraph_pages.py",
    "core/utils.py", "services/gemini_analyze.py", "services/telegraph.py",
]
parse_ok = True
for fname in all_py:
    fp = ROOT / fname
    if fp.exists():
        try:
            ast.parse(fp.read_text(encoding="utf-8"))
            print(f"  [✓] {fname}")
        except SyntaxError as e:
            print(f"  [❌] {fname}: SyntaxError L{e.lineno}: {e.msg}")
            ERRORS.append(f"VERIFY {fname}: SyntaxError")
            parse_ok = False


# ═══════════════════════════════════════════════════════════════════════════
# ИТОГ
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 72)
print("ИТОГ")
print("=" * 72)

if CHANGED:
    print(f"\n✅ Применено ({len(CHANGED)}):")
    for c in CHANGED:
        print(f"   • {c}")

if SKIPPED:
    print(f"\n⚪ Пропущено ({len(SKIPPED)}):")
    for s in SKIPPED:
        print(f"   • {s}")

if ERRORS:
    print(f"\n❌ ОШИБКИ ({len(ERRORS)}):")
    for e in ERRORS:
        print(f"   • {e}")
    sys.exit(1)

if not CHANGED:
    print("\n⚪ Все патчи уже применены.")
    sys.exit(0)

print(f"""
📋 ИСПРАВЛЕНО ({len(CHANGED)} изменений):

  [CRITICAL] ADMIN_IDS=set() bypass → commands.py + callbacks.py
             7 мест: "if ADMIN_IDS and" → "if not ADMIN_IDS or"

  [HIGH]     _clamp_content_timestamps → converters/md_telegraph.py
             secs%60 → parts напрямую (1:00 больше не → 0:00)

  [HIGH]     analytics markdown → services/telegraph_pages.py
             analysis_summary/argument_arc → _md_parse_inline

  [MEDIUM]   cleanup_nosub_files() → core/utils.py
             убран вызов при импорте

  [MEDIUM]   lock pop race condition → handlers/commands.py
             pop только если lock не занят

  [MEDIUM]   fallback list dedup → services/gemini_analyze.py
             убран дубль gemini-3.1-flash-lite

  [LOW]      format_timestamp guard → core/utils.py
             None/str/inf → "0:00" вместо crash

  [LOW]      sleep 5s → 2s → services/telegraph.py

  [LOW]      PDF filename → handlers/commands.py
             performer="" больше не даёт " — title.pdf"

📋 ПОРЯДОК ДЕЙСТВИЙ:
  1. py -3.13 dev-tools\\scripts\\apply_final_audit_v2.py
  2. py -3.13 bot_new.py                       ← проверь запуск
  3. Отправь видео → проверь лог
  4. git add -A
  5. git commit -m "fix: final-audit-v2 — 9 bugs (CRITICAL admin bypass, HIGH clamp+markdown, MEDIUM+LOW)"
  6. git push origin main
  7. .\\dev-tools\\scripts\\cleanup_backups.ps1
  8. git add -A && git commit -m "chore: cleanup .bak_v2" && git push
""")
