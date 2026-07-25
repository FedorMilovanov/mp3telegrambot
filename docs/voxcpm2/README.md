# VoxCPM2 CPU dubbing handbook

> Проектный статус на 2026-07-25. Этот документ — источник истины для локальной озвучки, будущей интеграции с LiveDub и передачи задачи другим ИИ/разработчикам.

## 1. Цель

Собрать управляемый конвейер дубляжа:

```text
YouTube / локальное видео
  -> исходный звук и субтитры
  -> проверенный русский перевод
  -> чистый референс голоса
  -> VoxCPM2 на CPU
  -> сегментная подгонка к таймкодам
  -> QA русского текста и аудио
  -> sidechain-микс русского голоса с тихим оригиналом
  -> финальный MP4 / WAV / SRT / JSON-отчёт
```

VoxCPM2 пока не включён в основной LiveDub runtime. Текущий этап — лабораторный CPU-контур и профессиональный сегментный прототип для Shorts.

---

## 2. Критически важное ограничение железа

### NVIDIA GeForce RTX 3060

Эта карта считается **подтверждённо аппаратно неисправной** для данного проекта.

Наблюдавшиеся симптомы:

- CUDA-запуск VoxCPM2 воспроизводимо сбрасывает драйвер;
- `nvlddmkm`, Event ID 153;
- LiveKernelEvent / WATCHDOG;
- временное зависание Windows;
- CUBLAS internal/execution errors;
- illegal memory access;
- сбои BF16/FP16;
- замена драйвера на 610.62 проблему не исправила;
- реболл уже выполнялся;
- CapCut иногда тоже вызывает Event ID 153, хотя отдельные длинные задачи способен завершать.

### Безусловное правило

До замены видеокарты VoxCPM2 **не запускать через CUDA** и не использовать RTX 3060 как резервный путь.

Каждый локальный скрипт обязан устанавливать переменные **до импорта PyTorch/VoxCPM**:

```python
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
```

PowerShell launcher также должен выставлять:

```powershell
$env:CUDA_VISIBLE_DEVICES = "-1"
$env:CUDA_DEVICE_ORDER = "PCI_BUS_ID"
```

Контрольная строка в каждом логе:

```text
CUDA available: False
```

Нельзя предлагать пользователю «ещё раз проверить CUDA», «попробовать другой драйвер» или «временно погонять RTX».

---

## 3. Зафиксированное локальное окружение

```text
Python:       3.11.9
voxcpm:       2.0.3
PyTorch:      2.13.0+cpu
CUDA:         False
CPU threads:  10 (первичный профиль)
LocDiT steps: 4 (черновой профиль)
```

Ключевые пути текущей Windows-машины:

```text
CPU venv:
C:\AI-Archive\VoxCPM2-CPU-TEST\.venv

Сохранённая модель / старый архив CUDA-запуска:
C:\AI-Archive\VoxCPM2-paused-RTX3060

Snapshot модели:
C:\AI-Archive\VoxCPM2-paused-RTX3060\models\voxcpm2-model-cache\models--openbmb--VoxCPM2\snapshots\bffb3df5a29440629464e5e839f4d214c8714c3d
```

Модель весит более 15 ГБ. CPU-venv использует те же сохранённые веса и не должен создавать вторую копию модели.

Оценка дополнительного дискового места:

- CPU virtualenv и зависимости: ориентировочно 2–4 ГБ;
- pip cache: ориентировочно 1–3 ГБ, после стабильного запуска можно очищать;
- WAV/JSON/Shorts: существенно меньше модели.

---

## 4. Подтверждённые тесты

### 4.1 Первый успешный CPU TTS

```text
Полученный звук:     10.72 сек.
Время синтеза:       101.37 сек.
RTF:                 9.46
Оценка для 42:23:    6.68 часа
Загрузка модели:     около 17 сек.
```

Вывод:

- CPU-контур работает;
- RTX 3060 не участвует;
- произношение русского текста хорошее;
- скорость пригодна для ночной обработки;
- один длинный вызов всё равно не является правильной архитектурой.

### 4.2 Качество первого голоса

Плюсы:

- хорошая русская дикция;
- интересный тембр;
- нет основного фонетического развала.

Минусы:

