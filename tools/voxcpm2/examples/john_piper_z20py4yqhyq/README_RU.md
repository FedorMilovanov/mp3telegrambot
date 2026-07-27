# John Piper SHORTS — VoxCPM2 CPU FINAL

Готовый прямой производственный запуск для ролика **Four Marks You Belong to Christ | John Piper Clip**:

- источник: `https://youtube.com/shorts/Z20Py4yQhYQ`;
- движок: **VoxCPM2**;
- устройство: **только CPU**;
- голос: zero-shot clone Джона Пайпера из самого ролика;
- перевод: литературный русский;
- монтаж: 5 смысловых блоков с индивидуальными задержками;
- английский оригинал: постоянно 25%, без sidechain и без динамического приглушения;
- русский голос: 100%;
- master: `-14 LUFS`, `-1 dBTP`;
- видео копируется без повторного кодирования.

## Почему только CPU

RTX 3060 в реальной VoxCPM2-генерации повторяемо создаёт `nvlddmkm Event ID 153` и с BF16, и с FP16. Runner принудительно задаёт `CUDA_VISIBLE_DEVICES=-1` и перед рендером проверяет, что PyTorch не видит CUDA.

## Голосовой профиль

Используется та же удачная схема, что и для John MacArthur:

1. исходный Shorts скачивается один раз;
2. создаётся extended-референс длительностью 24 секунды;
3. создаётся composite-референс из двух участков речи общей длительностью около 22 секунд;
4. VoxCPM2 переносит тембр и манеру речи на русский текст без fine-tune весов;
5. NoChew-детектор отсеивает или обрезает речеподобные хвосты после пауз;
6. короткие реплики не растягиваются ради заполнения таймкода.

## Профиль качества

```text
CPU only
Segments: 5
LocDiT steps: 16
CFG: 1.80
Candidates: 2 на сегмент; третий только при подозрительном результате
NoChew tail-restart detector
Intermediate audio: 24-bit / 48 kHz
Original English: constant 25%
Russian voice: 100%
Final loudness: -14 LUFS
True peak: -1 dBTP
AAC: 320 kbps / 48 kHz
```

## Одна команда

Обычный PowerShell; запуск от администратора не требуется:

```powershell
cd "C:\Users\Fedor\Projects\mp3telegrambot"; git pull origin main; Set-ExecutionPolicy -Scope Process Bypass -Force; .\tools\voxcpm2\examples\john_piper_z20py4yqhyq\Run-John-Piper-FINAL-CPU.ps1
```

Runner до синтеза сам проверяет:

- наличие CPU Python и VoxCPM2;
- отсутствие доступной CUDA;
- FFmpeg и FFprobe;
- синтаксис обоих Python-модулей;
- корректность JSON сегментов;
- наличие перевода и субтитров.

## Итог

Главный файл для загрузки:

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

Исходное видео сохраняется и при повторном запуске не скачивается заново. Синтез голоса запускается заново, чтобы старые промежуточные WAV не были ошибочно приняты за новый результат.

Сохранить все кандидаты и диагностические WAV:

```powershell
.\tools\voxcpm2\examples\john_piper_z20py4yqhyq\Run-John-Piper-FINAL-CPU.ps1 -KeepDiagnostics
```

Изменить постоянную громкость английского оригинала, например на 22%:

```powershell
.\tools\voxcpm2\examples\john_piper_z20py4yqhyq\Run-John-Piper-FINAL-CPU.ps1 -OriginalLevel 0.22
```

## Проверка production-runner

Windows validation passed: PowerShell parser, Python compile, five-segment JSON, and legacy-layer absence.

