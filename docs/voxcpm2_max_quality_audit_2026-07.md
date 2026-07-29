# VoxCPM2 Direct Max-Quality Audit — July 2026

This audit checks the actual MP3Bot/Dub Studio production path against official
VoxCPM2 documentation, upstream source/issues, speech-synthesis research and
broadcast/audio engineering guidance. It is deliberately not a list of every
possible trick: each item is marked **implemented**, **rejected**, **deferred**,
or **local verification required**.

The active production principle is:

```text
Telegram bot or manual PowerShell
  -> the same stable CLI path
  -> one direct VoxCPM2 renderer
  -> independent semantic/acoustic QA
  -> one two-pass master
  -> post-AAC delivery QA on the actual MP4
```

No `runpy`, semantic-rescue installer, subprocess proxy or model monkeypatch is
part of the standard synthesis path.

## A. Model loading and audio path

1. **Current model family:** use VoxCPM2, not 1.x. — **Implemented**.
2. **Explicit CPU device:** `device="cpu"`. — **Implemented**.
3. **No accidental CUDA path:** `CUDA_VISIBLE_DEVICES=-1`. — **Implemented**.
4. **CPU stability over compile tricks:** `optimize=False`. — **Implemented**.
5. **Denoiser weights not loaded for clean references:** `load_denoiser=False`. — **Implemented**.
6. **Offline snapshot only:** Hugging Face/Transformers offline flags. — **Implemented**.
7. **Model directory validation:** require config plus weights. — **Implemented**.
8. **Model identity report:** save model path and `config.json` SHA-256. — **Implemented**.
9. **Package identity report:** log installed `voxcpm` version. — **Implemented**.
10. **Native AudioVAE contract:** fail unless encode is 16 kHz and decode is 48 kHz. — **Implemented**.
11. **No external fake upsampler:** use native 48 kHz decoder output. — **Implemented**.
12. **Lossless intermediate output:** WAV PCM 24-bit. — **Implemented**.
13. **Timeline and master remain 48 kHz.** — **Implemented**.
14. **Final MP4 audio:** AAC 320 kbps after the lossless master. — **Implemented**.

## B. Voice reference quality

15. **Practical reference duration:** official 5–30 second range. — **Implemented** with focused 8–9 second references.
16. **At least five seconds of usable speech.** — **Implemented**.
17. **Calm identity reference separated from expressive delivery reference.** — **Implemented**.
18. **Source windows selected from the real speaker, not generated audio.** — **Implemented**.
19. **Reject near-empty reference windows by voiced/activity ratios.** — **Implemented**.
20. **Reject long internal reference gaps.** — **Implemented**.
21. **Trim leading/trailing non-speech and add short fades.** — **Implemented**.
22. **Do not loudness-normalize the reference merely for cloning.** — **Implemented**.
23. **Do not denoise an already clean reference:** filtering can change vocal colour. — **Implemented**.
24. **Do not force trailing silence padding:** upstream reports conflict, and reference-only mode has no official recommendation for it. — **Rejected**.
25. **Reuse the exact reference for consistent identity across segments.** — **Implemented**.
26. **Reference fingerprint in every checkpoint.** — **Implemented**.
27. **Model fingerprint in every checkpoint.** — **Implemented**.
28. **Hi-Fi/Ultimate continuation for the standard bot:** potentially stronger continuation, but exact prompt/transcript previously leaked non-Russian material. — **Rejected for standard production**.
29. **LoRA per speaker:** potentially useful for a large repeated catalogue, but unnecessary before direct reference-only quality is benchmarked. — **Deferred**.

## C. Text, translation and segmentation