- ощущение «гаража»;
- комнатная окраска;
- шумовой/реверберационный фон;
- недостаточно сухая студийная подача.

Это привело к правилу: не маскировать плохой референс агрессивным пост-EQ, а сначала выбирать и готовить правильный reference WAV.

### 4.3 MacArthur Shorts, попытка 1

Источник:

```text
https://youtube.com/shorts/RAaSAbPj-iw
```

Ошибка при combined/ultimate cloning:

```text
The expanded size of the tensor (512) must match the existing size (550)
Target sizes: [1, 2, 512, 128]
Tensor sizes: [2, 550, 128]
```

Причина: локальный snapshot создавал KV-cache длиной 512, а один только prompt/reference prefix занимал около 550 позиций.

### 4.4 MacArthur Shorts, попытка 2

После удаления `reference_wav_path` генерация дошла дальше, но завершилась:

```text
KV cache is full
```

Это подтвердило, что проблема не в памяти Windows и не в RTX, а в фактической длине `StaticKVCache`.

### 4.5 KV-cache patch

После повторной инициализации обоих кэшей:

```python
cache_length = 8192
cache_dtype = next(model.tts_model.parameters()).dtype
cache_device = model.tts_model.device

model.tts_model.base_lm.setup_cache(
    1, cache_length, cache_device, cache_dtype
)
model.tts_model.residual_lm.setup_cache(
    1, cache_length, cache_device, cache_dtype
)
```

лог подтвердил:

```text
KV cache expanded to 8192 positions.
```

### 4.6 MacArthur Shorts, попытка 3

Синтез технически завершился:

```text
WAV:        macarthur_ru_raw.wav
Audio:      19.20 сек.
Synthesis:  213.80 сек.
RTF:        11.14
Video:      48.69 сек.
atempo:     0.394299
```

Launcher правильно отказался растягивать 19.20 секунды до 48.69:

```text
Русская дорожка слишком отличается по длительности.
atempo=0.394299 выходит за безопасный диапазон.
```

Это не ошибка FFmpeg. Это **преждевременный stop-head / неполная длинная генерация**. Растягивать такой WAV нельзя: речь станет слишком медленной, а часть перевода может отсутствовать.

---

## 5. Главный архитектурный вывод

### Нельзя

```python
model.generate(text=весь_ролик_или_вся_проповедь)
```

для 48 секунд и тем более для 42 минут.

### Нужно

1. Разбить перевод по предложениям и таймкодам.
2. Загрузить модель один раз.
3. Для каждого сегмента заново подать один и тот же чистый reference WAV.
4. Задать сегменту собственные `min_len` и `max_len`.
5. Сохранить отдельный raw WAV.
6. Умеренно подогнать его через `atempo` к целевому временному окну.
7. Собрать сегменты на единой таймлинии через `adelay + amix`.
8. Выполнить один общий loudness pass.
9. Смешать с английским оригиналом через sidechain ducking.

Это одновременно решает:

- переполнение KV-cache;
- преждевременный stop;
- дрейф тембра на длинном тексте;
- невозможность точного lip/timeline fit;
- дорогую перегенерацию всего файла из-за одной ошибки;
- восстановление после сбоя.

---

## 6. Почему `min_len` и `max_len` важнее глобального atempo

Для текущего VoxCPM2 один авторегрессионный шаг соответствует примерно:

```python
seconds_per_step = (
    model.tts_model.patch_size
    * model.tts_model.chunk_size
    / model.tts_model._encode_sample_rate
)
```

На имеющемся snapshot это около 0.16 секунды.

Для окна `target_duration`:

```python
desired_steps = target_duration / seconds_per_step
min_len = floor(desired_steps * 0.88)
max_len = ceil(desired_steps * 1.35)
```

`min_len` не даёт stop-head закончить реплику в два-три раза раньше времени. `max_len` ограничивает runaway generation.

После этого `atempo` делает только умеренную коррекцию, а не пытается «спасти» неполный WAV.

---

## 7. Режимы клонирования

VoxCPM2 поддерживает четыре фактических режима.

### 7.1 Zero-shot / Voice Design

```python
wav = model.generate(text="(описание голоса)Текст")
```

Подходит для нового голоса. Не подходит, когда нужен конкретный проповедник.

