# Сравнение VoxCPM2 с конкурентами — 26 июля 2026

Этот документ отвечает на практический вопрос проекта: действительно ли VoxCPM2 является лучшей или хотя бы одной из наиболее перспективных открытых моделей для русской озвучки английских проповедей с сохранением голоса говорящего.

Проверено более 70 страниц: официальные статьи и репозитории, модельные карточки, независимые таблицы, слепые/человеческие сравнения, GitHub issues, Hugging Face discussions, Hacker News и Reddit. Заявления разработчиков отделены от независимых измерений и пользовательских жалоб.

## Краткий вывод

VoxCPM2 — не безусловный лидер по чистоте, естественности или сходству голоса. Она является одной из самых интересных **универсальных** открытых моделей благодаря сочетанию:

- 30 языков, включая русский;
- cross-lingual zero-shot cloning;
- reference-only, continuation и combined/Ultimate режимов;
- voice design;
- 48 kHz выхода;
- Apache-2.0;
- открытых SFT/LoRA-инструментов;
- возможности запуска на CPU;
- относительно умеренного размера 2B.

Однако независимый `tts-bench` показывает заметно более слабое сходство и предсказанную естественность, чем у IndexTTS2, F5-TTS, Fish S2-Pro, LongCat-AudioDiT, Echo-TTS и DramaBox. При этом VoxCPM2 там имеет очень низкий WER, то есть содержание произносит разборчиво, но голос может быть менее похожим и менее естественным.

Для текущего компьютера и русской озвучки разумно:

1. закончить стабилизацию VoxCPM2 как базовой системы;
2. затем честно сравнить её с **Chatterbox Multilingual V3** и **Qwen3-TTS 1.7B**;
3. Fish Audio S2-Pro рассматривать после замены GPU или через облачный запуск;
4. IndexTTS2 не использовать для русского без отдельной русской адаптации;
5. не переходить на модель только по одной официальной таблице.

## Почему рейтинги противоречат друг другу

### Официальные benchmark-таблицы

Разработчик обычно выбирает:

- собственный inference recipe;
- подходящий prompt/reference mode;
- seed и параметры;
- нормализацию текста;
- ASR и speaker encoder;
- набор языков;
- способ отбраковки неудачных генераций.

Поэтому официальные результаты полезны, но могут быть оптимистичнее обычного локального запуска.

### Независимый `tts-bench`

Плюсы:

- одинаковые пять фраз;
- один и тот же cloning-reference;
- WER, UTMOS, speaker similarity;
- отдельные speed/RAM/VRAM измерения.

Минусы:

- всего пять фраз;
- английский, а не русский cross-lingual dubbing;
- один референс;
- результат чувствителен к выбранному режиму и параметрам модели;
- UTMOS и speaker embedding не заменяют человеческое прослушивание.

### Voice Arena / Artificial Analysis

Это ценные human-preference таблицы, но в основном они сравнивают облачные API и собственные голоса провайдеров, а не локальное zero-shot cloning одного и того же человека. Их нельзя напрямую переносить на задачу «английский МакАртур → русский МакАртур».

### Issues и форумы

Это не статистически репрезентативный benchmark. Но именно там обнаруживаются реальные дефекты, которые официальные таблицы скрывают:

- обрыв последнего слога;
- повтор после паузы;
- drift голоса;
- неверный язык/акцент;
- нестабильная скорость;
- проблемы длительности;
- требования к памяти;
- несовпадение опубликованного benchmark с воспроизводимым результатом.

## Сравнительная матрица для нашего сценария

