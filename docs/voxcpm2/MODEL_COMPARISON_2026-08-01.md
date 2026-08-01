# VoxCPM2 и альтернативы для русской озвучки — актуальный sweep на 2026-08-01

## Короткий вывод

**VoxCPM2 не является безусловным лидером по всем метрикам, но остаётся разумной текущей baseline для нашего проекта.** Для задачи «англоязычная проповедь → русский дубляж с сохранением голоса одного спикера, точным текстом и длинным монологом» важнее не максимальный общий рейтинг, а одновременное выполнение пяти условий:

1. русский язык и cross-lingual cloning действительно работают, а не только заявлены в демо;
2. сохраняется один голос на протяжении всего монолога;
3. не появляются пропуски слов, повторные слоги, неожиданные хвосты и резкие скачки эмоции;
4. candidate можно безопасно вписать в таймлайн без замедления плохой тишины и артефактов;
5. лицензия и runtime подходят для реального проекта.

По совокупности доказательств на 1 августа 2026 года:

- **VoxCPM2** — лучшая уже установленная baseline для нашего CPU-контура: русский, 30 языков, cross-lingual reference-only cloning, Apache-2.0, 48 kHz, открытая fine-tuning-инфраструктура. Но независимый `tts-bench` заметно снижает её оценку speaker similarity и naturalness; upstream issues подтверждают long-form drift, EOS/хвосты и reference leakage.
- **Qwen3-TTS 1.7B Base** — главный прямой конкурент для обязательного A/B на русском. Официально заявлены русский, 3-секундный voice clone, streaming и Apache-2.0. По опубликованным Seed-TTS таблицам и независимому `tts-bench` он выглядит сильнее VoxCPM2 по общему балансу, но это ещё не доказательство лучшего русского монолога на нашем железе.
- **Chatterbox Multilingual V3** — главный лёгкий альтернативный тест: 500M, русский, MIT, reference cloning, watermark. У него есть отдельный documented risk accent leakage при несовпадении языка reference и target; для нашей английской reference → русский это нужно проверять отдельно.
- **Fish Audio S2-Pro** — наиболее сильный кандидат по raw quality, WER, naturalness и inline prosody control, но модель 4B и её Research License не дают считать её готовой коммерческой заменой без отдельного GPU и юридической проверки. S2.1 Pro — уже отдельный hosted/current-generation контур, не тот же самый open-weight S2-Pro.
- **Higgs Audio v3 TTS / Higgs TTS 3 4B** — я пропустил его в первом развёрнутом отчёте. Это очень сильный multilingual/conversational кандидат: 100+ языков, русский входит в polished tier, zero-shot clone, inline emotion/style/prosody/SFX, 24 kHz. По официальным macro-benchmark и independent `tts-bench` он заметно сильнее VoxCPM2 по naturalness/SIM, но требует GPU-контур и имеет custom Research/Non-Commercial license.
- **MOSS-TTS v1.5 8B** — в первом отчёте был только кратко упомянут, без отдельного анализа. Полноценный `MossTTSDelay-8B v1.5` поддерживает 31 язык, включая русский, long-form, duration control и явные `[pause X.Ys]`; он сильнее VoxCPM2 по independent SIM/UTMOS, но 8B существенно тяжелее и ещё не прогонялся у нас на русских semantic blocks.
- **CosyVoice3** — сильный русскоязычный GPU-кандидат с streaming, 0.5B/1.5B вариантами и Apache-2.0; официальная CV3-таблица полезна, но на русском в ней VoxCPM2 имеет WER 5.21, а CosyVoice3 — 6.64–6.77, то есть переход на CosyVoice3 нельзя обосновать одним официальным числом.
- **IndexTTS2** — концептуально очень интересен для dubbing из-за duration/emotion control, но официальный репозиторий прямо отмечает, что заявленный precise duration control ещё не включён в текущий release. Русский production path также не подтверждён официальным model card.
- **F5-TTS** — сильная flow-matching архитектура и хороший speaker similarity в независимом тесте, но официальный base ориентирован на zh/en, русские checkpoints являются community fine-tunes, а официальные веса имеют CC-BY-NC-4.0. Для текущего production-проекта это не безопасная первая замена.
- **XTTS-v2** — зрелый и русский-compatible fallback, но model weights находятся под Coqui Public Model License, а code license и weight license нельзя смешивать. Для коммерческого выпуска нужно отдельное юридическое решение.
- **OpenVoice V2** и **GPT-SoVITS** — полезные архитектурные/экспериментальные варианты, но русский не входит в официально перечисленный native target для OpenVoice V2, а GPT-SoVITS официально перечисляет zh/en/ja/ko/yue, не русский.

**Практическое решение:** не выбрасывать VoxCPM2 и не переписывать orchestration под одну новую модель. Закончить текущий semantic-block pipeline, затем провести одинаковый bake-off **VoxCPM2 vs Qwen3-TTS 1.7B Base vs Chatterbox Multilingual V3**. На GPU добавить отдельные ветки **Higgs Audio v3 TTS** и **MOSS-TTS v1.5 8B**; CosyVoice3 — следующий GPU-тест. Fish S2-Pro — контрольный high-end тест только в разрешённом non-commercial/research или отдельно лицензированном контуре.

## Что именно проверено

### Локальные материалы проекта

Проверены доступные в checkout документы:

- `docs/voxcpm2/MODEL_COMPARISON_2026-07-26.md`;
- `docs/voxcpm2/SOURCES.md`;
- `docs/voxcpm2/QUALITY_RESEARCH_2026-07-26.md`;
- `docs/voxcpm2/CURRENT_STATE.md`;
- `docs/voxcpm2/HANDOFF_FOR_AI.md`;
- текущие `SEMANTIC_BLOCK_PRODUCTION.md` и `SPEECH_BACKEND_CONTRACT.md`.

Вложенные в сообщение файлы с ожидаемыми путями `/home/user/uploads/TTS_Research_100plus_Links_August_2026.md` и `/home/user/uploads/TTS_Landscape_Report_August_2026.md` в данном Arena-sandbox фактически отсутствуют. Поэтому я не выдаю их текст за прочитанный; выводы ниже основаны на доступных локальных исследованиях и отдельно выполненном интернет-sweep.

