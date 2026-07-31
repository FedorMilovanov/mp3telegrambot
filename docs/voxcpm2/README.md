# VoxCPM2 CPU dubbing handbook

> Актуальный источник истины для локальной CPU-озвучки, будущей интеграции с LiveDub и передачи задачи другим ИИ/разработчикам. Обновлено 26 июля 2026 года.
>
> Актуальное сравнение VoxCPM2 с Qwen3-TTS, Chatterbox V3, Fish S2-Pro, CosyVoice3 и другими моделями: [`MODEL_COMPARISON_2026-08-01.md`](MODEL_COMPARISON_2026-08-01.md).

## 1. Цель проекта

Собрать управляемый конвейер:

```text
YouTube / локальное видео
  -> исходный звук и субтитры
  -> проверенный русский перевод
  -> выбранные референсы голоса
  -> VoxCPM2 строго на CPU
  -> несколько кандидатов каждого смыслового блока
  -> NoChew / endpoint / content QA
  -> точная таймлиния
  -> постоянный уровень оригинала без sidechain
  -> двухпроходный master
  -> MP4 / WAV / SRT / JSON-отчёты
```

VoxCPM2 пока не импортируется основным Telegram bot runtime. Текущий этап — стабильный лабораторный production-контур для Shorts и подготовка архитектуры длинных проповедей.

## 2. Текущий статус

```text
Подтверждённая база: V3.2 NoChew
Финальный эксперимент: Steps 16 / CFG 1.80
Статус публикации: ещё не утверждён владельцем
```

Владелец оценил V3.2 как первый действительно удачный полный результат:

- значительно лучше прежних версий;
- нет повторяющегося проглатывания слов;
- окончания фраз звучат чётко;
- исчезло характерное «жевание» после смысловых блоков;
- полный ролик собран и звучит связно.

Следующие изменения не должны ухудшать endpoint-поведение V3.2.

Точное текущее состояние:

```text
docs/voxcpm2/CURRENT_STATE.md
```

## 3. Безусловное аппаратное ограничение

NVIDIA GeForce RTX 3060 считается подтверждённо аппаратно неисправной для этого проекта.

Наблюдались:

- CUDA-сбросы драйвера;
- `nvlddmkm`, Event ID 153;
- LiveKernelEvent / WATCHDOG;
- временное зависание Windows;
- CUBLAS internal/execution errors;
- illegal memory access;
- BF16/FP16 failures;
- замена драйвера проблему не исправила;
- реболл уже выполнялся.

До замены карты VoxCPM2 нельзя запускать через CUDA и нельзя использовать RTX как fallback.

До импорта PyTorch/VoxCPM каждый процесс обязан установить:

```python
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
```

PowerShell launcher обязан сделать то же:

```powershell
$env:CUDA_VISIBLE_DEVICES = "-1"
$env:CUDA_DEVICE_ORDER = "PCI_BUS_ID"
```

Контрольная строка:

```text
CUDA available: False
```

## 4. Зафиксированное окружение

```text
Python:        3.11.9
voxcpm:        2.0.3
PyTorch:       2.13.0+cpu
CPU threads:   10
CPU venv:      C:\AI-Archive\VoxCPM2-CPU-TEST\.venv
Model archive: C:\AI-Archive\VoxCPM2-paused-RTX3060
Final package: C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL
```

Snapshot:

```text
C:\AI-Archive\VoxCPM2-paused-RTX3060\models\voxcpm2-model-cache\models--openbmb--VoxCPM2\snapshots\bffb3df5a29440629464e5e839f4d214c8714c3d
```

Модель уже сохранена локально. Нельзя создавать вторую копию 15+ ГБ без необходимости.

## 5. Подтверждённые измерения

Первый CPU smoke test:

```text
Полученный звук: 10.72 сек.
Синтез:          101.37 сек.
RTF:             9.46
Оценка 42:23:    6.68 часа
```

Первый длинный single-pass MacArthur:

```text
Видео:            48.69 сек.
Русский output:   19.20 сек.
Синтез:           213.80 сек.
RTF:              11.14
Нужный atempo:    0.394299
```

Это был неполный output, а не проблема FFmpeg. Растягивать его до длины видео нельзя.

Подробная история:

```text
docs/voxcpm2/EXPERIMENT_LOG.md
```

## 6. Архитектурный вывод

### Нельзя

```python
model.generate(text=весь_ролик_или_вся_проповедь)
```

### Нужно

1. Разбить перевод на смысловые блоки.
2. Загрузить модель один раз.
3. Для каждого блока снова подать approved reference.
4. Создать несколько кандидатов с записанными seed.
5. Проверить длину, clipping, edge silence и post-silence restart.
6. Выбрать лучший кандидат.
7. Не замедлять короткий output.
8. Ускорять только действительно длинный output в безопасном диапазоне.
9. Добавить тишину до точного временного окна.
10. Разместить блоки через `adelay`.
11. Собрать одну timeline WAV.
12. Выполнить master только после сборки.

