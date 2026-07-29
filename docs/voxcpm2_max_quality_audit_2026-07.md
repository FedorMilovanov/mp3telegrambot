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
23. **Do not denoise an already clean reference:** official docs warn it can change voice characteristics. — **Implemented**.
24. **Do not force trailing silence padding:** upstream reports conflict, and reference-only mode has no official recommendation for it. — **Rejected**.
25. **Reuse the exact reference for consistent identity across segments.** — **Implemented**.
26. **Reference fingerprint in every checkpoint.** — **Implemented**.
27. **Model fingerprint in every checkpoint.** — **Implemented**.
28. **Hi-Fi continuation mode for this bot:** potentially higher similarity, but exact transcript/prompt continuation previously leaked non-Russian material. — **Rejected for standard production**.
29. **LoRA per speaker:** possibly useful for large repeated workloads, but unnecessary before direct reference-only quality is benchmarked. — **Deferred**.

## C. Text, translation and segmentation

30. **Russian is officially supported without a language tag.** — **Implemented**.
31. **Short generation units instead of one long call:** prevents drift, buzzing, speed-up and runaway KV growth. — **Implemented**.
32. **Target window around 4.2 seconds, maximum around 5.4 seconds.** — **Implemented**.
33. **Very short/one-word windows are merged where timing permits:** official docs note weak stability below roughly one second. — **Implemented**.
34. **Punctuation used as prosody information, not stripped.** — **Implemented**.
35. **Questions, periods, commas and ellipses retain distinct pause intent.** — **Implemented**.
36. **Translation reads neighboring IDs as one speech, not isolated cards.** — **Implemented**.
37. **Three editorial passes:** draft, fidelity, spoken-performance. — **Implemented**.
38. **Preserve repetitions, anaphora, contrasts, questions and climax.** — **Implemented**.
39. **Only overloaded lines are compressed.** — **Implemented**.
40. **Do not insert actor notes or non-verbal tags automatically.** — **Implemented**; upstream tags are creative and can be unpredictable.
41. **Text normalization only when digits/dates/symbols require it.** — **Implemented**.
42. **No phoneme mode by default:** regular Russian text is the safe production path. — **Implemented**.

## D. Direct generation and candidate selection

43. **One stable CLI shared by PowerShell and bot.** — **Implemented**.
44. **No renderer wrapper chain.** — **Implemented**.
45. **Two deterministic candidates for every segment.** — **Implemented**.
46. **Third candidate only when existing candidates show a warning.** — **Implemented**.
47. **Deterministic seed per segment and attempt.** — **Implemented**.
48. **Quality profiles across attempts:** balanced, stronger adherence/detail, then lower-CFG stability fallback. — **Implemented**.
49. **CFG remains within the official 1–3 practical range.** — **Implemented**.
50. **Diffusion steps remain within the official 4–30 range.** — **Implemented**.
51. **Official `retry_badcase=True` when supported by installed package.** — **Implemented**.
52. **Retry parameters passed only when the installed API exposes them.** — **Implemented**.
53. **Generation has stop-token headroom so Russian final words are not clipped by an artificial short `max_len`.** — **Implemented**, with a strict 512-token short-form ceiling.
54. **No artificial slow-down of short outputs.** — **Implemented**.
55. **Maximum timing acceleration lowered from 1.50 to 1.35.** — **Implemented**.
56. **Candidate scoring includes duration, clipping, edge silence and tail restart.** — **Implemented**.
57. **Candidate scoring includes active speech ratio and maximum internal gap.** — **Implemented**.
58. **Candidate scoring includes F0 median/p90 match to the real reference.** — **Implemented**.
59. **Extreme high-register or near-unvoiced candidates are hard-rejected before global QA.** — **Implemented**.
60. **48 kHz candidate pitch analysis uses a 16 kHz diagnostic copy only; selected audio remains native 48 kHz.** — **Implemented**.
61. **Full speaker-embedding similarity (ECAPA/other verifier): useful but model-dependent and not universally aligned with TTS perception.** — **Deferred pending an offline benchmark and dependency audit**.

## E. Independent QA, mastering and operations