### Интернет-sweep

- Собрано **91 уникальный URL** (73 из них вошли в первый автоматический probe; 12 новых ссылок добавлены в ходе этой перепроверки), из них более 70 — официальные репозитории, model cards, технические отчёты, документация или upstream issues.
- Через `fetch_page` дополнительно прочитаны официальные страницы VoxCPM2, Qwen3-TTS, Fish Speech, CosyVoice/CV3-Eval, Chatterbox, F5-TTS, IndexTTS2, Higgs TTS 3, MOSS-TTS и независимого `tts-bench`.
- В shell-проверке первых 73 URL: **30 получили HTTP 200** напрямую через `curl`; для остальных Arena-сетевой стек завершал TLS-соединение ошибкой `curl 35`. Это не трактуется как 404: те же ключевые страницы были сверены через web-search/fetch_page.
- Независимый `tts-bench` сам предупреждает, что его объективные числа построены на пяти prompt-фразах; WER там является прежде всего детектором провала, а human votes — ground truth. Поэтому его цифры не переносятся напрямую на русскую проповедь.

## Единая постановка задачи

Наш target отличается от обычного TTS benchmark:

```text
English reference speaker
        + checked Russian translation
        + one-speaker sermon / monologue
        + source timing and subtitle windows
        + no original speech leakage in Russian master
        + exact word and stress QA
        -> Russian voice clone with stable timbre and natural cadence
```

Для нашего production-контракта модель должна работать внутри model-agnostic слоёв:

```text
SRT / semantic blocks
  -> backend adapter
  -> N candidates per complete block
  -> ASR/content/endpoint/identity QA
  -> accepted checkpoint
  -> previous-block continuation context when supported
  -> timeline assembly
  -> final media QA
```

`semantic blocks`, QA, checkpoints, ranking и master нельзя связывать с API конкретной модели. Model-specific код должен оставаться в `SpeechBackend` adapter.

## Сравнительная матрица

| Модель | Официальный русский | Cross-lingual cloning | Published/open license | Long-form / continuation | Главная сильная сторона | Главный риск для нас | Решение |
|---|---:|---:|---|---|---|---|---|
| **VoxCPM2 2B** | Да, 30 языков | Да; reference-only и continuation modes | Apache-2.0 | Нужна внешняя сегментация и re-anchor | Универсальность, 48 kHz, voice design, open fine-tune | drift, EOS/chewing, reference-tail leakage, CPU slow | Текущая baseline |
| **Qwen3-TTS 1.7B Base** | Да, 10 языков | Да, 3 s clone | Apache-2.0 | Официально заявлен сильный long-form и streaming; проверить runtime | Лучший прямой A/B для русского | CPU/RAM и длинный pacing не проверены у нас | A/B №1 |
| **Chatterbox Multilingual V3** | Да, 23+ / release заявляет 25 total | Да | MIT; PerTh watermark | Средний; блоки обязательны до измерения | Малый размер, русский, permissive license | accent leakage, repetition/noise, watermark, V3 ещё не наш bake-off | A/B №2 |
| **Fish Audio S2-Pro 4B** | Да, 80+ claimed, русский в model card | Да | Fish Audio Research License; commercial use требует отдельной лицензии | Сильные multi-turn/long-form claims | Raw quality, WER, inline tags, multi-speaker | 4B, H200-oriented runtime, legal boundary | High-end контрольный тест |
| **Higgs Audio v3 TTS 4B** | Да; русский в polished tier, 100+ languages | Да | Custom Research/Non-Commercial; Creator Use Grant имеет отдельные условия | Streaming/conversational context, 8k context | Сильный macro-WER, naturalness, inline emotion/style/SFX | H100/A100-class serving; custom license; 24 kHz | GPU A/B №3 |
| **MOSS-TTS v1.5 8B** | Да, 31 язык, включая ru | Да | Apache-2.0 | До 1 h claimed; duration + `[pause X.Ys]` | Long-form stability, explicit timing, IPA/Pinyin | 8B, GPU/RAM heavy; Russian block render не проверен | GPU A/B №4 |
| **CosyVoice3 0.5B/1.5B** | Да, 9 языков | Да | Apache-2.0 model card | Streaming, RAS, continuation-oriented API | Компактность, streaming, pronunciation inpainting | Russian score не лучше VoxCPM2 в CV3 table; runtime stack тяжелее | GPU A/B №5 |
| **IndexTTS2 1.5B** | Официально не подтверждён как русский target | Ограниченно / зависит от model path | Code/model terms проверять отдельно | Research paper обещает duration control, current release его не включает | Emotion/timbre disentanglement, duration concept | Русская фонетика и фактический duration API не доказаны | Не брать первым |
| **F5-TTS v1** | Base zh/en; Russian community checkpoints | Не является гарантированным base path | Code MIT, pretrained weights CC-BY-NC-4.0 | Flow matching; long text требует chunking | Высокий SIM и естественный flow path | license, Russian checkpoint quality, WER | Research only |
| **XTTS-v2** | Да, 17 языков, включая ru | Да | CPML для weights | Sentence splitting; mature API | Зрелый русский fallback, простой clone | CPML и weaker independent SIM | Только legal-approved fallback |
| **OpenVoice V2** | Native: en/es/fr/zh/ja/ko | Claimed zero-shot cross-lingual | MIT | Двухступенчатый base TTS + tone converter | Style/timbre separation, speed | Russian native support отсутствует; нужен другой base TTS | Не target baseline |
| **GPT-SoVITS** | Официально не перечислен | zh/en/ja/ko/yue | MIT code; model/checkpoint terms отдельно | WebUI/fine-tune, не единый long-form clone | 5 s zero-shot, 1 min few-shot, ecosystem | нет официального русского production path | Community experiment |
| **LongCat / VibeVoice** | Для русского не подтверждено в нашем scope | Частично | Проверять per-model | Сильные long-form claims у отдельных моделей | Исследовательский потолок | size, hardware, unknown Russian fidelity | Не текущий A/B |
| **Piper / Kokoro** | Есть русские/многоязычные варианты, но это preset voices | Нет zero-shot cloning | Обычно permissive per model | Очень быстрые | CPU baseline для нейтральной речи | не сохраняют голос John Piper | Только ASR/timing sanity |

