# Dub Studio Clean Direct Production

## Цель

Telegram-бот должен давать тот же аудиорезультат, что и проверенный ручной запуск из PowerShell. Бот является диспетчером: он готовит входные данные, запускает один production renderer и один master, показывает прогресс и отправляет файлы.

## Стандартный путь

1. Получить исходное видео и наиболее точную доступную расшифровку.
2. Для Gemini MAX выполнить три редакторских прохода перевода и отдельную компрессию только перегруженных реплик.
3. Разбить речь на естественные окна с целью 4,2 секунды и жёстким максимумом 5,4 секунды.
4. Выбрать спокойные, непрерывные и достаточно озвученные фрагменты исходного голоса.
5. Сохранить для каждого voice reference прозрачный `*.selection.json` с интервалами и метриками.
6. Запустить напрямую:
   - `tools/voxcpm2/examples/john_piper_z20py4yqhyq/voxcpm2_cpu_shorts_production.py`
   - `tools/voxcpm2/examples/john_piper_z20py4yqhyq/master_constant_mix.py`
7. После рендера независимо проверить русский текст, акустику, начало, хвост, внутренние паузы и регистр голоса.
8. При первом отказе повторить только плохие ID с одним новым общим seed.
9. После второго отказа остановить выпуск с точной причиной. Не переключать API клонирования, не добавлять prompt-rescue и не ослаблять QA.

## Чего нет в production path

- `runpy`-матрёшек;
- `subprocess` proxy;
- подмены renderer;
- `VOXCPM_ORIGINAL_RENDERER`/`VOXCPM_RESCUE_RENDERER`;
- prompt-transcript rescue;
- автоматического изменения русского текста после перевода;
- продолжения checkpoints v4.2–v4.7 без clean-marker.

Старые модули сохранены только для истории и совместимости тестов. Recipe `generic_short_v1` их не вызывает.

## Голосовой клон

Отбор voice reference выполняется до дорогого CPU-синтеза. Кандидаты оцениваются по:

- доле устойчивой озвученной речи;
- медиане и верхнему диапазону F0;
- внутренним провалам активности;
- общей непрерывности;
- RMS и пикам.

Итоговые файлы:

- `references/extended_reference.wav`
- `references/extended_reference.selection.json`
- `references/composite_reference.wav`
- `references/composite_reference.selection.json`

Если все кандидаты обрывочны, почти без речи или непригодны по активности, задание останавливается до рендера.

## Старые проекты и `/dubfix all`

Полный clean repair:

1. Проверяет hash существующего русского текста.
2. При необходимости один раз мигрирует старые длинные окна.
3. Разделяет окна длиннее 5,4 секунды.
4. Объединяет слишком короткую или однословную реплику с соседом, только если общее окно остаётся в пределах 5,4 секунды.
5. Проверяет, что последовательность всех русских токенов полностью совпадает с исходной.
6. Создаёт `segments_ru_final.pre_clean.json`, если структура изменилась.
7. Удаляет все старые checkpoints и markers.
8. Строит новые voice references и запускает прямой renderer с нуля.

Выборочный `/dubfix ID1,ID2` разрешён только после успешного clean baseline.

## Release master

- Russian target: `-16 LUFS`;
- true peak: `-1.5 dBTP`;
- LRA target: `8`;
- уровень оригинала берётся из проекта, по умолчанию `18%`.

## Диагностика

- `output/clean_production_report.json`
- `output/audio_repair_report.json`
- `audio/<video_id>_ru_timeline.clean_qa.json`
- `segment_work/clean_production.marker.json`
- `references/*.selection.json`

`clean_production_report.json` обязан содержать `wrapper_count: 0`.