30. **Russian is supported without a language tag.** — **Implemented**.
31. **Short generation units instead of one long call:** prevents long-form drift and runaway context. — **Implemented**.
32. **Target window around 4.2 seconds, maximum around 5.4 seconds.** — **Implemented**.
33. **Very short/one-word windows are merged where timing permits.** — **Implemented**.
34. **Punctuation used as prosody information, not stripped.** — **Implemented**.
35. **Questions, periods, commas and ellipses retain distinct pause intent.** — **Implemented**.
36. **Translation reads neighbouring IDs as one speech, not isolated cards.** — **Implemented**.
37. **Three editorial passes:** draft, fidelity, spoken-performance. — **Implemented**.
38. **Preserve repetitions, anaphora, contrasts, questions and climax.** — **Implemented**.
39. **Only overloaded lines are compressed.** — **Implemented**.
40. **Do not insert actor notes or non-verbal tags automatically.** — **Implemented**.
41. **Text normalization only when digits/dates/symbols require it.** — **Implemented**.
42. **No phoneme mode by default:** regular Russian text is the safe production path. — **Implemented**.
43. **Creator/manual captions outrank automatic captions and Whisper.** — **Implemented**.
44. **Failed manual-caption download falls back to automatic, then Whisper.** — **Implemented**.
45. **Rolling VTT duplicate lines and non-speech cues are removed without losing meaningful text.** — **Implemented**.

## D. Direct generation and candidate selection

46. **One stable CLI shared by PowerShell and bot.** — **Implemented**.
47. **No renderer wrapper chain.** — **Implemented**.
48. **Two deterministic candidates for every segment.** — **Implemented**.
49. **Third candidate only when existing candidates show a warning.** — **Implemented**.
50. **Deterministic seed per segment and attempt.** — **Implemented**.
51. **Quality profiles across attempts:** balanced, stronger adherence/detail, then lower-CFG stability fallback. — **Implemented**.
52. **CFG remains within the documented practical range.** — **Implemented**.
53. **Diffusion steps remain within the documented range.** — **Implemented**.
54. **Official `retry_badcase=True` when supported by the installed package.** — **Implemented**.
55. **Retry parameters passed only when the installed API exposes them.** — **Implemented**.
56. **Generation has stop-token headroom so Russian final words are not clipped by an artificial short `max_len`.** — **Implemented**, with a strict short-form ceiling.
57. **No artificial slow-down of short outputs.** — **Implemented**.
58. **Maximum timing acceleration lowered from 1.50 to 1.35.** — **Implemented**.
59. **Candidate scoring includes duration, clipping, edge silence and tail restart.** — **Implemented**.
60. **Candidate scoring includes active speech ratio and maximum internal gap.** — **Implemented**.
61. **Candidate scoring includes F0 median/p90 match to the real reference.** — **Implemented**.
62. **Candidate scoring includes a conservative 18-band long-term spectral-envelope similarity.** — **Implemented as a soft tie-breaker**.
63. **Only gross spectral mismatch is a hard failure:** phonetic content also changes the spectrum. — **Implemented with a deliberately low floor**.
64. **Extreme high-register or near-unvoiced candidates are hard-rejected before global QA.** — **Implemented**.
65. **Best-of-bad fallback is forbidden:** if all candidates fail hard limits, stop with diagnostics. — **Implemented**.
66. **48 kHz candidate analysis uses diagnostic copies only; selected audio remains native 48 kHz.** — **Implemented**.
67. **Heavy ECAPA/WavLM speaker verifier:** useful but model-dependent and not universally aligned with TTS perception. — **Deferred pending an offline A/B benchmark**.

## E. Independent QA, mastering and operations

