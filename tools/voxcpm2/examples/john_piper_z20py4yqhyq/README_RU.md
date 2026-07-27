# John Piper SHORTS — VoxCPM2 CPU FINAL

Готовый однокомандный производственный пакет для ролика:

- **Four Marks You Belong to Christ | John Piper Clip**
- источник: `https://youtube.com/shorts/Z20Py4yQhYQ`
- движок: **VoxCPM2**
- устройство: **только CPU**
- голос: zero-shot clone Джона Пайпера из самого ролика
- перевод: буквальный литературный русский
- монтаж: 5 смысловых блоков с индивидуальными задержками
- оригинальный английский звук: постоянные 18%
- русский голос: 100%
- master: `-14 LUFS`, `-1 dBTP`
- видео: без повторного кодирования

## Почему только CPU

RTX 3060 в реальной VoxCPM2-нагрузке дала повторяемые `nvlddmkm Event ID 153`
и в BF16, и в FP16. Этот launcher принудительно устанавливает
`CUDA_VISIBLE_DEVICES=-1` и проверяет, что выбранный PyTorch не видит CUDA.

## Что означает «клонирование»

Это та же схема, что использовалась для John MacArthur:

1. исходный Shorts скачивается;
2. из голоса спикера создаются два чистых reference-профиля:
   - `B_extended_24s.wav`;
   - `C_composite_21s.wav`;
3. VoxCPM2 переносит голос, тембр и манеру речи на русский текст;
4. отдельный fine-tune или обучение весов не выполняется.

## Профиль качества

```text
CPU only
Reference mode: reference-only
Segments: 5
LocDiT steps: 16
CFG: 1.80
Candidates: 2 на сегмент; третий только при подозрительном результате
NoChew tail-restart detector
Короткие кандидаты никогда не замедляются
Смысловые задержки: 220 / 160 / 100 / 70 / 40 мс
Intermediate audio: 24-bit / 48 kHz
Original English: constant 18%
Russian voice: 100%
Final loudness: -14 LUFS
True peak: -1 dBTP
AAC: 320 kbps / 48 kHz
```

## Одна команда

PowerShell **от имени администратора**:

```powershell
cd "C:\Users\Fedor\Projects\mp3telegrambot"; git pull origin main; Set-ExecutionPolicy -Scope Process Bypass -Force; .\tools\voxcpm2\john_piper_shorts\Run-John-Piper-FINAL-CPU.ps1
```

## Итог

Главный файл для загрузки:

```text
C:\AI-Archive\John-Piper-Short-Z20Py4yQhYQ-FINAL\output\
John_Piper_Russian_Dub_FINAL_UPLOAD.mp4
```

Рядом создаются:

```text
John_Piper_Russian_Dub_FINAL_RUSSIAN_ONLY.mp4
John_Piper_Russian_Dub_FINAL.srt
John_Piper_Russian_Translation.txt
John_Piper_Source_English.srt
John_Piper_FINAL.manifest.json
```

## Повторный запуск

Исходное видео сохраняется и повторно не скачивается. Финальный синтез выполняется
заново, чтобы не принять старые промежуточные WAV за новый результат.

Для сохранения всех кандидатов и временных WAV:

```powershell
.\tools\voxcpm2\john_piper_shorts\Run-John-Piper-FINAL-CPU.ps1 -KeepDiagnostics
```

Для изменения постоянной громкости оригинала:

```powershell
.\tools\voxcpm2\john_piper_shorts\Run-John-Piper-FINAL-CPU.ps1 -OriginalLevel 0.15
```