### 7.2 Reference-only

```python
wav = model.generate(
    text=target_text,
    reference_wav_path=reference_wav,
)
```

Изолированно извлекает тембр и не требует транскрипта референса.

**Проектный default для cross-language ENG -> RU.**

Причина: Ultimate/continuation способен сильнее переносить английскую артикуляцию и акцент в русскую речь.

### 7.3 Continuation

```python
wav = model.generate(
    text=target_text,
    prompt_wav_path=reference_wav,
    prompt_text=exact_reference_transcript,
)
```

Сильнее воспроизводит ритм и продолжение prompt-а. Требует дословно точной расшифровки.

### 7.4 Combined / Ultimate

```python
wav = model.generate(
    text=target_text,
    reference_wav_path=reference_wav,
    prompt_wav_path=reference_wav,
    prompt_text=exact_reference_transcript,
)
```

Обычно максимальная похожесть, но для переноса английского голоса на русский может сильнее сохранять английскую артикуляцию. Использовать как A/B-вариант после успешного reference-only результата.

---

## 8. Золотой стандарт референса

### Рекомендуется

- один говорящий;
- 5–12 секунд для первого теста;
- одна или две законченные фразы;
- без музыки;
- без аплодисментов;
- без второго голоса;
- без резких склеек внутри слова;
- без сильного зала и эха;
- без clipping;
- ровная громкость;
- максимально близкий микрофон;
- WAV mono, 16 kHz для encoder input.

### Не рекомендуется

- агрессивный `afftdn` до клонирования;
- чрезмерный noise gate;
- сильная компрессия;
- искусственное «радио»-EQ;
- MP3, перекодированный несколько раз;
- длинный reference только ради «больше данных»;
- неточная расшифровка для prompt/ultimate.

### Безопасная стартовая подготовка FFmpeg

```text
highpass=f=65,
lowpass=f=7800,
loudnorm=I=-20:LRA=7:TP=-2
```

Не добавлять `afftdn` по умолчанию. Сначала слушать исходный reference и сравнивать A/B.

---

## 9. Параметры качества

### Черновой Shorts

```text
inference_timesteps = 4
cfg_value = 2.0
threads = 10
clone_mode = reference
cache_length = 2048
```

### Финальный Shorts

```text
inference_timesteps = 8–10
cfg_value = 1.8–2.2
threads = 10–16 после A/B benchmark
clone_mode = reference или ultimate после сравнения
cache_length = 2048
```

### Длинная проповедь

- только сегменты;
- ориентир 10–30 секунд на сегмент;
- одинаковый reference на каждом сегменте;
- checkpoint JSON после каждого WAV;
- повторная генерация только плохих сегментов;
- финальная проверка faster-whisper;
- смысловая QA через Gemini по оригиналу, переводу и таймкодам.

### `optimize`

На CPU:

```python
optimize=False
```

Официальная реализация оптимизации использует `torch.compile` и ориентирована на CUDA. Не пытаться включать её для текущего CPU-профиля.

### Denoiser

Первичный профиль:

```python
load_denoiser=False
denoise=False
```

Причины:

- меньше RAM;
- нет дополнительного скачивания;
- меньше скрытых преобразований референса;
- проще A/B-анализ.

`load_denoiser=True` проверять отдельно только после рабочего сегментного конвейера.

---

## 10. Профессиональная подгонка таймингов

### Для каждого сегмента

1. Генерировать raw WAV с ограничениями длины.
2. Измерить длительность через `ffprobe`.
3. Вычислить:

```text
atempo = raw_duration / target_duration
```

4. Применить цепочку `atempo`, если значение выходит за диапазон одного фильтра 0.5–2.0.
5. Добавить короткие fade-in/fade-out.
6. `apad` + `atrim` до точной длительности окна.
7. Разместить сегмент через `adelay=start_ms`.
8. Сложить `amix=normalize=0`.
9. Один общий `loudnorm` до -16 LUFS / -1.5 dBTP.

### Защитные пороги

Если raw-сегмент короче целевого окна более чем примерно на 35% или длиннее более чем на 65%, не маскировать проблему экстремальным `atempo`: перегенерировать сегмент или исправить текст/min_len/max_len.

---