## Benchmark: что можно сравнивать честно

### 1. Официальный VoxCPM2 / public technical report

В VoxCPM2 report опубликованы следующие representative Seed-TTS-Eval результаты:

```text
VoxCPM2 2B, test-EN:      WER 1.84 / SIM 75.3
VoxCPM2 2B, test-ZH:      CER 0.97 / SIM 79.5
VoxCPM2 2B, test-ZH-Hard: CER 8.13 / SIM 75.3
```

В official repository CV3-Eval multilingual table для русского указано:

```text
VoxCPM2:       WER 5.21
Fish Audio S2: WER 2.78
CosyVoice3:    WER 6.64 (1.5B) / 6.77 (0.5B)
```

Это полезная baseline-информация, но не русский English→Russian sermon A/B. CV3-Eval измеряет разные subsets, reference conditions и ASR/embedding pipeline; его нельзя читать как гарантию качества конкретного МакАртура.

### 2. Независимый `tts-bench`, cloning track

`tts-bench` использует одинаковые пять prompts и один cloning reference (`chris_hemsworth_15s`). Значения ниже — доли/score именно этого теста, не проценты русской речи:

| Модель | SIM ↑ | UTMOS ↑ | WER ↓ | Интерпретация |
|---|---:|---:|---:|---|
| LongCat-AudioDiT 1B | 0.870 | 3.881 | 0.146 | Высокий SIM, русский не подтверждён |
| IndexTTS2 | 0.810 | 3.705 | 0.087 | Высокий SIM, language/duration gate не пройден |
| F5-TTS v1 | 0.769 | 4.027 | 0.195 | Сильное звучание, но base/license/russian issues |
| Fish S2-Pro | 0.725 | 4.270 | 0.058 | Сильный quality/SIM баланс, но 4B + Research License |
| MOSS-TTS v1.5 8B | 0.699 | 4.050 | 0.060 | SIM/UTMOS выше VoxCPM2, но 8B и нет нашего Russian run |
| Higgs Audio v3 TTS 4B | 0.666 | 4.245 | 0.046 | Naturalness/SIM заметно выше VoxCPM2, но custom license/GPU |
| CosyVoice3 0.5B | 0.723 | 3.761 | 0.501 | SIM хороший, content WER в этом run плохой |
| Qwen3-TTS 1.7B (CUDA-graph) | 0.629 | 3.938 | 0.077 | Сильный direct competitor |
| Chatterbox | 0.627 | 4.278 | 0.073 | Очень хороший naturalness, V3 нужно проверить отдельно |
| XTTS-v2 | 0.420 | 3.890 | 0.076 | Content acceptable, cloning SIM слабее |
| VoxCPM2 2B | 0.533 | 3.596 | 0.040 | WER-фильтр сильный, SIM/UTMOS ниже лидеров |
| OpenVoice V2 | 0.247 | 4.048 | 0.075 | Naturalness good, identity SIM слабый |

Корректное чтение таблицы:

- VoxCPM2 в этом независимом наборе хорошо проходит content/ASR gate, но не выигрывает speaker similarity или predicted naturalness.
- Это **не** доказывает, что Fish, IndexTTS2 или LongCat лучше говорят по-русски.
- Это **доказывает**, что нельзя называть VoxCPM2 unconditional SOTA только по одному official benchmark.
- Для нашей задачи SIM нельзя использовать единственным selector: модель может получить высокий embedding similarity из-за тембра, но ошибиться в русском ударении, окончаниях или theology text.

**Что добавляют эти две модели:**

- **Higgs TTS 3:** в `tts-bench` он получил SIM 0.666 / UTMOS 4.245 / WER 0.046. То есть он заметно выше VoxCPM2 по SIM и UTMOS, а по WER почти рядом. В официальном Boson/LMSYS macro-report указаны Seed-TTS 1.11, CV3 4.41, MiniMax-Multilingual 2.74 и internal Higgs-Multilingual 3.61. Это сильный сигнал в пользу naturalness/expressiveness, но не Russian sermon proof: значения macro-averaged, benchmark vendor-linked, а независимый `tts-bench` всего на пяти фразах.
- **MOSS-TTS v1.5:** в `tts-bench` SIM 0.699 / UTMOS 4.050 / WER 0.060. Он уступает Higgs по UTMOS и немного уступает VoxCPM2 по WER, но заметно выигрывает у VoxCPM2 по SIM/UTMOS. Официальный MOSS model card добавляет именно те knobs, которых не хватает для dubbing: language tag, token-level duration, IPA/Pinyin, long-form и `[pause X.Ys]`. Это делает MOSS особенно интересным для timing, однако 8B path нужно проверять по памяти, RTF, финальным окончаниям и continuity.

### 3. Qwen3-TTS и Fish S2

Официальный Qwen report заявляет 10 языков, включая русский, сильные Seed-TTS результаты, streaming и более 10 минут long-form; это делает Qwen3-TTS самым важным A/B, но claims должны быть воспроизведены теми же Russian blocks и тем же ASR.

Fish S2 report использует Seed-TTS, CV3-Eval, MiniMax multilingual и long-form evaluation. Он показывает очень сильные objective и instruction-following результаты, но open weights находятся под Fish Audio Research License: research/non-commercial разрешены, commercial use требует отдельной письменной лицензии. Это исключает Fish S2-Pro из безусловного production default для коммерческого сервиса.

## Model-by-model выводы

### VoxCPM2

**Плюсы для проекта:**

- 30 официально перечисленных языков, включая русский;
- English reference → Russian target предусмотрен модельным режимом;
- reference-only cloning позволяет не тащить source-language acoustic bed в русский candidate;
- continuation/Ultimate API даёт возможность строить предыдущий accepted block как optional context;
- Apache-2.0 и открытый код;
- 48 kHz output и открытые SFT/LoRA scripts;
- уже есть локальный CPU baseline, reference assets и проверенный pipeline.

**Минусы, подтверждённые не только теорией:**