62. **ASR semantic recall against every Russian target line.** — **Implemented**.
63. **Onset, trailing silence and start-artifact checks.** — **Implemented**.
64. **Internal-gap continuity check.** — **Implemented**.
65. **Voice-register comparison against reference.** — **Implemented**.
66. **Only failed IDs receive one direct retry with a new seed.** — **Implemented**.
67. **No dynamic rescue prompt or foreign transcript.** — **Implemented**.
68. **Full routes always start a fresh baseline.** — **Implemented**.
69. **Selective repair requires a successful clean expressive baseline.** — **Implemented**.
70. **Two-pass loudness normalization after the complete timeline, not per segment.** — **Implemented**.
71. **Speech-oriented master target:** -16 LUFS integrated. — **Implemented**.
72. **True-peak safety:** -1.5 dBTP before AAC. — **Implemented**.
73. **Original voice mixed at one constant 18%, no pumping sidechain.** — **Implemented**.
74. **Telegram progress uses one editable durable message per job.** — **Implemented**.
75. **Russian title casing is shared across Shorts, LiveDub and Dub Studio.** — **Implemented**.
76. **CI compiles direct renderer modules and runs synthetic pitch, candidate, max-length, title and progress contracts.** — **Implemented**.
77. **Real CPU synthesis, speaker likeness and emotional naturalness:** automated gates reduce obvious failures but do not replace listening. — **Local verification required**.
78. **Human A/B test:** compare Russian-only, mixed version, source, and selected references over the full emotional arc. — **Local verification required**.

## High-value decisions from the audit

- Keep **short synthesis calls with repeated reference injection**. Upstream documentation and the long-form drift issue both support this over one long generation.
- Keep **reference-only cloning** for the standard bot. It separates timbre from continuation context and avoids the exact prompt/transcript failure path previously observed.
- Keep **clean references without denoise**. Denoising is conditional, not a universal quality switch.
- Do **not** copy forum tricks into production without agreement between official guidance and real tests.
- Candidate selection must reject bad audio **before** expensive full-timeline QA; global QA remains the final independent gate.
- Speaker identity needs more than F0 in the long term, but a third-party embedding model must earn its place through a reproducible local A/B benchmark.

## Primary sources reviewed

### Official VoxCPM2

- Repository and current README: https://github.com/OpenBMB/VoxCPM
- Quick Start: https://voxcpm.readthedocs.io/en/latest/quickstart.html
- Usage Guide: https://voxcpm.readthedocs.io/en/latest/usage_guide.html
- Voice Chef Cookbook: https://voxcpm.readthedocs.io/en/latest/cookbook.html
- Architecture: https://voxcpm.readthedocs.io/en/latest/models/architecture.html
- Demo: https://openbmb.github.io/voxcpm2-demopage/
- Technical report: https://arxiv.org/abs/2606.06928
- Official app implementation: https://github.com/OpenBMB/VoxCPM/blob/main/app.py

### Upstream issues and practitioner evidence

- Long-form voice drift #302: https://github.com/OpenBMB/VoxCPM/issues/302
- Chirp/click and cut endings #272: https://github.com/OpenBMB/VoxCPM/issues/272
- Reference/data preparation #271: https://github.com/OpenBMB/VoxCPM/issues/271
- 16 kHz encoder / 48 kHz decoder correction #202: https://github.com/OpenBMB/VoxCPM/issues/202

### Research and engineering references

- VoxCPM2 Technical Report: https://arxiv.org/abs/2606.06928
- Simple and Effective Multi-sentence TTS with Expressive and Coherent Prosody: https://arxiv.org/abs/2206.14643
- Automatic Evaluation of Speaker Similarity: https://arxiv.org/abs/2207.00344
- An Exploration of ECAPA-TDNN and x-vector Speaker Representations in Zero-shot Multi-speaker TTS: https://arxiv.org/abs/2506.20190
- HW-TSC IWSLT 2026 Cross-Lingual Voice Cloning Track: https://aclanthology.org/2026.iwslt-1.11/
- EBU R128 loudness recommendation: https://tech.ebu.ch/publications/r128

## Scope note

A GitHub code audit and synthetic unit tests can validate routing, parameters,
fingerprints, signal-processing math and failure policy. Only the local Windows
VoxCPM2 environment can validate the actual installed package API, CPU runtime,
voice likeness and final listening quality. A completed render must therefore be
judged from the Russian-only track first, then the mixed version.