| Модель | Русский из коробки | Cross-lingual cloning | Независимое сходство | Long-form | CPU-практичность | Главный риск |
|---|---:|---:|---:|---:|---:|---|
| VoxCPM2 2B | да | да | среднее в `tts-bench` | требует сегментации | работает, но медленно | EOS, chewing, drift, room transfer |
| Fish S2-Pro 4B+0.4B | да | да | высокое | сильная архитектура | практически нет на нашем CPU | высокий ресурс, сложный inference stack |
| Qwen3-TTS 1.7B | да | да | выше VoxCPM2 в `tts-bench` | официальный сильный long-form | медленнее VoxCPM2 CPU в независимом тесте | ускорение речи на длинном тексте |
| Chatterbox Multilingual V3 | да | да | V3 ещё мало независимо проверена | средне | перспективно для CPU | accent leakage, repetition/noise |
| CosyVoice3 0.5B | да | да | хорошее сходство, плохой WER в одном независимом тесте | streaming | вероятно легче | hallucination/reproducibility |
| IndexTTS2 1.5B | фактически нет полноценного русского | ограниченно | очень высокое | точный duration control | очень хорошая CPU-скорость | русская фонетика ломается |
| F5-TTS 0.33B | не основная нативная цель | через prompt | высокое | chunking | крайне медленно на CPU | лицензия весов, WER, язык |
| LongCat-AudioDiT 1B | русский не подтверждён официально | сильное zh/en cloning | очень высокое | non-AR | GPU-ориентировано | неизвестная русская пригодность |
| MOSS-TTS 8B | multilingual | да | высокое | сильный long-form | слишком тяжело | огромные ресурсы |
| VibeVoice | не для русского | ограниченно | среднее | до 90 минут | медленно | English/Mandarin focus |
| Echo-TTS / DramaBox | English focus | да | очень высокое | творческий speech | GPU-heavy | нет русского, лицензия/установка |

## VoxCPM2: где она действительно сильна

### Официальные результаты

На Seed-TTS-Eval VoxCPM2 показывает хороший баланс English WER 1.84 и SIM 75.3. Это не лучший WER и не лучший SIM, но результат конкурентный среди открытых моделей.

В CV3 multilingual test для русского указано WER 5.21. В той же таблице Fish Audio S2 имеет 2.78, CosyVoice3 — 6.64. То есть VoxCPM2 лучше CosyVoice3 по разборчивости русского, но существенно уступает Fish S2.

Во внутреннем MiniMax multilingual benchmark VoxCPM2 заявляет Russian SIM 81.1 против Fish S2 79.0 и Qwen3-TTS 79.2. Это важный сигнал, но он self-reported и не является независимым слепым тестом.

### Независимый `tts-bench`

В cloning track:

```text
VoxCPM2: SIM 0.533, UTMOS 3.596, WER 0.040
```

Сильная сторона — очень низкий WER. Слабые — speaker similarity и naturalness.

Для сравнения:

```text
Fish S2-Pro:       SIM 0.725, UTMOS 4.270, WER 0.058
IndexTTS2:         SIM 0.810, UTMOS 3.705, WER 0.087
F5-TTS:            SIM 0.769, UTMOS 4.027, WER 0.195
LongCat 1B:        SIM 0.870, UTMOS 3.881, WER 0.146
Chatterbox:        SIM 0.627, UTMOS 4.278, WER 0.073
Qwen3-TTS 1.7B:    SIM 0.629, UTMOS 3.938, WER 0.077
Echo-TTS:          SIM 0.834, UTMOS 4.217, WER 0.074
DramaBox:          SIM 0.778, UTMOS 3.889, WER 0.080
```

Вывод: VoxCPM2 может очень хорошо произносить заданный текст, но по этому независимому маленькому тесту хуже удерживает конкретный голос.

### CPU

На независимом Windows-бенчмарке с Ryzen 9950X3D cloning VoxCPM2 работает примерно со скоростью 0.39× realtime и использует около 7.53 GB RAM. Qwen3-TTS Base там медленнее — около 0.19× и 10.4 GB RAM. Chatterbox Turbo — около 0.71× и 4.39 GB RAM. IndexTTS2 — около 1.47× и 5.54 GB RAM.

Наш локальный RTF значительно хуже независимого теста. Возможные причины:

- другой CPU;
- bfloat16 path;
- LocDiT steps;
- reference length;
- installed package 2.0.3;
- model-generation mode;
- thread scheduling;
- разные тексты и длины.

Сравнивать RTF надо только на одном компьютере и одной фразе.

### Реальные дефекты

Upstream reports подтверждают:

- обрыв EOS и последних согласных;
- click/chirp от хвоста reference-conditioning;
- drift в long-form;
- pacing anomalies;
- KV-cache проблемы;
- необходимость подбора CFG/seed/prompt;
- высокое качество LoRA после ручной настройки, но не автоматическую стабильность.

Именно поэтому наш NoChew/ASR/segment-candidate pipeline является не косметикой, а необходимым production layer.

## Fish Audio S2-Pro

### Сильные стороны