68. **ASR semantic recall against every Russian target line.** — **Implemented**.
69. **Onset, trailing silence and start-artifact checks.** — **Implemented**.
70. **Internal-gap continuity check.** — **Implemented**.
71. **Expression-aware voice-register comparison against reference.** — **Implemented**.
72. **Only failed IDs receive one direct retry with a new seed.** — **Implemented**.
73. **No dynamic rescue prompt or foreign transcript.** — **Implemented**.
74. **Full routes always start a fresh baseline.** — **Implemented**.
75. **Selective repair requires a successful clean expressive baseline.** — **Implemented**.
76. **Two-pass loudness normalization after the complete timeline, not per segment.** — **Implemented**.
77. **Speech-oriented master target:** -16 LUFS integrated. — **Implemented**.
78. **True-peak safety:** -1.5 dBTP before AAC. — **Implemented**.
79. **Explicit `aresample=48000` after loudnorm.** — **Implemented**.
80. **AAC does not inherit `-shortest` truncation risk:** final duration is explicitly tied to source duration with a padded audio tail. — **Implemented**.
81. **Measure the actual encoded MP4 again:** codec, 48 kHz, stereo, duration, integrated LUFS and true peak. — **Implemented**.
82. **Fail if final AAC exceeds -1.0 dBTP, deviates more than 0.9 LU, or differs by more than 0.10 s.** — **Implemented**.
83. **Save `final_media_verification.json`.** — **Implemented**.
84. **Original voice mixed at one constant 18%, no pumping sidechain.** — **Implemented**.
85. **Telegram progress uses one editable durable message per job.** — **Implemented**.
86. **Russian title casing is shared across Shorts, LiveDub and Dub Studio.** — **Implemented**.
87. **CI compiles direct renderer/master/QA modules and runs synthetic contracts.** — **Implemented**.
88. **One ASR is not treated as a perfect judge:** acoustic gates and human listening remain independent. — **Implemented as policy; second-ASR ensemble deferred**.
89. **Real CPU synthesis, likeness and emotional naturalness:** automated gates reduce obvious failures but do not replace listening. — **Local verification required**.
90. **Human A/B test:** compare Russian-only, mixed version, source and selected references across the full emotional arc. — **Local verification required**.

## High-value decisions from the audit

- Keep **short synthesis calls with repeated real-reference injection**. Long-context research supports preserving discourse context, but one unlimited generation is not required to obtain it.
- Keep **reference-only cloning** for the standard bot. It avoids the exact continuation/prompt failure path previously observed.
- Keep **clean references without speculative denoise or silence padding**.
- Do **not** copy forum tricks into production without agreement between official guidance, code inspection and a reproducible local test.
- Candidate selection must reject bad audio **before** expensive full-timeline QA; global QA remains the independent gate.
- Speaker identity is multidimensional. F0 plus a conservative spectral envelope is useful; a heavy speaker embedding model must still earn its place in a local human-correlated benchmark.
- The PCM master is not the finished product. Delivery QA must measure the final AAC inside the MP4.
- WER/ASR, MOS predictors and speaker embeddings are complementary proxies, not substitutes for full-track listening.

## Source matrix reviewed (50 primary sources)

### Official VoxCPM2 and upstream implementation

1. OpenBMB VoxCPM repository — https://github.com/OpenBMB/VoxCPM
2. VoxCPM Quick Start — https://voxcpm.readthedocs.io/en/latest/quickstart.html
3. VoxCPM Usage Guide — https://voxcpm.readthedocs.io/en/latest/usage_guide.html
4. VoxCPM Voice Chef Cookbook — https://voxcpm.readthedocs.io/en/latest/cookbook.html
5. VoxCPM architecture documentation — https://voxcpm.readthedocs.io/en/latest/models/architecture.html
6. VoxCPM2 official demo — https://openbmb.github.io/voxcpm2-demopage/
7. VoxCPM2 Technical Report — https://arxiv.org/abs/2606.06928
8. VoxCPM2 Hugging Face model card — https://huggingface.co/openbmb/VoxCPM2
9. Official Gradio app and generation parameters — https://github.com/OpenBMB/VoxCPM/blob/main/app.py
10. Upstream long-form voice drift issue #302 — https://github.com/OpenBMB/VoxCPM/issues/302
11. Upstream chirp/click and cut-ending issue #272 — https://github.com/OpenBMB/VoxCPM/issues/272
12. Upstream reference/data preparation issue #271 — https://github.com/OpenBMB/VoxCPM/issues/271
13. Upstream encoder/decoder and preparation discussion #202 — https://github.com/OpenBMB/VoxCPM/issues/202

### Long-form, expressive and controllable speech research

