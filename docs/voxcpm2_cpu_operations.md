# VoxCPM2 CPU и экспериментальные TTS: безопасная эксплуатация

Этот runbook фиксирует рабочую конфигурацию Dub Studio и правила, которые предотвращают повторение ошибок с путями, `request.json`, лишними загрузками и смешиванием окружений.

## 1. Текущий production-контур

Для готового русского SRT используется полный локальный VoxCPM2 в CPU-режиме.

```text
Backend:        voxcpm2
Model profile:  voxcpm2-production-v1
Model archive:  C:\AI-Archive\VoxCPM2-paused-RTX3060
CPU venv:       C:\AI-Archive\VoxCPM2-CPU-TEST\.venv
Project root:   C:\AI-Archive\MP3Bot-Dub-Studio\projects\<project-id>
GPU policy:     CUDA_VISIBLE_DEVICES=-1
```

Рекомендуемый профиль для текущего ролика МакАртура:

```text
threads:          10
steps:            22
cfg:              1.8
cache_length:     4096
original_level:   0.18
russian_delay_ms: 520
```

`steps=22` — базовый профиль. Адаптивные попытки VoxCPM2 могут увеличивать число шагов только для неудачной реплики. Не поднимать все сегменты до 40–50 шагов без измеримого выигрыша.

## 2. Единственная допустимая структура request.json

Параметры модели должны храниться внутри `speech_options`:

```json
{
  "speech_backend": "voxcpm2",
  "speech_model_profile": "voxcpm2-production-v1",
  "speech_options": {
    "threads": 10,
    "steps": 22,
    "cfg": 1.8,
    "cache_length": 4096,
    "base_seed": 2026080322
  },
  "speech_backend_config": {
    "vox_archive": "C:\\AI-Archive\\VoxCPM2-paused-RTX3060",
    "cpu_venv": "C:\\AI-Archive\\VoxCPM2-CPU-TEST\\.venv"
  },
  "original_level": 0.18,
  "russian_delay_ms": 520
}
```

### Запрещено

Не хранить одновременно разные значения в двух местах:

```text
speech_options.steps = 16
request.steps         = 22
```

Текущий control plane специально останавливает запуск при таком конфликте. Не лечить ошибку случайным удалением модели, окружения или проекта.

Для ремонта использовать штатный инструмент:

```powershell
py -3.13 -m tools.voxcpm2.repair_project_request `
  --project-root "C:\AI-Archive\MP3Bot-Dub-Studio\projects\dub-ba15009b7a" `
  --write `
  --write-notes
```

Инструмент создаёт резервную копию, убирает плоские дубли, записывает каноническую вложенную конфигурацию, проверяет её production-loader и при ошибке восстанавливает исходный файл.

## 3. Правильный порядок запуска

1. Проверить наличие `request.json`, `input/ready_translation.srt` и `source/source.mp4`.
2. Проверить CPU Python и локальный snapshot VoxCPM2.
3. Нормализовать и валидировать `request.json` до загрузки модели.
4. Только после `CONFIG VALID` удалять старые промежуточные checkpoints, если изменились steps, CFG, reference или seed.
5. Запускать `tools.voxcpm2.generic_direct_runtime`.
6. Проверять `output/final_upload.mp4`, `output/russian_only.mp4` и `output/manifest.json`.

При изменении генерационного профиля разрешено очищать только:

```text
segment_work/
master_work/
references/
audio/
```

Не удалять автоматически:

```text
request.json
input/
source/
output/
VoxCPM2-paused-RTX3060/
VoxCPM2-CPU-TEST/
Whisper caches
репозиторий бота
```

## 4. Политика установок и больших загрузок

Перед любой загрузкой модели в выводе должны быть явно указаны:

1. точное имя и версия модели;
2. официальный или сторонний источник;
3. размер каждого крупного компонента и общий объём;
4. зачем нужен каждый компонент;
5. отдельные root, venv и cache;
6. способ удаления после теста;
7. будет ли использоваться CPU или GPU.

До подтверждения этих пунктов запрещено скачивать веса.

### Изоляция

Каждая экспериментальная модель получает собственную папку:

```text
C:\AI-Archive\<MODEL>-CPU
  .venv\
  .hf_cache\
  repo\
  work\
```

Не устанавливать экспериментальные пакеты:

- в глобальный Python;
- в окружение бота;
- в VoxCPM2 CPU venv;
- в общий Hugging Face cache без отдельного `HF_HOME`.

Не использовать `huggingface-cli download <repo>` без `--include`, когда репозиторий содержит несколько квантовок или полные BF16-веса.

## 5. Решения по моделям

### VoxCPM2

Текущий рабочий production-вариант для CPU. Модель и окружение уже установлены. Не переустанавливать при ошибке конфигурации или путей.

### MOSS-TTS Nano

Использован только как короткий baseline. Качество клонирования оказалось недостаточным для финального МакАртура. Установка и тестовые файлы удалены. Не восстанавливать автоматически.

### MOSS-TTS 8B v1.5

Целевая модель для будущего высококачественного клонирования. Не путать с 8B 1.0 и не называть тест 1.0 тестом v1.5.

До следующей установки:

- повторно проверить наличие официального CPU/GGUF-пути именно для v1.5;
- не скачивать 1.0 как скрытую замену;
- сделать preflight RAM, диска и температуры;
- сначала генерировать одну короткую законченную фразу;
- полный ролик запускать только после подтверждённого сходства.

### Seed-VC

Не использовать как незаявленную замену TTS-клонированию. Любой дополнительный voice-conversion этап должен быть заранее назван, обоснован и одобрен.

## 6. CPU и температура

Для i5-14600K первый production-запуск ограничивать 10 потоками. Не включать GPU автоматически.

Перед длинным рендером:

- закрыть тяжёлые приложения;
- проверить свободную RAM;
- убедиться, что файл подкачки включён;
- контролировать CPU Package и thermal throttling;
- не оставлять длительный рендер при устойчивых 94–97 °C.

Ошибка до строки загрузки модели не является CPU-нагрузкой и не требует удаления весов.

## 7. Диагностика по типу ошибки

### `Конфликт speech_options.steps и request.steps`

Причина: дубли в `request.json`. Запустить `repair_project_request`. Модель не удалять.

### `Не найден кеш VoxCPM2`

Проверить `vox_archive`. Канонический путь:

```text
C:\AI-Archive\VoxCPM2-paused-RTX3060
```

Не искать модель внутри `MP3Bot-Dub-Studio/models`, если профиль указывает отдельный archive root.

### Ошибка импорта `voxcpm`, `torch` или `soundfile`

Проверять только:

```text
C:\AI-Archive\VoxCPM2-CPU-TEST\.venv\Scripts\python.exe
```

Не чинить это установкой пакетов в Python бота.

### Генерация уже началась, но сегмент не проходит QA

Смотреть candidate report, длительность, voiced ratio, F0 и хвостовой шум. Не отключать QA и не принимать «лучший из плохих» автоматически.

## 8. Проектная запись

В каждом Dub Studio проекте должен лежать `PROJECT_TTS_OPERATIONS.md`, созданный `repair_project_request --write-notes`. В нём фиксируются:

- project ID;
- активная модель и профиль;
- точные пути runtime;
- steps, CFG, threads и seed;
- уровень оригинала и задержка;
- дата последней нормализации;
- запреты на опасную очистку и смешивание окружений.