## 11. Финальный микс

Желаемый результат:

- русский голос впереди;
- английский оригинал остаётся различимым;
- во время русской речи английский приглушается sidechain-компрессором;
- в паузах оригинал возвращается;
- финальные слова не обрезаются;
- видео обычно копируется без перекодирования.

Принцип:

```text
RU -> asplit -> sidechain key + final mix
EN -> volume -> sidechaincompress keyed by RU
EN ducked + RU -> amix -> limiter
```

Это согласуется с существующим `services/livedub_mix.py`, где уже реализованы sidechain, loudness alignment, tail guard и сохранение чистых дорожек.

---

## 12. Интеграция с mp3telegrambot

Будущая интеграция должна быть отдельным сервисом, а не кодом внутри handler-а.

Предлагаемая структура:

```text
services/
  voxcpm2_runtime.py       # загрузка модели, CPU guard, cache setup
  voxcpm2_reference.py     # поиск/оценка/подготовка референса
  voxcpm2_segmenter.py     # SRT/перевод -> сегменты
  voxcpm2_synth.py         # segment generation + checkpoint
  voxcpm2_timeline.py      # atempo/adelay/amix
  voxcpm2_qa.py            # ASR + смысловая проверка

pipelines/
  livedub_voxcpm2.py       # orchestration
```

Основной pipeline не должен импортировать тяжёлый PyTorch при обычном старте Telegram-бота. Модель загружается лениво только для выбранного режима.

Требования:

- lock: одновременно один CPU synthesis job;
- отмена через существующий `/stop`;
- прогресс по сегментам;
- возобновление после сбоя;
- кэш reference WAV по source ID + speaker window;
- кэш сегментов по hash текста, reference и параметров;
- raw/fitted/final дорожки не удалять до успешного QA;
- не публиковать результат, если отсутствуют сегменты или duration check не прошёл;
- маркировать синтетический дубляж для прозрачности.

---

## 13. Текущий MacArthur V2

Сегменты построены по исходным английским SRT:

```text
00:00.000–00:05.120
00:05.120–00:10.880
00:10.880–00:16.960
00:16.960–00:24.160
00:24.720–00:32.600
00:33.200–00:39.680
00:39.680–00:48.000
```

Перевод:

1. «Харизматическое движение само по себе ничего не добавило к ясности Библии.»
2. «Оно ничего не добавило ни к толкованию Писания, ни к здравому учению.»
3. «Здравое учение существовало задолго до появления харизматического движения.»
4. «От верных служителей вплоть до апостолов к нам тянется ясный поток истины.»
5. «Это движение ничего к нему не добавляет. Оно лишь умаляет истину и вносит путаницу.»
6. «Спасались ли люди в харизматических церквях и через проповедь харизматических проповедников? Да, спасались.»
7. «Но ничто, исходившее от этого движения, не было причиной их спасения.»

Первый V2-прогон должен использовать `reference-only`, 4 шага и cache 2048. Ultimate проверяется только вторым A/B-прогоном.

---

## 14. Диагностика известных ошибок

### `from` / `import` не распознаётся PowerShell

Причина: Python-код вставлен прямо в PowerShell.

Решение: давать пользователю PowerShell-команды или готовый ZIP. Не просить вручную вставлять многострочный Python.

### ZIP не найден

Проверить реальный путь и возможное браузерное переименование `(1)`. Не продолжать после `Test-Path=False`.

### `UnicodeEncodeError: charmap`

В Python:

```python
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")
```

В окружении:

```text
PYTHONUTF8=1
PYTHONIOENCODING=utf-8
```

### `unexpected keyword argument 'seed'`

Локальная установленная версия API не принимала `seed`. Не копировать сигнатуру из другой ветки без проверки `inspect.signature(model.generate)`.

### `512 must match 550`

Prompt/reference prefix больше выделенного cache. Переинициализировать оба LM cache после загрузки модели.

### `KV cache is full`

Слишком короткий cache и/или слишком длинный single-pass. Для Shorts-сегментов использовать cache 2048; для эксперимента с длинным проходом максимум 8192, но архитектурно всё равно сегментировать.

### Генерация завершилась слишком рано

Использовать рассчитанный `min_len`, а не экстремальный `atempo`.

