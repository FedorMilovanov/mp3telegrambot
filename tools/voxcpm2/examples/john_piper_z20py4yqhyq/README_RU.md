# John Piper SHORTS — VoxCPM2 CPU FINAL

Прямой производственный запуск для ролика **Four Marks You Belong to Christ | John Piper Clip**.

Здесь **John Piper — исходный спикер**, а **VoxCPM2 — наша модель синтеза**. Сторонний Piper TTS не используется.

## Production-профиль

- источник: `https://youtube.com/shorts/Z20Py4yQhYQ`;
- фактическая длительность и конец таймлайна: `62.514` секунды;
- устройство: только CPU;
- голос: zero-shot clone Джона Пайпера из самого ролика;
- перевод: буквальный литературный русский;
- монтаж: 5 смысловых блоков с индивидуальными задержками;
- LocDiT: 16 шагов;
- CFG: 1.80;
- кандидаты: два на блок, третий только при подозрительном результате;
- NoChew: контроль речеподобного повторения в хвосте;
- английский оригинал: постоянно 25%, без sidechain и ducking;
- русский голос: 100%;
- master: `-14 LUFS`, `-1 dBTP`;
- AAC: 320 кбит/с, 48 кГц;
- видеопоток копируется без повторного кодирования.

## Чистая архитектура

Production-путь не содержит ZIP, Base64 и вложенных PowerShell-launcher-ов:

```text
Run-John-Piper-FINAL-CPU.ps1
    ├── voxcpm2_cpu_shorts_production.py
    ├── master_constant_mix.py
    ├── segments_ru_final.json
    ├── subtitles_ru_final.srt
    └── translation_ru.txt
```

Постоянный Windows CI запрещает появление `package.part*.b64`, встроенных ZIP и `*-Inner.ps1`, рекурсивно компилирует Python, разбирает каждый PowerShell-файл и запускает regression-тесты.

## Почему только CPU

RTX 3060 в реальной VoxCPM2-генерации повторяемо создавала `nvlddmkm Event ID 153` и в BF16, и в FP16. Runner до импорта модели задаёт:

```text
CUDA_VISIBLE_DEVICES=-1
```

и прекращает работу, если PyTorch неожиданно видит CUDA.

## Голосовой профиль

Используется принятая схема John MacArthur:

1. исходный Shorts скачивается один раз;
2. создаётся extended-reference длительностью 24 секунды;
3. создаётся composite-reference из двух участков речи общей длительностью около 22 секунд;
4. VoxCPM2 переносит тембр и манеру речи на русский текст без fine-tune весов;
5. NoChew отсеивает или аккуратно обрезает речеподобные хвосты после пауз;
6. короткие удачные реплики не замедляются ради заполнения таймкода.

## Возобновление после остановки

После каждого завершённого смыслового блока сохраняются:

```text
segment_work\checkpoints\segment_XX.json
segment_work\segments_fitted\XX_*_fitted.wav
```

Checkpoint привязан к тексту блока, его таймингу, задержке, reference-профилю, Steps, CFG и seed. При повторном запуске совпадающие завершённые блоки используются повторно. Если работа остановилась на четвёртом блоке, следующий запуск продолжит с четвёртого, а первые три не будут заново вычисляться.

Сырые кандидаты удаляются после успешной сборки, но fitted WAV и checkpoints сохраняются. Изменение текста конкретного блока автоматически делает checkpoint этого блока недействительным; остальные блоки остаются пригодными.

## Одна команда

Обычный PowerShell; права администратора не требуются:

```powershell
cd "C:\Users\Fedor\Projects\mp3telegrambot"; git pull origin main; Set-ExecutionPolicy -Scope Process Bypass -Force; .\tools\voxcpm2\examples\john_piper_z20py4yqhyq\Run-John-Piper-FINAL-CPU.ps1
```

До загрузки модели runner проверяет:

- CPU Python и VoxCPM2;
- отсутствие доступной CUDA;
- FFmpeg и FFprobe;
- компиляцию обоих Python-модулей;
- ровно пять сегментов;
- соответствие конца таймлайна фактической длительности видео;
- перевод и оба файла субтитров.

## Итоговый файл

Загружать на канал:

```text
C:\AI-Archive\John-Piper-Short-Z20Py4yQhYQ-FINAL\output\John_Piper_Russian_Dub_FINAL_UPLOAD.mp4
```

Рядом создаются:

```text
John_Piper_Russian_Dub_FINAL_RUSSIAN_ONLY.mp4
John_Piper_Russian_Dub_FINAL.srt
John_Piper_Russian_Translation.txt
John_Piper_Source_English.srt
John_Piper_FINAL.manifest.json
John_Piper_Russian_Dub_FINAL_UPLOAD.master.json
```

Сохранить также сырые кандидаты и диагностические WAV:

```powershell
.\tools\voxcpm2\examples\john_piper_z20py4yqhyq\Run-John-Piper-FINAL-CPU.ps1 -KeepDiagnostics
```

Изменить постоянную громкость английского оригинала, например на 22%:

```powershell
.\tools\voxcpm2\examples\john_piper_z20py4yqhyq\Run-John-Piper-FINAL-CPU.ps1 -OriginalLevel 0.22
```

## Проверка

Финальная Windows-валидация прошла успешно:

- embedded ZIP/Base64/inner launcher отсутствуют;
- все Python-файлы компилируются;
- все PowerShell-launcher-ы разбираются без ошибок;
- lightweight VoxCPM2 regression-тесты проходят;
- Ruff fatal checks проходят;
- Python 3.11 и 3.13 safety checks проходят;
- Windows runtime safety checks проходят;
- конец source, segments и SRT согласован на `62.514` секунды;
- checkpoints и их сохранение после очистки покрыты regression-тестом.