- upstream issue #302 описывает long-form drift, когда conditioning всё больше зависит от собственных latent features;
- upstream issue #272 описывает chirp/click/reference-tail leakage;
- upstream issue #213 описывает случайные обрезания последних слогов/согласных;
- issue #276 показывает, что надежное управление паузой через text instruction отсутствует;
- в текущем независимом `tts-bench` SIM 0.533 и UTMOS 3.596 уступают нескольким конкурентам;
- текущий локальный CPU RTF непригоден для быстрых итераций длинной проповеди без checkpoint/resume.

**Production decision:** оставить VoxCPM2 baseline; использовать semantic blocks 7–15 s, несколько кандидатов на полный block, fixed identity reference, optional previous-block prompt only when adapter supports it, content/endpoint/identity QA и Russian stress lexicon.

### Qwen3-TTS 1.7B Base

**Почему это первый A/B:**

- тот же target class: multilingual voice clone, русский, cross-lingual, short reference;
- Apache-2.0;
- отдельный Base checkpoint, то есть сравнение будет с voice cloning, а не с curated CustomVoice;
- официально заявлены streaming и long-form;
- independent `tts-bench` показывает SIM 0.629 / UTMOS 3.938 / WER 0.077 против VoxCPM2 0.533 / 3.596 / 0.040.

**Что нельзя обещать заранее:**

- официальные benchmark не являются русской проповедью;
- русская pronunciation quality на словах с lexical stress пока не измерена;
- CPU RTF/RAM в нашей среде отсутствуют;
- long-form claim требует проверки на 60–120 секунд с одинаковым reference и block context;
- независимый `tts-bench` score относится к варианту `Qwen3-TTS 1.7B (CUDA-graph)`, а не автоматически к любому 1.7B checkpoint;
- Qwen3 использует discrete tokenizer/codebooks, поэтому его endpoint failure modes будут другими, но это не означает автоматическое отсутствие chewing.

### Chatterbox Multilingual V3

**Почему это второй A/B:**

- 500M и русский out of the box;
- MIT;
- built-in PerTh watermark — полезно для provenance, но output нельзя считать полностью «чистым» без решения, приемлем ли watermark для публикации;
- official README отдельно советует снижать `cfg_weight`, если reference language не совпадает с target language, чтобы уменьшить accent transfer. Это напрямую относится к English → Russian.

**Risk:** V3 официально улучшает hallucination/similarity, но текущий independent `tts-bench` в основном содержит старый Chatterbox entry; нельзя переносить его numbers на V3. Прогнать нужно именно V3, русский, English reference, `language_id="ru"`, neutral/authoritative settings и три кандидата на block.

### Higgs Audio v3 TTS / Higgs TTS 3 4B

Ты прав: в первом отчёте **Higgs TTS 3 был пропущен как отдельная модель**. Он присутствовал только в свежей выдаче `tts-bench`, но не был разобран в matrix/model-by-model разделе. Это исправлено этим addendum.

**Что подтверждено официально:**

- модель `bosonai/higgs-audio-v3-tts-4b` — примерно 4B autoregressive decoder на Qwen3-4B;
- 8 audio codebooks, delay pattern, 25 fps, выход 24 kHz;
- zero-shot voice cloning, reference audio и optional reference transcript;
- 100+ языков; русский включён в официально перечисленный polished tier с WER/CER under 5 в model card;
- inline tags для emotion, style, prosody, pauses и sound effects;
- official serving path — SGLang-Omni/vLLM-Omni, OpenAI-compatible endpoint;
- published serving numbers ориентированы на H100 80 GB; отдельная operational note указывает A100 40 GB как подтверждённый floor, а меньшие GPU не считаются проверенными.

**Benchmarks:**

```text
Official/LMSYS macro results:
Seed-TTS:             1.11
CV3:                  4.41
MiniMax-Multilingual: 2.74
Higgs-Multilingual:   3.61

tts-bench cloning:
SIM:                  0.666
UTMOS:                4.245
WER:                  0.046
```

В `tts-bench` Higgs заметно лучше VoxCPM2 по SIM `0.666 vs 0.533` и UTMOS `4.245 vs 3.596`; VoxCPM2 имеет немного меньший WER `0.040 vs 0.046`. Это очень сильный аргумент в пользу Higgs как **GPU quality A/B**, но не доказательство лучшего русского дубляжа: `tts-bench` использует пять фраз и не является Russian sermon test, а официальные числа macro-averaged и vendor-linked.

**Главный риск:** лицензия не равна Apache/MIT. Official model card говорит Research and Non-Commercial License; в нём есть Creator Use Grant для digital creators, включая monetized podcasts/videos/social posts, при обязательном credit Boson AI. Но embedding в product/service, hosted API, reselling или revenue-generating production требует separate commercial license. Для нашего Telegram bot/service path это отдельный legal gate.

**Наш вывод:** Higgs TTS 3 raw quality и expressiveness выглядят сильнее VoxCPM2, но текущий CPU-only pipeline его не заменяет. Его нужно прогонять на отдельной исправной GPU или через разрешённый hosted trial, не смешивая с VoxCPM2 runtime и не меняя model-agnostic orchestration.

### MOSS-TTS v1.5 8B

В старом отчёте MOSS был только в общей строке с LongCat/VibeVoice. Это было недостаточно: **MossTTSDelay-8B v1.5** — отдельный прямой кандидат для нашей задачи.

**Что подтверждено official model card/repository:**

- production-recommended checkpoint — `MossTTSDelay-8B v1.5`;
- 8B autoregressive delay-pattern architecture;
- 31 язык, включая русский;
- zero-shot voice cloning и continuation;
- long-form до одного часа заявлено model card;
- token-level duration control;
- Pinyin/IPA pronunciation control;
- language tag рекомендуется указывать явно для multilingual synthesis;
- explicit pause markers вида `[pause 3.2s]`;
- improved v1.5 stability, punctuation-following prosody и long-reference short-text cloning;
- family models released under Apache-2.0.

**Не путать две ветки:**

- `MossTTSDelay-8B v1.5` — production flagship, обычно 24 kHz path;
- `MOSS-TTS-Local-Transformer-v1.5` — отдельная примерно 4B ветка на Qwen3-4B с MOSS-Audio-Tokenizer-v2 и 48 kHz stereo/streaming path.