### «Гараж», шум, помещение

Проверить reference WAV до модели. Сравнить raw source, lightly filtered reference и output. Не лечить всё пост-EQ. Переключить Ultimate -> Reference-only. Вырезать другой чистый участок.

### RAM около 75%

Для 32 ГБ это допустимо, если остаётся несколько гигабайт available и нет активного thrashing/pagefile. Не запускать параллельно CapCut и тяжёлый браузерный workload.

---

## 15. Источники: 40+ первичных ссылок

### Официальные VoxCPM2

1. https://github.com/OpenBMB/VoxCPM
2. https://voxcpm.readthedocs.io/
3. https://voxcpm.readthedocs.io/en/latest/quickstart.html
4. https://voxcpm.readthedocs.io/en/latest/installation.html
5. https://voxcpm.readthedocs.io/en/latest/usage_guide.html
6. https://voxcpm.readthedocs.io/en/latest/reference/changelog.html
7. https://huggingface.co/openbmb/VoxCPM2
8. https://pypi.org/project/voxcpm/
9. https://pypi.org/project/voxcpm/2.0.3/
10. https://arxiv.org/abs/2509.24650
11. https://github.com/OpenBMB/VoxCPM/blob/main/app.py
12. https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/core.py
13. https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/model/voxcpm2.py
14. https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/modules/minicpm4/cache.py
15. https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/modules/minicpm4/model.py

### Официальные issues: реальные ограничения и edge cases

16. https://github.com/OpenBMB/VoxCPM/issues/52
17. https://github.com/OpenBMB/VoxCPM/issues/62
18. https://github.com/OpenBMB/VoxCPM/issues/136
19. https://github.com/OpenBMB/VoxCPM/issues/209
20. https://github.com/OpenBMB/VoxCPM/issues/213
21. https://github.com/OpenBMB/VoxCPM/issues/219
22. https://github.com/OpenBMB/VoxCPM/issues/248
23. https://github.com/OpenBMB/VoxCPM/issues/256
24. https://github.com/OpenBMB/VoxCPM/issues/285
25. https://github.com/OpenBMB/VoxCPM/issues/293
26. https://github.com/OpenBMB/VoxCPM/issues/296
27. https://github.com/OpenBMB/VoxCPM/issues/302
28. https://github.com/OpenBMB/VoxCPM/issues/316
29. https://github.com/OpenBMB/VoxCPM/issues/321
30. https://github.com/OpenBMB/VoxCPM/issues/323
31. https://github.com/OpenBMB/VoxCPM/issues/338
32. https://github.com/OpenBMB/VoxCPM/issues/342
33. https://github.com/OpenBMB/VoxCPM/issues/344
34. https://github.com/OpenBMB/VoxCPM/issues/357
35. https://github.com/OpenBMB/VoxCPM/issues/359
36. https://github.com/OpenBMB/VoxCPM/issues/360

### Первичные источники по runtime и аудио

37. https://ffmpeg.org/ffmpeg-filters.html#atempo
38. https://ffmpeg.org/ffmpeg-filters.html#adelay
39. https://ffmpeg.org/ffmpeg-filters.html#amix
40. https://ffmpeg.org/ffmpeg-filters.html#sidechaincompress
41. https://ffmpeg.org/ffmpeg-filters.html#loudnorm
42. https://pytorch.org/docs/stable/generated/torch.set_num_threads.html
43. https://pytorch.org/docs/stable/generated/torch.inference_mode.html
44. https://python-soundfile.readthedocs.io/
45. https://github.com/yt-dlp/yt-dlp
46. https://huggingface.co/docs/huggingface_hub/en/guides/manage-cache
47. https://huggingface.co/docs/safetensors/index

---

## 16. Правила обновления этого документа

После каждого значимого теста записывать:

- дата;
- source URL / file;
- точный commit/version package;
- clone mode;
- reference interval;
- текст сегмента;
- steps, cfg, min_len, max_len, cache length, threads;
- load time;
- synthesis time;
- raw duration;
- fitted duration;
- RTF;
- субъективное качество;
- ошибки;
- путь к WAV/JSON/log.

Не заменять подтверждённые факты предположениями. Экспериментальный совет помечать как экспериментальный.