- очень низкий WER в официальном Seed-TTS-Eval;
- сильный CV3 Russian WER;
- 10–30 секунд reference;
- 50+ языков в release notes, более широкие заявления в repo;
- multi-speaker/multi-turn;
- inline prosody tags;
- высокие независимые UTMOS и speaker similarity;
- Fish S2 Pro занимает заметное место в актуальных hosted voice arenas.

### Ограничения

- 4B slow AR + 400M fast AR;
- официальный быстрый runtime показан на H200;
- CPU-путь для S2-Pro не выглядит практичным;
- пользователи просят quantization/optimization;
- cross-language language-detection может смешиваться с языком reference;
- inference code и streaming engineering получают критические отзывы;
- default/no-reference voice consistency не детерминирована.

### Для нас

Fish S2-Pro — главный кандидат на качество после замены GPU или через ограниченный облачный A/B. На текущем CPU он не должен заменять VoxCPM2 первым.

## Qwen3-TTS

### Сильные стороны

- русский входит в десять официальных языков;
- 3-секундное клонирование;
- Apache-2.0;
- сильные Russian WER/SIM в официальной multilingual table;
- сильный cross-lingual benchmark;
- официальный long-form WER заметно лучше VoxCPM baseline и VibeVoice на приведённом тесте;
- streaming architecture.

### Ограничения

- независимый CPU speed ниже VoxCPM2 на `tts-bench`;
- RAM выше;
- issue reports показывают постепенное ускорение речи на длинном тексте;
- official benchmark по Russian может зависеть от конкретной 12/25 Hz версии и inference recipe.

### Для нас

Это наиболее важный **прямой конкурент VoxCPM2 для русского**. После завершения VoxCPM2 V3.2 следует сделать 5–7 коротких одинаковых Russian cloning samples на Qwen3-TTS 1.7B.

## Chatterbox Multilingual V3

### Сильные стороны

- русский поддерживается;
- сравнительно небольшая модель;
- CPU должен быть легче VoxCPM2;
- V3 заявляет уменьшение hallucination и улучшение similarity;
- старые Chatterbox/Turbo имеют сильную naturalness в независимом `tts-bench`;
- Podonos human evaluations для Turbo показывают конкурентность с proprietary realtime TTS.

### Ограничения

- Podonos tests были организованы поставщиком модели и в основном на английском;
- Turbo и Multilingual V3 — разные модели;
- issues содержат repetition, noise, speedup, state corruption и language-mode ограничения;
- cross-language reference может переносить исходный accent; сами разработчики советуют снижать CFG.

### Для нас

Это лучший кандидат на **лёгкий CPU-side альтернативный тест**. Но нельзя переносить English Turbo benchmark на русский Multilingual V3 без локального A/B.

## CosyVoice3

### Плюсы

- русский поддерживается;
- 0.5B;
- streaming и низкая latency на GPU;
- сильное официальное similarity;
- instruct control;
- собственный CV3-Eval с in-the-wild reference.

### Минусы

- CV3 Russian WER хуже VoxCPM2 и Fish S2;
- независимый `tts-bench` получил хороший SIM, но WER 0.501;
- пользователь официального repo не смог воспроизвести Seed score и наблюдал hallucinations с WER около 25%.

### Для нас

Интересна как GPU/streaming модель, но для богословского текста content fidelity важнее привлекательной интонации. Не первая замена.

## IndexTTS2

### Плюсы

- очень сильное speaker similarity;
- точный duration control — идеально по концепции для дубляжа;
- emotion/timbre disentanglement;
- высокая CPU speed в независимом тесте.

### Критический минус

Официальная production focus — китайский и английский. Issues показывают, что русский может читаться с китайской фонетикой, а пользователи отдельно просят полноценную русскую поддержку/tokenizer.

### Для нас

Пока непригодна без Russian adaptation, несмотря на почти идеальный набор функций для lip-sync dubbing.

## F5-TTS

### Плюсы

- маленькая модель;
- высокий independent SIM;
- простой zero-shot flow-matching подход;
- развитые finetuning-инструменты.

### Минусы

- CPU в независимом тесте очень медленный;
- pretrained weights CC-BY-NC;
- Russian не является основной гарантированной целью base checkpoint;
- independent WER хуже большинства ведущих cloning-моделей.

### Для нас

Не выигрывает у VoxCPM2 по сумме ограничений.

## LongCat-AudioDiT

LongCat 1B показывает лучший independent SIM в рассматриваемой таблице и сильные официальные Seed metrics. Non-autoregressive waveform-latent design потенциально снижает autoregressive chewing/drift.