Пользователь указал именно **v1.5 8B**, поэтому в этом отчёте benchmark и risk относятся к Delay-8B, а не к 4B Local Transformer.

**Independent `tts-bench` cloning:**

```text
MOSS-TTS v1.5 8B:
SIM:   0.699
UTMOS: 4.050
WER:   0.060
```

По сравнению с VoxCPM2 `0.533 / 3.596 / 0.040`, MOSS заметно выигрывает по speaker similarity и predicted naturalness, но уступает по WER. Для нашей задачи это означает: MOSS может быть более монолитным и похожим по голосу, но content gate и русское ударение всё равно обязательны.

**Что особенно полезно для dubbing:** explicit duration/pause controls потенциально лучше подходят для subtitle windows, чем попытка заставлять модель заполнить окно через высокий `min_len`. Но `[pause X.Ys]` нельзя автоматически считать естественным: нужно проверять, не превращается ли пауза в слышимый шов.

**Риски:** 8B path тяжёлый по RAM/VRAM, official runtime требует отдельного окружения/Transformers 5.x/FlashAttention или SGLang path, а у нас нет реального Russian semantic-block render. `tts-bench` не проверяет русскую phonetics, а официальные цифры MOSS не заменяют наш A/B.

**Наш вывод:** MOSS-TTS v1.5 8B — самый интересный кандидат после Higgs/Fish для GPU-бенчмарка, особенно если приоритет — долгий монолог, speaker stability и точный pause/duration control. Он не должен встраиваться напрямую в orchestration: нужен только новый `SpeechBackend` adapter.

### Fish Audio S2-Pro / S2.1

Fish S2-Pro сильнее всех выглядит на raw quality benchmark: 4B Dual-AR, inline natural-language tags, multi-speaker/multi-turn, long-form, русский и очень сильные WER/SIM. Но:

- model files и runtime существенно тяжелее VoxCPM2/Qwen/Chatterbox;
- reference model card требует H200-oriented setup для опубликованного performance context;
- Fish Audio Research License не даёт коммерческое право автоматически;
- S2.1 Pro, опубликованный в конце июля 2026, нельзя смешивать в одной таблице с open-weight S2-Pro: это новое поколение/current hosted offering с отдельным deployment/legal path.

**Decision:** Fish — контроль качества/облачный A/B, не замена текущему CPU worker.

### CosyVoice3

CosyVoice3 интересна тем, что одновременно даёт русский, 0.5B/1.5B, streaming, instruct control, RAS и pronunciation inpainting. Она сильна как GPU backend, особенно если нужна низкая latency или серверный режим.

Однако official CV3 Russian WER хуже VoxCPM2, а independent `tts-bench` выдаёт SIM 0.723 при WER 0.501. Это хороший пример, почему identity score без content QA опасен. Для богословской проповеди CosyVoice3 стоит проверять только с обязательным ASR coverage и exact-text rejection.

### IndexTTS2

IndexTTS2 закрывает важнейшую dubbing-идею: разделение timbre/emotion и precise duration control. Но official GitHub прямо говорит, что precise synthesis duration functionality **not yet enabled in this release**. Поэтому в документации нельзя писать, что текущий inference действительно умеет миллисекундный fit.

Также current official language metadata не подтверждает русский production checkpoint. Даже если community fork читает русский, это отдельный model card/checkpoint/license, а не доказательство официальной поддержки. Не брать до отдельного Russian smoke и юридического review.

### F5-TTS и Russian fine-tunes

F5-TTS интересен flow matching и strong independent SIM. Но official `SHARED.md` разделяет base zh/en и voluntary community language checkpoints. В Russian section есть community checkpoint, а отдельный Russian model card указывает CC-BY-NC-SA-4.0. Следовательно:

- base F5-TTS нельзя маркировать как guaranteed native Russian;
- community Russian quality нельзя приписывать upstream base;
- code MIT не превращает pretrained weights в MIT;
- production use требует отдельной проверки checkpoint license.

### XTTS-v2

XTTS-v2 официально поддерживает русский и 17 языков, имеет зрелый Coqui API и простую speaker reference модель. Это полезный baseline для content/timing smoke, но:

- CPML относится к weights, не к toolkit code;
- independent `tts-bench` ставит SIM 0.420, ниже VoxCPM2 и большинства текущих cloning candidates;
- нет нужного нам modern semantic-block continuation contract без собственного adapter.

### OpenVoice V2 и GPT-SoVITS

OpenVoice V2 силён в разделении tone color и style, MIT и лёгок, но official native languages — English, Spanish, French, Chinese, Japanese, Korean. Для Russian target потребуется другой base TTS или community path; это уже не честное сравнение «модель из коробки».

GPT-SoVITS хорош как ecosystem/few-shot tool: 5-second zero-shot, 1-minute few-shot и WebUI. Но upstream README перечисляет cross-lingual English/Japanese/Korean/Cantonese/Chinese, а русского production checkpoint нет. Community Russian forks нельзя использовать как evidence для upstream.

## Рейтинг для нашего проекта, не общий рейтинг рынка

### Сейчас, с текущим CPU и без реального нового model runtime

1. **VoxCPM2** — единственная уже проверенная и интегрированная production baseline.
2. **Chatterbox Multilingual V3** — следующий лёгкий A/B, если CPU install проходит.
3. **Qwen3-TTS 1.7B Base** — обязательный quality A/B, но сначала отдельное окружение и измерение CPU/RAM.
4. **XTTS-v2** — compatibility/content fallback после legal review.
5. **F5-TTS Russian** — только non-commercial research, не production default.

### После исправной GPU/удалённого GPU с достаточной VRAM

1. **Higgs TTS 3 + Fish S2-Pro** — quality-control tier. Higgs сильнее по опубликованному macro content/expressiveness profile, Fish — по independent SIM/UTMOS; обе модели требуют license/runtime gate.
2. **MOSS-TTS v1.5 8B** — наиболее интересен для long-form, duration и pause control; требуется тяжёлый GPU A/B.
3. **Qwen3-TTS 1.7B Base** — наиболее сбалансированный русский competitor с более доступным размером.
4. **CosyVoice3 0.5B/1.5B** — streaming/production candidate.
5. **VoxCPM2** — strongest existing CPU/pipeline/control baseline.
6. **Chatterbox V3** — license/provenance/size advantage, quality must be measured on Russian.
7. **IndexTTS2** — only after official duration/runtime and Russian gates.

