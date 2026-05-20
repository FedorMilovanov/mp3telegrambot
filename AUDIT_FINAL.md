# MP3Bot — Верифицированный аудит (2026-05-20)

## Резюме

| Проверка | Результат |
|---|---|
| Синтаксис всех 36 .py файлов | ✅ OK |
| Импорт всех модулей | ✅ OK |
| `audio_timestamp=True` в Gemini-конфиге | ✅ OK |
| Двойной `query.answer()` | ✅ Исправлен |
| `get_event_loop()` → `get_running_loop()` | ✅ Исправлен |
| Per-video locks cleanup при рестарте | ✅ Исправлен |
| SQLite WAL + busy_timeout | ✅ OK |
| Субтитры default + миграция БД | ✅ OK |
| **Graceful shutdown (/stop)** | ⚠️ **Всё ещё использует `os._exit(0)`** |
| **Gemini модель в .env** | 🔴 **Критично: `gemini-2.5-pro` стала paid-only в апреле 2026** |

---

## 🔴 Критично: почему Gemini "упала" и выдаёт только базовую инфу

### Диагноз
Ты используешь модель **`gemini-2.5-pro`** (видно из лога: `🧠 AI (gemini-2.5-pro): ✅`).

**С 1 апреля 2026 Google сделал Pro-модели платными.** Free tier больше не работает с `gemini-2.5-pro`. Все 4 твоих ключа получают `429 / ResourceExhausted` или `quota exceeded`, бот молча перебирает ключи, все падают, и `gemini_analyze_audio` возвращает `None`. Код корректно обрабатывает отсутствие анализа: отправляет аудио с базовым заголовком (автор, название, ссылки на YouTube/RuTube/VK) — именно то, что ты и видишь.

**Источник:** [Gemini API Pricing 2026 — metacto.com](https://www.metacto.com/blogs/the-true-cost-of-google-gemini-a-guide-to-api-pricing-and-integration) и официальный changelog Google (апрель 2026).

### Лечение (выбери одно)

**Вариант А (рекомендуется):** Переключиться на **`gemini-2.5-flash`**
- Стабильная GA-модель, работает на free tier (1500 запросов/день).
- Поддерживает всё то же самое: audio, video, 1M контекст, 65K выход.
- Дата отключения: 16 октября 2026 (потом обновим на `gemini-3.5-flash`).

**Вариант Б:** Купить/подключить платный биллинг к текущим ключам для `gemini-2.5-pro`.

**Вариант В:** Переключиться на **`gemini-3.5-flash`** (самая новая, free tier, выпущена 19 мая 2026).
- Но она пока preview и может менять поведение без предупреждения.

### Применить фикс
В файле `.env` измени:
```bash
GEMINI_MODEL=gemini-2.5-flash
```
И перезапусти бота. Никаких изменений в коде не требуется — `GEMINI_MODEL` читается из env.

---

## 🟡 Важно: `/stop` всё ещё убивает процесс через `os._exit(0)`

В `handlers/commands.py:314` внутри `_graceful()` всё ещё вызывается `os._exit(0)`. Это означает:
- SQLite соединения не закрываются корректно (возможна WAL-консистентность, но не критично при WAL).
- Фоновые задачи (Whisper preload, рендер Shorts) обрываются на полуслове.
- Файлы в `downloads/` и Gemini Files API могут остаться висеть.

### Применить фикс
См. `FIX_shutdown.patch` — заменяет `os._exit(0)` на глобальный флаг `_SHUTDOWN_REQUESTED`, который ломает цикл `while True` в `main.py:run_bot()`, позволяя Python выполнить `finally` и `atexit` корректно.

---

## 🟡 Бот не реагирует на команды (уже было, но повторю)

В логе у тебя:
```
✅ Бот запущен!
```
и тишина. Это **не баг кода**. Три вероятные причины (в порядке частоты):

1. **Двойной процесс Python.** Telegram отдаёт update только одному polling-клиенту. Если старая копия бота висит в фоне, новая молчит.
   ```powershell
   Get-Process python | Format-Table Id, ProcessName, StartTime
   # Если больше 1 — убей всех:
   Get-Process python | Stop-Process -Force
   ```

2. **Webhook не сброшен.** Если когда-то деплоил на Render, webhook может быть активен.
   ```powershell
   $token = "ТВОЙ_BOT_TOKEN"
   Invoke-RestMethod "https://api.telegram.org/bot$token/deleteWebhook?drop_pending_updates=true"
   ```

3. **Неверный токен.** Проверь:
   ```powershell
   $token = "ТВОЙ_BOT_TOKEN"
   Invoke-RestMethod "https://api.telegram.org/bot$token/getMe"
   ```

---

## 🎨 Субтитры Shorts — как включить

В коде default уже `shorts_subtitles=True` и миграция `_one_time_enable_subtitles()` есть. Но если ты раньше выключал субтитры вручную через `/settings`, в БД осталось `shorts_subtitles=0`.

**Быстрое включение:**
1. В Telegram нажми `/settings` → кнопка **💬 Субтитры** — она переключится на ✅.
2. Или выполни SQL прямо в папке бота:
   ```powershell
   cd C:\Users\Fedor\Projects\mp3telegrambot
   sqlite3 bot_cache.db "UPDATE bot_settings SET value='1' WHERE key='shorts_subtitles';"
   sqlite3 bot_cache.db "UPDATE bot_settings SET value='1' WHERE key='shorts_subtitles_karaoke';"
   ```

---

## 🅰️ Шрифты — PowerShell скрипт

Файл `install_fonts.ps1` в workspace. Запусти в PowerShell **от имени администратора**:
```powershell
Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force
.\install_fonts.ps1
```
Это скачает Montserrat ExtraBold (и Black) с Google Fonts и установит для всех пользователей.

---

## Сводка действий "сделай сейчас"

1. **Останови все Python-процессы** (двойной запуск — главная причина молчания).
2. **Поменяй в `.env`:** `GEMINI_MODEL=gemini-2.5-flash`.
3. **Примени патч `FIX_shutdown.patch`** (или руками убери `os._exit(0)`).
4. **Включи субтитры** через `/settings` или SQL.
5. **Запусти `install_fonts.ps1`** от администратора.
6. **`python bot_new.py`** — проверь, что в логе появится:
   ```
   🧠 AI (gemini-2.5-flash): ✅ (ключей: 4)
   💬 Субтитры Shorts: ✅ включены
   🅰️ Шрифт субтитров: Montserrat ExtraBold
   ```