Но в официальных материалах, которые были найдены, русский production support не подтверждён. Прежде чем устанавливать модель, надо проверить tokenizer/language coverage на коротком облачном demo или отдельном checkpoint.

## MOSS-TTS, VibeVoice, Echo-TTS, DramaBox

- MOSS-TTS: хорош для long-form, но 8B и 22+ GB VRAM в независимом тесте.
- VibeVoice: выдающийся long-form/multi-speaker, но English/Mandarin focus и слабая применимость к русскому клонированию.
- Echo-TTS: очень высокий independent SIM, но GPU-heavy и не доказан для русского.
- DramaBox: впечатляющая expressiveness и similarity, но English focus, 17+ GB VRAM, LTX community license и сложная установка.

Они показывают, что VoxCPM2 не находится на абсолютной вершине cloning fidelity, но не являются лучшей заменой для текущей задачи.

## Итоговый рейтинг именно для проекта

### Сейчас, на текущем CPU

1. **VoxCPM2** — уже установлена, русский поддерживается, качество можно улучшать pipeline-ом.
2. **Chatterbox Multilingual V3** — следующий CPU A/B из-за меньшей модели и русского.
3. **Qwen3-TTS 1.7B** — наиболее серьёзный русский конкурент, но CPU тяжелее.
4. **IndexTTS2** — только после подтверждённой Russian adaptation.
5. **F5-TTS** — невыгоден по CPU/лицензии/языку.

### После исправной GPU 16–24 GB или облака

1. **Fish Audio S2-Pro** — главный кандидат на raw quality и intelligibility.
2. **Qwen3-TTS 1.7B** — русский, cross-lingual, long-form.
3. **VoxCPM2** — универсальность и control/fine-tune.
4. **Chatterbox Multilingual V3** — production simplicity и naturalness.
5. **LongCat-AudioDiT 1B** — только если подтвердится русский.

## Как провести честный bake-off

Нужен один и тот же пакет:

### References

- B extended 24 seconds;
- более чистый close-mic МакАртур 15–25 seconds;
- никаких разных референсов между моделями без отдельной отметки.

### Семь фраз

1. спокойное утверждение;
2. перечисление;
3. сложное богословское предложение;
4. вопрос;
5. короткий ответ;
6. фраза с трудными русскими согласными в конце;
7. 20–30 секунд long-form.

### Метрики

- Russian ASR WER;
- speaker embedding similarity к reference;
- UTMOS/DNSMOS как вспомогательная метрика;
- initial/final silence;
- pause-then-restart tail;
- clipping;
- RTF;
- peak RAM/VRAM;
- ручные оценки 1–5: сходство, естественность, дикция, интонация, echo, окончания.

### Правила

- минимум три кандидата на фразу;
- одинаковый text normalization;
- никаких скрытых перегенераций;
- все параметры записываются;
- победитель выбирается не по одной средней цифре, а по bottleneck задачи: русская разборчивость + сходство + отсутствие артефактов.

## Источники: официальные и первичные

### VoxCPM2

1. https://github.com/OpenBMB/VoxCPM
2. https://voxcpm.readthedocs.io/en/latest/quickstart.html
3. https://voxcpm.readthedocs.io/en/latest/usage_guide.html
4. https://voxcpm.readthedocs.io/en/latest/models/voxcpm2.html
5. https://arxiv.org/abs/2606.06928
6. https://huggingface.co/openbmb/VoxCPM2
7. https://huggingface.co/openbmb/VoxCPM2/discussions
8. https://huggingface.co/openbmb/VoxCPM2/discussions/3
9. https://github.com/OpenBMB/VoxCPM/issues/192
10. https://github.com/OpenBMB/VoxCPM/issues/202
11. https://github.com/OpenBMB/VoxCPM/issues/213
12. https://github.com/OpenBMB/VoxCPM/issues/238
13. https://github.com/OpenBMB/VoxCPM/issues/271
14. https://github.com/OpenBMB/VoxCPM/issues/272

### Независимые и human-preference benchmarks

15. https://5uck1ess.github.io/tts-bench/scores.html
16. https://5uck1ess.github.io/tts-bench/speed.html
17. https://5uck1ess.github.io/tts-bench/listen.html
18. https://5uck1ess-tts-arena.hf.space/
19. https://voicearena.com/
20. https://artificialanalysis.ai/text-to-speech/leaderboard/provider-voice?tab=leaderboard
21. https://arxiv.org/abs/2504.20581

