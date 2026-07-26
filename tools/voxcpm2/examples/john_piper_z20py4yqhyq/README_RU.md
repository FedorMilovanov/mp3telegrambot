# John Piper SHORTS — VoxCPM2 CPU FINAL

Это готовый однокомандный производственный пакет для ролика **Four Marks You Belong to Christ | John Piper Clip**.

Здесь John Piper — проповедник и исходный спикер. Синтез выполняет наша локальная **VoxCPM2**, а не TTS-проект Piper.

## Производственная схема

- источник: `https://youtube.com/shorts/Z20Py4yQhYQ`;
- устройство: только CPU;
- голос: zero-shot clone Джона Пайпера из самого ролика;
- перевод: буквальный литературный русский без расширительного пересказа;
- 5 смысловых блоков, выровненных по исходной речи;
- два reference-профиля: extended 24 секунды и composite около 22 секунд;
- LocDiT: 16 шагов, CFG 1.80;
- 2 кандидата на блок, третий только при подозрительном результате;
- NoChew-проверка хвостового повторения;
- короткие удачные кандидаты не замедляются;
- индивидуальные задержки блоков: 220 / 160 / 100 / 70 / 40 мс;
- русский голос 100%, постоянный английский фон 18%;
- master: -14 LUFS, LRA 9, -1 dBTP;
- AAC 320 кбит/с, 48 кГц;
- видеопоток копируется без повторного кодирования.

## Почему CPU

На этой RTX 3060 реальные прогоны VoxCPM2 вызывали повторяемые `nvlddmkm Event ID 153` как в BF16, так и в FP16. Launcher принудительно задаёт `CUDA_VISIBLE_DEVICES=-1` и прекращает работу, если выбранный PyTorch неожиданно видит CUDA.

## Одна команда

Откройте PowerShell и выполните:

```powershell
cd "C:\Users\Fedor\Projects\mp3telegrambot"; git pull origin main; Set-ExecutionPolicy -Scope Process Bypass -Force; .\tools\voxcpm2\examples\john_piper_z20py4yqhyq\Run-John-Piper-FINAL-CPU.ps1
```

Launcher сам:

1. проверит существующее CPU-окружение VoxCPM2;
2. скачает исходный Shorts;
3. извлечёт и очистит голосовые референсы Джона Пайпера;
4. синтезирует пять русских блоков;
5. выберет лучшие кандидаты и подгонит только слишком длинные;
6. соберёт постоянный оригинальный фон и русский master;
7. удалит тяжёлые промежуточные WAV;
8. откроет папку с готовым результатом.

## Итоговый файл

Загружать на канал:

```text
C:\AI-Archive\John-Piper-Short-Z20Py4yQhYQ-FINAL\output\John_Piper_Russian_Dub_FINAL_UPLOAD.mp4
```

Рядом будут:

```text
John_Piper_Russian_Dub_FINAL_RUSSIAN_ONLY.mp4
John_Piper_Russian_Dub_FINAL.srt
John_Piper_Russian_Translation.txt
John_Piper_Source_English.srt
John_Piper_FINAL.manifest.json
```

Для сохранения всех кандидатов и диагностических WAV:

```powershell
.\tools\voxcpm2\examples\john_piper_z20py4yqhyq\Run-John-Piper-FINAL-CPU.ps1 -KeepDiagnostics
```

Для другого постоянного уровня английского оригинала:

```powershell
.\tools\voxcpm2\examples\john_piper_z20py4yqhyq\Run-John-Piper-FINAL-CPU.ps1 -OriginalLevel 0.15
```

## Контрольные материалы

- `source_subtitles_en.srt` — присланные автоматические английские субтитры;
- `translation_ru.txt` — полный буквальный литературный перевод;
- `segments_ru_final.json` — производственный текст, тайминг и задержки;
- `subtitles_ru_final.srt` — русские субтитры;
- `youtube_metadata_ru.md` — заголовок, описание и закреплённый комментарий.