Это не утверждение, что модель №1 всегда звучит лучше. Это порядок экспериментов при наших ограничениях.

## Обязательный честный bake-off

### Test package

Один и тот же пакет для всех backend adapters:

1. `reference_identity.wav`: 15–25 s, один спикер, cleaned close-mic, без музыки и applause;
2. `reference_continuation.wav`: тот же speaker, отдельный anchor;
3. exact Russian transcript с ручной stress evidence;
4. 10 semantic blocks по 7–15 s;
5. 1 long-form chain 60–120 s;
6. один target SRT и одинаковые timing windows.

### Фразы и failure probes

- спокойное утверждение;
- authoritative theological sentence;
- длинное предложение с двумя subordinate clauses;
- перечисление;
- вопрос и ответ;
- фраза с `грядёт`, `придёт`, `возвестит`, `совершит`;
- фраза с финальными согласными `ст, зд, ть, нт`;
- короткая, но не однословная фраза;
- controlled pause around punctuation;
- block ending with definitive period and no following text.

### Для каждого candidate сохранять

```text
model / revision / checkpoint hash
backend adapter version
reference ID + exact reference transcript
language / mode / seed
CFG / steps / min_len / max_len / temperature
raw duration / speech duration / leading & trailing silence
ASR transcript / text coverage / WER
speaker similarity + model used
speaking rate / pitch range (diagnostic only)
clipping / loudness / tail detector
pause-restart / repetition / hallucinated suffix
human notes: timbre, cadence, emotion, echo, stress
```

### Acceptance gates

1. **Content gate:** 100% critical words, no invented or missing theology terms. WER — rejector, not sole ranker.
2. **Russian pronunciation gate:** explicit check of stress, especially `грядёт`, not `грЯдет`.
3. **Identity gate:** one speaker cluster across blocks; no bass voice or reference speaker swap.
4. **Continuity gate:** no abrupt register jump, no cadence reset at every block, no synthetic breath mismatch.
5. **Endpoint gate:** no clipped final consonant, no pause→chewing restart, no broadband island after speech.
6. **Timing gate:** short audio is padded with silence; it is not slowed to fill the slot. Long audio is accelerated only in a bounded, reviewed range.
7. **Master gate:** Russian-only direct master is the publication candidate; original English bed is a separately mixed optional track and never used to hide source leakage.

Suggested decision rule:

```text
Reject any candidate failing content or endpoint gates.
Among survivors, rank:
  35% human naturalness/continuity
  25% Russian content + stress correctness
  20% speaker identity
  10% endpoint cleanliness
  10% timing fit / compute cost
```

Source-language F0/energy may be logged as diagnostic, but it must not rank a Russian candidate or expand identity limits in the direct production path.

## Что менять в коде после исследования

### Уже правильно оставлять

- `semantic_block_runtime.py` как orchestration layer;
- `source_prosody_policy.py` с diagnostic-only role;
- `SpeechBackend.build_renderer_command()` и `build_master_command()`;
- backend identity в fingerprints;
- checkpoint/resume после принятого полного semantic block;
- candidate QA до timeline assembly;
- Russian-only direct master для problematic Piper clip;
- fixed identity anchor отдельно от previous-block continuation prompt.

### Следующая реализация

1. Добавить `Qwen3TTSBackend` только в adapter layer: load/generate/stream/save metadata. Не трогать block grouping, QA, checkpoints и master.
2. Добавить `ChatterboxMultilingualV3Backend` с явным `language_id="ru"`, `cfg_weight` и watermark metadata.
3. Добавить `HiggsAudioV3Backend` с 24 kHz metadata, inline-tag policy, `ref_audio/ref_text` mapping и обязательным license gate.
4. Добавить `MOSSTTSV15Backend` для `MossTTSDelay-8B v1.5`; отдельно не смешивать его с `MOSS-TTS-Local-Transformer-v1.5`.
5. Сделать единый `bakeoff_manifest.json` для повторяемого 10-block теста.
6. Добавить backend-neutral report schema для WER, SIM, UTMOS, tail, stress and continuity.
7. Ввести license metadata gate: `commercial_allowed`, `weights_license`, `output_policy`, `watermark_policy`.
8. Только после CPU smoke запускать дорогие Windows/GPU renders.

Нельзя делать:

- подменять плохую короткую фразу другой моделью в середине блока;
- ранжировать русский candidate по English source F0;
- объявлять модель лучшей по одному WER или SIM;
- использовать community Russian checkpoint как доказательство upstream support;
- смешивать Fish S2-Pro open weights с Fish S2.1 hosted/current offering;
- считать HTTP/TLS failure sandbox-а доказательством, что URL мёртв.

## Источники: 91 URL

### VoxCPM2 — official, paper, docs, upstream evidence