### Fish Audio S2

22. https://github.com/fishaudio/fish-speech
23. https://github.com/fishaudio/fish-speech/blob/main/docs/en/index.md
24. https://github.com/fishaudio/fish-speech/releases
25. https://arxiv.org/abs/2603.08823
26. https://github.com/fishaudio/fish-speech/issues/1168
27. https://github.com/fishaudio/fish-speech/issues/1260
28. https://github.com/fishaudio/fish-speech/issues/1263
29. https://news.ycombinator.com/item?id=45117949

### Qwen3-TTS

30. https://github.com/QwenLM/Qwen3-TTS
31. https://arxiv.org/abs/2601.15621
32. https://github.com/QwenLM/Qwen3-TTS/issues/239
33. https://github.com/QwenLM/Qwen3-TTS/issues

### Chatterbox

34. https://github.com/resemble-ai/chatterbox
35. https://pypi.org/project/chatterbox-tts/
36. https://www.podonos.com/blog/chatterbox-turbo
37. https://www.podonos.com/resembleai/chatterbox-turbo-vs-elevenlabs-turbo?t=a
38. https://www.podonos.com/resembleai/chatterbox-turbo-vs-vibevoice7b?t=a
39. https://github.com/resemble-ai/chatterbox/issues/327
40. https://github.com/resemble-ai/chatterbox/issues/346
41. https://github.com/resemble-ai/chatterbox/issues

### CosyVoice

42. https://github.com/FunAudioLLM/CosyVoice
43. https://github.com/FunAudioLLM/CV3-Eval
44. https://github.com/FunAudioLLM/CosyVoice/issues/1801

### IndexTTS2

45. https://github.com/index-tts/index-tts
46. https://arxiv.org/abs/2506.21619
47. https://ojs.aaai.org/index.php/AAAI/article/view/40820
48. https://github.com/index-tts/index-tts/issues/394
49. https://github.com/index-tts/index-tts/issues/410
50. https://github.com/index-tts/index-tts/issues/418
51. https://www.reddit.com/r/LocalLLaMA/comments/1lyy39n/indextts2_the_most_realistic_and_expressive/

### F5-TTS

52. https://github.com/SWivid/F5-TTS
53. https://arxiv.org/abs/2410.06885

### LongCat, MOSS, VibeVoice и новые кандидаты

54. https://github.com/meituan-longcat/LongCat-AudioDiT
55. https://arxiv.org/abs/2603.29339
56. https://github.com/OpenMOSS/MOSS-TTS
57. https://arxiv.org/abs/2603.18090
58. https://arxiv.org/abs/2508.19205
59. https://arxiv.org/abs/2602.04160
60. https://arxiv.org/abs/2605.05611

### DramaBox / community evidence

61. https://huggingface.co/ResembleAI/Dramabox
62. https://huggingface.co/ResembleAI/Dramabox/blob/main/README.md
63. https://www.reddit.com/r/LocalLLaMA/comments/1tc5wx1/dramabox_most_expressive_voice_model_ever_based/
64. https://www.reddit.com/r/comfyui/comments/1te0r2k/dramabox_expressive_tts_with_voice_cloning/

### Дополнительные независимые обзоры

65. https://github.com/mirfahimanwar/TTS-Model-Comparison-Chart/
66. https://andrew.ooo/posts/voxcpm2-tokenizer-free-tts-review/
67. https://www.alphaxiv.org/abs/2606.06928

## Финальный практический вывод

Выбор VoxCPM2 был не ошибкой. Она действительно входит в наиболее перспективную группу открытых multilingual cloning-моделей и особенно удобна как исследовательская платформа. Но ожидать, что одна настройка превратит её в безусловно лучший voice clone, нельзя.

Наш путь должен быть двухконтурным:

```text
VoxCPM2: довести pipeline и использовать как текущую baseline
+
Chatterbox V3 / Qwen3-TTS: провести одинаковый русский cross-lingual bake-off
+
Fish S2-Pro: проверить после появления подходящего GPU или облачного бюджета
```

Переходить на другую модель следует только если она на наших семи русских фразах одновременно выигрывает по сходству, окончаниям, отсутствию эха, WER и ресурсоёмкости.