Это решает:

- переполнение KV-cache;
- premature stop длинного текста;
- drift голоса;
- локальную замену плохого блока;
- восстановление после сбоя;
- точное сохранение исходных пауз.

## 7. Главный урок NoChew

Старые версии рассчитывали `min_len` примерно как 88–95% временного окна. Это оказалось ошибкой.

Фактический дефект V3.1:

```text
нормальная речь -> пауза -> слабое повторное бормотание
```

Фраза уже закончилась, но высокий `min_len` запрещал stop-head остановиться.

Текущее правило:

```text
Первая попытка: min_len=2
Повышение: только для доказанно неполной повторной попытки
Запрещено: выводить min_len из 90%+ subtitle window
```

`retry_badcase=True` не заменяет QA. Он не понимает:

- пропущенное слово;
- повтор слога;
- clipped final consonant;
- pause-then-chewing restart;
- voice drift;
- английский accent leakage.

Нужна собственная проверка кандидатов.

## 8. Правило duration fit

### Запрещено

Замедлять короткий WAV только ради заполнения окна:

```text
atempo < 1
```

Так растягиваются:

- паузы;
- дыхание;
- шум;
- слабые хвосты;
- модельные артефакты.

### Текущее правило

```text
Короткий output: сохранить естественную скорость и дополнить тишиной
Длинный output: умеренно ускорить
```

Никакой end fade не должен касаться произнесённой части. Fade допустим только внутри уже обнаруженной тишины после подтверждённого дефектного хвоста.

## 9. Текущая схема MacArthur

Источник:

```text
https://youtube.com/shorts/RAaSAbPj-iw
```

Production timing plan:

```text
1: 00.000-10.880 — B extended
2: 10.880-24.160 — B extended
3: 24.720-32.600 — B extended
4: 33.200-48.694 — C composite
```

Файл:

```text
tools/voxcpm2/examples/macarthur_raasabpj_iw/segments_ru_final.json
```

Почему четыре блока, а не семь:

- меньше hard prosodic resets;
- более цельная интонация;
- меньше искусственных склеек;
- сохранены естественные исходные паузы.

## 10. Reference strategy

Сравнивались:

```text
A: 10.88 сек., reference-only
B: 24 сек., reference-only
C: около 21 сек., composite reference-only
D: 10.88 сек., Ultimate
```

Результат owner listening:

- B — лучший основной голос и начало;
- C — лучшая завершающая каденция;
- A — неплохой, но с неестественной внутренней паузой;
- D — худший.

Production policy:

```text
Блоки 1-3: B
Блок 4:    C
Mode:      reference-only
Ultimate:  только отдельное исследование
```

Подготовка B/C:

```text
highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2
```

Не применять агрессивный `afftdn` по умолчанию.

Более длинный reverberant reference может одновременно усилить узнаваемость и перенести больше помещения. Лучший будущий шаг — чистый close-mic MacArthur reference 15–25 секунд.

Полный playbook:

```text
docs/voxcpm2/REFERENCE_AUDIO_PLAYBOOK.md
```

## 11. KV-cache

Локальная конфигурация 512 была недостаточна.

Ошибка:

```text
The expanded size of the tensor (512) must match the existing size (550)
```

и затем:

```text
KV cache is full
```

Сегментный production-профиль использует явную инициализацию обоих кэшей, например 4096:

```python
cache_dtype = next(model.tts_model.parameters()).dtype
cache_device = model.tts_model.device

model.tts_model.base_lm.setup_cache(
    1, 4096, cache_device, cache_dtype
)
model.tts_model.residual_lm.setup_cache(
    1, 4096, cache_device, cache_dtype
)
```

8192 работало для эксперимента, но для коротких production-блоков 4096 достаточно и экономнее.

## 12. Текущий final profile

```text
Device:          CPU
Clone mode:      reference-only
Steps:           16
CFG:             1.80
Candidates:      2 per block
Third candidate: only when suspicious
Seeds:           fixed and recorded
Intermediate:    PCM 24-bit / 48 kHz
NoChew:          enabled
Short slowdown:  forbidden
```

Этот профиль пока является экспериментом. Нельзя утверждать, что Steps 16 / CFG 1.80 лучше V3.2, пока владелец не прослушал результат.

## 13. Mix policy

Owner rejected speech-triggered sidechain ducking.

Нужно:

```text
Русский голос: 100%
Оригинал:       один постоянный reduced gain
Sidechain:      disabled
```

Критически важно различать:

| Формулировка | Gain | Приблизительно |
|---|---:|---:|
| снизить на 25% | `0.75` | `-2.50 dB` |
| оставить на 25% | `0.25` | `-12.04 dB` |
| снизить на 30% | `0.70` | `-3.10 dB` |

Текущий intended range:

```text
OriginalGain: 0.70-0.78
Default:      0.75
```

Изменение gain не требует нового VoxCPM2 synthesis. Использовать:

```text
tools/voxcpm2/windows/Remaster-MacArthur-Constant-Gain.ps1
```

## 14. Production tools

```text
tools/voxcpm2/production_preflight.py
tools/voxcpm2/windows/Run-MacArthur-Final-CPU.ps1
tools/voxcpm2/windows/Remaster-MacArthur-Constant-Gain.ps1
tools/voxcpm2/examples/macarthur_raasabpj_iw/segments_ru_final.json
tools/voxcpm2/examples/macarthur_raasabpj_iw/subtitles_ru_final.srt
```

Главный runbook:

```text
docs/voxcpm2/PRODUCTION_RUNBOOK.md
```

## 15. Preflight

До загрузки 15+ ГБ модели проверяются:

- Python executable;
- production package scripts;
- Python syntax;
- FFmpeg/ffprobe;
- source video;
- source duration;
- segment bounds;
- B/C references;
- model snapshot;
- free disk;
- `CUDA_VISIBLE_DEVICES=-1`.

Tool:

```text
tools/voxcpm2/production_preflight.py
```

## 16. PowerShell quality gate

Первые final launchers выявили отдельный класс ошибок:

- unmatched expression;
- encoding corruption;
- cascading parser errors;
- relative parser path under `C:\Windows\System32`;
- missing directories/source/references.

Текущая политика:

- ASCII-safe control flow;
- arrays for command arguments instead of fragile backtick chains;
- automatic directory creation;
- automatic source reuse/download;
- automatic B/C generation;
- AST parse every `.ps1` in CI.

Workflow:

```text
.github/workflows/voxcpm2-windows.yml
```

## 17. Publication acceptance gate

Нельзя утверждать «готово к загрузке», пока не проверено:

### Content

- все русские предложения присутствуют;
- нет swallowed word/consonant;
- нет повторов и hallucinated words;
- богословские термины точны;
- SRT соответствует смыслу финальной речи.

### Voice

- MacArthur similarity приемлема;
- B delivery сохранена в 1–3 блоках;
- C cadence работает в финале;
- нет резкого voice drift;
- нет недопустимого английского акцента.

### Audio

- Russian-only прослушан отдельно;
- нет pause-then-chewing tail;
- нет clipping;
- постоянный original gain;
- нет sidechain pumping;
- master JSON существует;
- начало и конец не обрезаны;
- длительность совпадает с source.

### Reproducibility

- preflight JSON;
- synthesis JSON;
- master JSON;
- параметры и seed;
- выбранный candidate каждого блока;
- source URL и duration.

## 18. Следующие оптимизации

В порядке ожидаемой пользы:

1. прослушать текущий Steps 16 / CFG 1.80 render;
2. remaster одного и того же Russian WAV при gain 0.70 / 0.75 / 0.78;
3. добавить ASR completeness score для каждого candidate;
4. проверять endpoint phoneme/consonant, а не только энергию;
5. найти cleaner close-mic reference;
6. провести CFG 1.55 / 1.75 / 1.95 sweep на одной ending-sensitive фразе;
7. сравнить Steps 10 / 16 только после выбора CFG;
8. добавить resumable manifest и selected-segment regeneration;
9. провести одинаковый русский bake-off с Chatterbox Multilingual V3 и Qwen3-TTS;
10. Fish S2-Pro тестировать после исправной GPU или в облаке.

Слепое увеличение steps менее перспективно, чем candidate selection, reference acoustics и content QA.

## 19. Передача другому ИИ

Сначала читать:

1. `docs/voxcpm2/CURRENT_STATE.md`;
2. `docs/voxcpm2/HANDOFF_FOR_AI.md`;
3. `docs/voxcpm2/PRODUCTION_RUNBOOK.md`;
4. `docs/voxcpm2/EXPERIMENT_LOG.md`;
5. `docs/voxcpm2/REFERENCE_AUDIO_PLAYBOOK.md`;
6. `docs/voxcpm2/QUALITY_RESEARCH_2026-07-26.md`;
7. `docs/voxcpm2/MODEL_COMPARISON_2026-07-26.md`;
8. `docs/voxcpm2/SOURCES.md`.

Другой ИИ не должен:

- предлагать CUDA;
- возвращать семь коротких V2-сегментов;
- восстанавливать timing-derived high `min_len`;
- замедлять short candidates;
- использовать Ultimate по умолчанию;
- возвращать sidechain;
- путать `reduce by 25%` с `gain 0.25`;
- утверждать финальный успех без owner listening.
