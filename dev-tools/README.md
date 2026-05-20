# dev-tools/

Папка для **временных** инструментов разработки: патчи, диагностические скрипты, документация для AI-ассистентов.

**ВАЖНО:** эта папка НЕ должна засоряться. После применения патча — удаляй .py-скрипт.
.patch-файлы (унифицированные diff) храним в `patches/` — их можно повторно применять и они компактные.

## Структура

```
dev-tools/
├── README.md                   # этот файл
├── docs/
│   ├── AI_GUIDELINES.md        # требования для AI: что НЕ ломать
│   ├── CHANGELOG_PATCHES.md    # хронология применённых патчей
│   └── KNOWN_ISSUES.md         # известные нерешённые баги
├── patches/                    # .patch файлы (git apply)
│   └── *.patch
└── scripts/                    # .py скрипты для разовых задач
    ├── apply_patch.ps1         # обёртка для применения .patch
    ├── cleanup_backups.ps1     # удаление .bak_* и старых .py
    └── diagnose_gemini.py      # диагностика API
```

## Как применять патчи

### Способ 1 (рекомендуется): git apply

```powershell
cd C:\Users\Fedor\Projects\mp3telegrambot
git apply dev-tools/patches/2026-05-20_deep_quality.patch
```

После успешного применения — закоммить и удали лишние .bak_* через:
```powershell
.\dev-tools\scripts\cleanup_backups.ps1
```

### Способ 2: проверить что патч применится (dry-run)

```powershell
git apply --check dev-tools/patches/2026-05-20_deep_quality.patch
```

Если ошибок нет — можно применять.

### Способ 3: откат уже применённого патча

```powershell
git apply --reverse dev-tools/patches/2026-05-20_deep_quality.patch
```

## Правило: один патч = одна задача

Каждый .patch файл решает **одну осмысленную задачу**:
- `2026-05-20_audio_upload.patch` — переход на File API
- `2026-05-20_503_retry.patch` — retry на 503
- `2026-05-20_multi_model.patch` — fallback моделей
- `2026-05-20_3_5_flash_thinking.patch` — thinking_level=high для 3.x
- `2026-05-20_deep_quality.patch` — глубокая оптимизация качества (этот)

## Очистка корня репо

В корне НЕ должно быть:
- `fix_*.py` (всё в `dev-tools/`)
- `*.bak_*` (всё удаляется после успешного коммита)
- `bot_cache.db-shm`, `bot_cache.db-wal` (SQLite WAL — не коммитим)
- `apply_fixes.py`, `diagnose_*.py` (всё в `dev-tools/scripts/`)