14. Long-Context Speech Synthesis with Context-Aware Memory — https://arxiv.org/abs/2508.14713
15. Simple and Effective Multi-sentence TTS with Expressive and Coherent Prosody — https://arxiv.org/abs/2206.14643
16. StyleTTS — https://arxiv.org/abs/2205.15439
17. StyleTTS 2 — https://arxiv.org/abs/2306.07691
18. NaturalSpeech 2 — https://arxiv.org/abs/2304.09116
19. The time scale of redundancy between prosody and linguistic context — https://aclanthology.org/2025.acl-long.1471/
20. What Do Prosody and Text Convey? — https://aclanthology.org/2026.acl-long.1085/
21. CLAPSpeech — https://aclanthology.org/2023.acl-long.518/
22. Prosody-TTS — https://aclanthology.org/2023.findings-acl.508/
23. DiffStyleTTS — https://aclanthology.org/2025.coling-main.352/
24. MultiVerse — https://aclanthology.org/2024.findings-emnlp.533/
25. DisCo-Speech — https://aclanthology.org/2026.acl-long.863/
26. FC-TTS: Style and Timbre Control — https://aclanthology.org/2026.acl-long.173/
27. TED-TTS intra-utterance emotion/duration control — https://aclanthology.org/2026.acl-long.1077/
28. Comprehensive Benchmarking of Long-Form Speech Generation — https://aclanthology.org/2026.findings-acl.112/
29. Speech is More Than Words: prosody-aware speech translation — https://aclanthology.org/2024.wmt-1.119/
30. Towards Controllable Speech Synthesis in the Era of LLMs — https://aclanthology.org/2025.emnlp-main.40/

### Cross-lingual cloning and evaluation

31. IWSLT 2026 cross-lingual voice cloning overview — https://aclanthology.org/2026.iwslt-1.11/
32. Balancing Linguistic Intelligibility and Speaker Identity — https://aclanthology.org/2026.iwslt-1.12/
33. One Voice, Many Tongues — https://aclanthology.org/2026.iwslt-1.25/
34. Advancing Zero-shot TTS Intelligibility across Languages — https://aclanthology.org/2025.acl-long.598/
35. kNN Retrieval for Zero-Shot Multi-speaker TTS — https://aclanthology.org/2025.naacl-short.65/
36. Koel-TTS preference alignment and CFG — https://aclanthology.org/2025.emnlp-main.1076/
37. Evaluating Discrete Token-based Speech-LM TTS — https://aclanthology.org/2024.lrec-main.573/
38. TTS Evaluation Campaign with a Common Spanish Database — https://aclanthology.org/L10-1317/

### Objective quality, intelligibility and speaker identity

39. NISQA multidimensional speech quality — https://arxiv.org/abs/2104.09494
40. UTMOS / VoiceMOS Challenge 2022 — https://arxiv.org/abs/2204.02152
41. Automatic Evaluation of Speaker Similarity — https://arxiv.org/abs/2207.00344
42. SpeakerSleuth: limitations of audio-language judges — https://aclanthology.org/2026.acl-long.944/
43. Evaluating Low-Level Speech Features Against Human Perception — https://aclanthology.org/Q17-1030/
44. RT-VC evaluation with WER, UTMOS, similarity and F0 — https://aclanthology.org/2025.acl-demo.37/

### Loudness, true peak and final delivery

45. ITU-R BS.1770-5 loudness and true-peak algorithm — https://www.itu.int/rec/R-REC-BS.1770-5-202311-I
46. EBU loudness/R128 specification family — https://tech.ebu.ch/loudness/
47. EBU R128 recommendation — https://tech.ebu.ch/publications/r128
48. EBU Tech 3344 distribution and reproduction guidance — https://tech.ebu.ch/publications/tech3344
49. EBU loudness compliance test set — https://tech.ebu.ch/publications/ebu_loudness_test_set
50. FFmpeg `loudnorm` official filter documentation — https://ffmpeg.org/ffmpeg-filters.html#loudnorm

## Scope note

A GitHub code audit and synthetic unit tests can validate routing, parameters,
fingerprints, signal-processing math and failure policy. Only the local Windows
VoxCPM2 environment can validate the installed package API, CPU runtime, voice
likeness and final listening quality. A completed render must therefore be judged
from the Russian-only track first, then the mixed version.
