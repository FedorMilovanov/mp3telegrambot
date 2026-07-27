# VoxCPM2 Dub Studio

## Назначение

Dub Studio превращает Telegram-бота в пульт управления локальным
production-конвейером VoxCPM2. Telegram не держит многочасовую генерацию внутри
обработчика сообщения: отдельный worker выполняет очередь, пишет heartbeat,
сохраняет логи и продолжает с checkpoints после перезапуска.

На первом этапе в Studio зарегистрирован проверенный John Piper Shorts как
миграционный рецепт. Следующий этап — общий `generic_shorts_v1`, где меняются
только данные проекта (источник, перевод, сегменты и голосовой профиль), а не
код и PowerShell-файлы.

## Почему два процесса

- **Telegram-процесс** принимает команды, показывает статусы и уведомляет о
  результате.
- **Dub worker** последовательно выполняет CPU-задания и может пережить рестарт
  Telegram-бота.
- Состояние хранится в SQLite с WAL в `DUB_STUDIO_ROOT`.
- Одновременно исполняется только один тяжёлый VoxCPM2 job.
- Задание может быть отменено; после аварии зависший job возвращается в очередь.

## Включение

В локальный `.env`:

```dotenv
DUB_STUDIO_ENABLED=1
DUB_STUDIO_AUTOSTART_WORKER=1
DUB_STUDIO_ROOT=C:\AI-Archive\MP3Bot-Dub-Studio
```

Worker обычно запускается ботом автоматически. Ручной диагностический запуск:

```powershell
cd C:\Users\Fedor\Projects\mp3telegrambot
python -m tools.voxcpm2.dub_worker
```

Один проход без постоянного ожидания:

```powershell
python -m tools.voxcpm2.dub_worker --once
```

## Управление в Telegram

Доступ только пользователям из `ADMIN_IDS`.

```text
/dub
/dubnew john_piper_z20py4yqhyq
/dublist
/dubstatus
/dubrun
/dubrepair
/dubfiles
/dubcancel
/dubworker
```

У большинства команд ID необязателен: используется последний проект
администратора. Карточка проекта содержит inline-кнопки.

### John Piper

1. Создать проект:

```text
/dubnew john_piper_z20py4yqhyq
```

2. Если полный ролик ещё не сделан:

```text
/dubrun
```

3. Для уже готового ролика с ошибочным номером псалма:

```text
/dubrepair
```

Repair action пересчитывает только второй сегмент, заменяет его в сохранённой
русской WAV-дорожке и создаёт новые файлы `PSALM15_FIXED`. Остальные четыре
сегмента не генерируются заново.

## Безопасность

Recipe-файлы находятся только в `tools/voxcpm2/recipes`. Telegram не передаёт
worker-у произвольную shell-команду, путь или аргументы. Допустимы только:

- `.ps1` внутри `tools/voxcpm2`;
- Python-модули внутри `tools.voxcpm2.*` или `pipelines.dubbing.*`;
- заранее описанные в recipe параметры скалярного типа.

Это принципиально: управление через Telegram не превращается в удалённую
командную строку Windows.

## Хранилище

По умолчанию на Windows:

```text
C:\AI-Archive\MP3Bot-Dub-Studio\
  dub_studio.sqlite3
  worker.lock
  worker-supervisor.log
  logs\
    job-000001.log
```

Основные сущности:

- `dub_projects` — карточки проектов и текущий этап;
- `dub_jobs` — очередь и история рендеров/ремонтов;
- `dub_events` — завершения для Telegram-уведомлений;
- `dub_workers` — heartbeat, PID и текущее задание.

## Целевая универсальная архитектура

После миграционного этапа один проект будет содержать:

```text
project.json
source\
  source.mp4
transcript\
  source.srt
  source.words.json
translation\
  translation_pack.md
  pronunciation.json
voice\
  profile.json
  references\
segments\
  segments.json
  overrides.json
render\
  checkpoints\
  candidates\
  selected\
output\
  russian_only.mp4
  mixed_upload.mp4
  subtitles.srt
  subtitles.ass
  manifest.json
```

Сегмент считается чистым, если совпадает его signature:

- утверждённый spoken text;
- начало/конец и задержка;
- версия voice profile и reference;
- steps, CFG, seed;
- версия NoChew renderer.

После редакторского изменения dirty становится только соответствующий сегмент.
Именно так точечная правка вроде «шестнадцатом» → «пятнадцатом» превращается в
штатную операцию, а не в новый специальный скрипт.

## Следующие этапы

1. Вынести Piper NoChew engine из папки примера в общий
   `pipelines/dubbing/voxcpm2_renderer.py`.
2. Добавить `/dubcreate URL` и получение source/transcript.
3. Генерировать `translation_pack.md` со стабильными ID.
4. Импортировать утверждённый перевод без автоматического переписывания.
5. Добавить voice profile registry и трёхфразную калибровку.
6. Добавить универсальный `repair segments=...` вместо специальных repair
   actions.
7. Отправлять готовые MP4/SRT непосредственно из карточки проекта.

До выполнения этих этапов Studio честно показывает только зарегистрированные
production-рецепты; она не выдаёт экспериментальный generic flow за готовый.