1. [OpenBMB/VoxCPM repository](https://github.com/OpenBMB/VoxCPM) — canonical code, README, benchmark tables.
2. [VoxCPM2 technical report, arXiv](https://arxiv.org/abs/2606.06928) — architecture and official evaluation.
3. [VoxCPM2 technical report, HTML](https://arxiv.org/html/2606.06928) — readable primary report.
4. [VoxCPM2 model card](https://huggingface.co/openbmb/VoxCPM2) — model usage and supported languages.
5. [VoxCPM2 config](https://huggingface.co/openbmb/VoxCPM2/blob/main/config.json) — checkpoint metadata.
6. [VoxCPM quickstart](https://voxcpm.readthedocs.io/en/latest/quickstart.html) — installation and inference.
7. [VoxCPM usage guide](https://voxcpm.readthedocs.io/en/latest/usage_guide.html) — cloning and generation controls.
8. [VoxCPM2 model guide](https://voxcpm.readthedocs.io/en/latest/models/voxcpm2.html) — reference-only/continuation modes.
9. [VoxCPM FAQ](https://voxcpm.readthedocs.io/en/latest/faq.html) — runtime caveats.
10. [VoxCPM fine-tuning guide](https://voxcpm.readthedocs.io/en/latest/fine_tuning.html) — SFT/LoRA.
11. [VoxCPM deployment docs](https://voxcpm.readthedocs.io/en/latest/deployment/index.html) — serving context.
12. [VoxCPM PyPI](https://pypi.org/project/voxcpm/) — package/release provenance.
13. [VoxCPM core.py](https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/core.py) — public generate path.
14. [VoxCPM2 model source](https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/model/voxcpm2.py) — prompt cache and inference behavior.
15. [Issue #302: long-form voice drift](https://github.com/OpenBMB/VoxCPM/issues/302) — upstream report and source analysis.
16. [Issue #272: reference-tail chirp/click](https://github.com/OpenBMB/VoxCPM/issues/272) — upstream artifact report.
17. [Issue #213: final word/syllable truncation](https://github.com/OpenBMB/VoxCPM/issues/213) — endpoint risk.
18. [Issue #285: accent control](https://github.com/OpenBMB/VoxCPM/issues/285) — voice-design accent reliability.
19. [Issue #276: pause/silence control](https://github.com/OpenBMB/VoxCPM/issues/276) — punctuation/timing limitation.
20. [Issue #357: short-input hallucination](https://github.com/OpenBMB/VoxCPM/issues/357) — avoid one-word repair clips.
21. [vLLM-Omni issue #2896](https://github.com/vllm-project/vllm-omni/issues/2896) — EOS failure in an integration path, not proof of upstream core failure.

### Qwen3-TTS

22. [QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) — official code, model list and evaluations.
23. [Qwen3-TTS technical report](https://arxiv.org/abs/2601.15621) — primary research report.
24. [Qwen3-TTS technical report HTML](https://arxiv.org/html/2601.15621) — readable report.
25. [Qwen3-TTS blog](https://qwen.ai/blog?id=qwen3tts-0115) — official release context.
26. [Qwen3-TTS 1.7B Base model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base) — cloning checkpoint.
27. [Qwen3-TTS VoiceDesign model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign) — design/control checkpoint.
28. [Qwen3-TTS tokenizer model card](https://huggingface.co/Qwen/Qwen3-TTS-Tokenizer-12Hz) — tokenizer/codec metadata.

### Fish Audio S2 / S2.1

29. [Fish Speech repository](https://github.com/fishaudio/fish-speech) — official implementation.
30. [Fish Speech English docs](https://github.com/fishaudio/fish-speech/blob/main/docs/en/index.md) — installation and runtime.
31. [Fish S2-Pro model card](https://huggingface.co/fishaudio/s2-pro) — languages, hardware and license metadata.
32. [Fish S2-Pro license](https://huggingface.co/fishaudio/s2-pro/blob/main/LICENSE.md) — research/non-commercial/commercial boundary.
33. [Fish S2 technical report](https://arxiv.org/abs/2603.08823) — primary evaluation.
34. [Fish S2 technical report HTML](https://arxiv.org/html/2603.08823) — readable report.
35. [Fish Audio S2 launch post](https://fish.audio/blog/fish-audio-open-sources-s2/) — official feature announcement.
36. [Fish S2 inline-control post](https://fish.audio/blog/fish-audio-s2-fine-grained-ai-voice-control-at-the-word-level/) — tags and multilingual claims.
37. [Fish S2.1 Pro announcement](https://fish.audio/blog/s2-1-pro-free-api/) — separate current hosted/API offering.
38. [Fish Speech license in repository](https://github.com/fishaudio/fish-speech/blob/main/LICENSE) — primary license text.

### CosyVoice3 / CV3-Eval

39. [QwenAudio/CosyVoice repository](https://github.com/QwenAudio/CosyVoice) — official code and release links.
40. [Fun-CosyVoice3 0.5B model card](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512) — language/license/runtime metadata.
41. [CosyVoice3 paper](https://arxiv.org/abs/2505.17589) — architecture and CV3 results.
42. [CosyVoice3 paper HTML](https://arxiv.org/html/2505.17589v2) — readable tables.
43. [CV3-Eval repository](https://github.com/FunAudioLLM/CV3-Eval) — dataset, metrics and evaluation code.
44. [CosyVoice3 demo page](https://funaudiollm.github.io/cosyvoice3/) — official examples.
45. [CosyVoice 3 model on ModelScope](https://www.modelscope.cn/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512) — alternative official distribution.

### Chatterbox Multilingual V3

46. [ResembleAI/chatterbox](https://github.com/resemble-ai/chatterbox) — official repo and language/runtime instructions.
47. [Chatterbox model card](https://huggingface.co/ResembleAI/chatterbox) — multilingual model metadata.
48. [Resemble model overview](https://www.resemble.ai/learn/models/chatterbox-multilingual) — official product/model summary.
49. [Chatterbox V3 watermark announcement](https://www.resemble.ai/resources/chatterbox-multilingual-v3-tts-with-embedded-watermarking-for-25-languages) — V3 and PerTh details.
50. [Chatterbox PyPI](https://pypi.org/project/chatterbox-tts/) — package provenance.
51. [PerTh watermark repository](https://github.com/resemble-ai/perth) — watermark implementation reference.

### F5-TTS and Russian variants

52. [SWivid/F5-TTS](https://github.com/SWivid/F5-TTS) — official code.
53. [F5-TTS paper](https://arxiv.org/abs/2410.06885) — flow-matching architecture.
54. [F5-TTS shared model cards](https://github.com/SWivid/F5-TTS/blob/main/src/f5_tts/infer/SHARED.md) — base/community language checkpoints and licenses.
55. [F5-TTS model card](https://huggingface.co/SWivid/F5-TTS) — weights and license.
56. [F5-TTS Russian checkpoint](https://huggingface.co/hotstone228/F5-TTS-Russian) — community Russian fine-tune and NC-SA license.
57. [Cross-Lingual F5-TTS paper](https://arxiv.org/html/2509.14579v1) — research extension, not official base checkpoint.

### IndexTTS2

58. [index-tts repository](https://github.com/index-tts/index-tts) — official README and current release caveat.
59. [IndexTTS2 paper](https://arxiv.org/abs/2506.21619) — duration/emotion research.
60. [IndexTTS2 model card](https://huggingface.co/IndexTeam/IndexTTS-2) — checkpoint metadata.
61. [IndexTTS2 paper page](https://huggingface.co/papers/2506.21619) — paper summary and audio links.

### XTTS-v2, OpenVoice, GPT-SoVITS

62. [XTTS-v2 documentation](https://docs.coqui.ai/en/latest/models/xtts.html) — official API and language list.
63. [XTTS-v2 model card](https://huggingface.co/coqui/XTTS-v2) — Russian support and CPML.
64. [Coqui Public Model License](https://coqui.ai/cpml) — weight license terms.
65. [MyShell OpenVoice repository](https://github.com/myshell-ai/OpenVoice) — official implementation.
66. [OpenVoice V2 model card](https://huggingface.co/myshell-ai/OpenVoiceV2) — native language and MIT claims.
67. [OpenVoice paper](https://arxiv.org/abs/2312.01479) — cross-lingual tone-color/style architecture.
68. [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) — official WebUI and few-shot path.
69. [GPT-SoVITS README](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/README.md) — official language and CPU/GPU notes.

### Benchmarks and additional candidates

70. [Seed-TTS paper](https://arxiv.org/abs/2406.02430) — WER/SIM methodology and limitations.
71. [Seed-TTS evaluation repository](https://github.com/BytedanceSpeech/seed-tts-eval) — evaluation code.
72. [tts-bench scores](https://5uck1ess.github.io/tts-bench/scores.html) — independent five-prompt objective comparison.
73. [tts-bench speed](https://5uck1ess.github.io/tts-bench/speed.html) — per-rig speed/RAM data.
74. [tts-bench listening page](https://5uck1ess.github.io/tts-bench/listen.html) — blind audio examples.
75. [OpenMOSS/MOSS-TTS](https://github.com/OpenMOSS/MOSS-TTS) — long-form/high-capacity alternative.
76. [LongCat-AudioDiT](https://github.com/meituan-longcat/LongCat-AudioDiT) — non-AR candidate with strong independent SIM.
77. [Microsoft VibeVoice](https://github.com/microsoft/VibeVoice) — long-form/multi-speaker candidate.
78. [Rhasspy Piper](https://github.com/rhasspy/piper) — CPU/preset-voice baseline, not cloning.
79. [Kokoro-82M model card](https://huggingface.co/hexgrad/Kokoro-82M) — lightweight preset-voice baseline.

### Higgs Audio v3 TTS / Higgs TTS 3

80. [Higgs TTS 3 model card](https://huggingface.co/bosonai/higgs-audio-v3-tts-4b) — official architecture, 100+ languages, Russian tier, controls and license.
81. [Higgs TTS 3 renamed model card](https://huggingface.co/bosonai/higgs-tts-3-4b) — current model naming and Creator Use Grant.
82. [Boson AI Higgs Audio repository](https://github.com/boson-ai/higgs-audio) — official release/API boundary and license notice.
83. [Boson AI Higgs TTS 3 announcement](https://www.boson.ai/blog/higgs-audio-v3-tts) — official benchmark and conversational behavior results.
84. [LMSYS/SGLang Higgs TTS report](https://www.lmsys.org/blog/2026-06-04-higgs-audio-v3-tts/) — serving, macro WER/CER and architecture.
85. [SGLang Higgs cookbook](https://sgl-project.github.io/sglang-omni/cookbook/higgs_tts.html) — runtime and inline-control details.
86. [vLLM-Omni Higgs recipe](https://github.com/vllm-project/vllm-omni/blob/main/recipes/BosonAI/Higgs-Audio-V3-TTS.md) — H100/A100 deployment information.

### MOSS-TTS v1.5 8B

87. [MOSS-TTS license](https://github.com/OpenMOSS/MOSS-TTS/blob/main/LICENSE) — Apache-2.0 model-family license.
88. [MOSS-TTS official model card](https://github.com/OpenMOSS/MOSS-TTS/blob/main/docs/moss_tts_model_card.md) — v1.5 language, long-form, duration and pause controls.
89. [MOSS-TTS v1.5 model card](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-v1.5) — exact 8B checkpoint and supported languages.
90. [MOSS-TTS technical report](https://arxiv.org/abs/2603.18090) — primary research report.
91. [LMSYS MOSS Local Transformer v1.5 report](https://www.lmsys.org/blog/2026-06-17-moss-tts-local-v15/) — separate 4B/48 kHz streaming branch; not the 8B Delay checkpoint.

## Финальная рекомендация

На текущем этапе переходить с VoxCPM2 на другую модель **без одинакового русского bake-off нельзя**. Более высокий общий score в чужом benchmark не решает наши конкретные проблемы: русский stress, monolithic continuity, endpoint chewing, source leakage и timing.

Правильная последовательность:

```text
VoxCPM2 v6.8 orchestration + semantic blocks
  -> Qwen3-TTS 1.7B Base adapter + same manifest
  -> Chatterbox Multilingual V3 adapter + same manifest
  -> Higgs Audio v3 TTS GPU/hosted adapter + license gate
  -> MOSS-TTS v1.5 8B GPU adapter + same manifest
  -> CosyVoice3 GPU adapter
  -> Fish S2-Pro only in separately approved license/runtime
```

До появления реальных одинаковых WAV/JSON результатов утверждение должно звучать так:

> VoxCPM2 — проверенная текущая baseline и сильная универсальная открытая модель, но не доказанный абсолютный лидер русского дубляжа. Qwen3-TTS — главный обязательный A/B, Chatterbox V3 — самый перспективный лёгкий альтернативный backend, Higgs TTS 3 — сильный high-end по macro quality/naturalness, MOSS-TTS v1.5 — сильный long-form/duration кандидат, CosyVoice3 — streaming GPU-кандидат, Fish S2-Pro — high-end reference с отдельным license gate.

Это честнее и технически полезнее, чем объявлять победителя по рекламной таблице или одному автоматическому SIM/WER.
