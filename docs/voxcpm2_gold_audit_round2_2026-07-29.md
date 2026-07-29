# VoxCPM2 / Dub Studio — gold audit, round 2

Date: 2026-07-29

This round was performed against the live `main` branch while another agent was
also committing. Every code change used the current file SHA and was committed
atomically; already-fixed work was not replayed.

## Confirmed defects from this round

1. **Russian tail could be cut before mastering.** The mixed PCM used
   `amix=duration=first`, so the mix ended with the source audio stream rather
   than the authored Russian timeline. Later `apad` could only add silence. The
   master now resets both input PTS, uses `duration=longest`, pads, and trims to
   the exact source-video duration.
2. **Missing timbre evidence was treated as a perfect match.** Empty or invalid
   spectral profiles returned similarity `1.0`. They now return `0.0`, so the
   hard timbre gate fails closed.
3. **Missing F0/reference evidence was treated as a normal ratio.** Final voice
   QA silently substituted ratio `1.0` when a requested reference profile or
   pitch estimate was absent. QA policy v4 now requires a real requested
   reference profile plus valid reference and candidate F0 evidence.
4. **Timeline limiter defect was found concurrently.** The parallel agent fixed
   `alimiter` auto-level and latency compensation before this auditor wrote the
   same file, so that change was deliberately not overwritten.

## Decisions retained

- Keep short synthesis segments and independent per-segment retries.
- Keep `condition_on_previous_text=False` for isolated QA clips.
- Keep final AAC/MP4 verification after encoding, not only PCM checks.
- Keep semantic ASR, timing/continuity, F0 and timbre as separate signals.
- Do not let one metric rescue a confidently foreign or acoustically broken clip.
- Do not enable aggressive reference denoising unconditionally; clean-source
  identity can be damaged by spectral rewriting.
- Treat upstream issues as reproducibility signals, not as authoritative tuning
  instructions. Production changes require source-code, official documentation,
  a standard, or a local A/B result.

## 60-source primary matrix

### VoxCPM2 — official code, model, reports and upstream defect reports

1. https://github.com/OpenBMB/VoxCPM — official repository and usage contract.
2. https://github.com/OpenBMB/VoxCPM/blob/main/app.py — official UI/API argument wiring.
3. https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/model/voxcpm2.py — generation and prompt-cache implementation.
4. https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/core.py — public model API.
5. https://huggingface.co/openbmb/VoxCPM2 — official model card and recommended generation arguments.
6. https://huggingface.co/openbmb/VoxCPM2/tree/main — official model repository tree.
7. https://huggingface.co/openbmb/VoxCPM2/blame/main/README.md — model-card history and argument defaults.
8. https://arxiv.org/abs/2606.06928 — VoxCPM2 technical report.
9. https://arxiv.org/abs/2509.24650 — original VoxCPM technical report.
10. https://github.com/OpenBMB/VoxCPM/issues/272 — upstream chirp/click and cut-ending report.
11. https://github.com/OpenBMB/VoxCPM/issues/302 — upstream long-form voice drift analysis.
12. https://github.com/OpenBMB/VoxCPM/issues — current upstream issue index.
13. https://github.com/OpenBMB/VoxCPM/issues/202 — upstream garbage-output investigation.
14. https://github.com/OpenBMB/VoxCPM/issues/271 — reference/segment preparation discussion.
15. https://github.com/OpenBMB/VoxCPM/issues/238 — official project licensing clarification thread.
16. https://huggingface.co/openbmb/VoxCPM2/discussions/3 — model-output speed discussion.
17. https://huggingface.co/openbmb/VoxCPM2/discussions/4 — model numeric precision discussion.

### FFmpeg — official documentation and implementation

18. https://ffmpeg.org/ffmpeg-filters.html — filter contracts (`amix`, `alimiter`, `apad`, `atrim`, `adelay`, `loudnorm`).
19. https://ffmpeg.org/ffmpeg-formats.html — MP4/MOV muxing and `faststart`.
20. https://ffmpeg.org/ffprobe.html — machine-readable stream/container probing.
21. https://www.ffmpeg.org/ffprobe-all.html — full ffprobe option reference.
22. https://ffmpeg.org/doxygen/trunk/af__amix_8c_source.html — `amix` duration-mode implementation.
23. https://ffmpeg.org/doxygen/trunk/af__amix_8c.html — `amix` option definitions.
24. https://ffmpeg.org/doxygen/trunk/af__alimiter_8c.html — limiter implementation and options.
25. https://ffmpeg.org/doxygen/trunk/af__loudnorm_8c_source.html — loudness-normalizer implementation.
26. https://ffmpeg.org/doxygen/trunk/af__loudnorm_8c.html — loudnorm filter structure/options.
27. https://ffmpeg.org/doxygen/trunk/af__adelay_8c.html — channel-delay implementation.
28. https://www.ffmpeg.org/doxygen/trunk/af__adelay_8c.html — alternate official generated reference.
29. https://ffmpeg.org/doxygen/8.0/af__amix_8c_source.html — released FFmpeg 8.0 `amix` implementation.
30. https://ffmpeg.org/doxygen/7.0/af__amix_8c_source.html — released FFmpeg 7.0 `amix` implementation.
31. https://ffmpeg.org/doxygen/4.1/af__amix_8c_source.html — historical duration contract check.
32. https://ffmpeg.org/pipermail/ffmpeg-devel/2012-September/130702.html — origin of `ffprobe -show_entries`.
33. https://ffmpeg.org/pipermail/ffmpeg-user/2022-December/055824.html — timestamp/start-time semantics discussion.
34. https://github.com/FFmpeg/FFmpeg — official Git mirror for source-level verification.

### Whisper, faster-whisper and speech activity detection

35. https://github.com/SYSTRAN/faster-whisper — official faster-whisper repository.
36. https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py — decoding, language detection and VAD argument behavior.
37. https://github.com/SYSTRAN/faster-whisper/issues/869 — language-misdetection example and maintainer guidance.
38. https://github.com/SYSTRAN/faster-whisper/issues/1261 — VAD truncation report and maintainer response.
39. https://github.com/openai/whisper — official Whisper repository.
40. https://github.com/openai/whisper/blob/main/whisper/transcribe.py — official transcription failure-loop/timestamp controls.
41. https://arxiv.org/abs/2212.04356 — Whisper paper.
42. https://github.com/snakers4/silero-vad — official Silero VAD repository.
43. https://github.com/snakers4/silero-vad/discussions/217 — official-project VAD windowing discussion.

### Speaker identity, semantic fidelity and perceptual quality metrics

44. https://arxiv.org/abs/2005.07143 — ECAPA-TDNN speaker verification.
45. https://arxiv.org/abs/2110.05777 — self-supervised representations for speaker verification.
46. https://arxiv.org/abs/2503.10446 — Whisper Speaker Identification.
47. https://arxiv.org/abs/2407.10048 — Whisper-SV speaker verification.
48. https://arxiv.org/abs/2206.12285 — NORESQA-MOS and metric generalization limits.
49. https://arxiv.org/abs/2110.01763 — DNSMOS P.835.
50. https://arxiv.org/abs/2010.15258 — DNSMOS.
51. https://arxiv.org/abs/2104.09494 — NISQA multidimensional quality prediction.
52. https://arxiv.org/abs/2401.16812 — SpeechBERTScore for speech-generation evaluation.
53. https://arxiv.org/abs/2110.13900 — WavLM speech representation model.

### Loudness, true peak and subjective listening standards

54. https://www.itu.int/rec/R-REC-BS.1770-5-202311-I — ITU-R BS.1770-5 loudness and true-peak algorithm.
55. https://www.itu.int/dms_pubrec/itu-r/rec/bs/R-REC-BS.1770-5-202311-I%21%21TOC-HTM-E.htm — BS.1770-5 structure and true-peak annex.
56. https://tech.ebu.ch/loudness/ — EBU R128 family overview and compliance test material.
57. https://tech.ebu.ch/publications/tech3342 — EBU Tech 3342 loudness range.
58. https://tech.ebu.ch/publications/tech3343 — EBU Tech 3343 production guidance.
59. https://tech.ebu.ch/publications/tech3344 — EBU Tech 3344 distribution/reproduction guidance.
60. https://www.itu.int/rec/T-REC-P.808-202106-I/en — ITU-T P.808 subjective crowdsourced speech-quality evaluation.

## Practical gold extracted

- Exact stream-end behavior must be tested at the filter that owns it; checking
  only the final container cannot recover speech already removed upstream.
- Unknown/missing similarity evidence must be neutral only in exploratory
  ranking, never in a release hard gate. Release gates must fail closed and say
  which evidence is missing.
- Short isolated QA clips should not condition on previous ASR text; it can create
  repetition loops and timestamp drift.
- VAD is not universally safe for music/noisy speech and must not be enabled as
  an unconditional semantic-QA preprocessor.
- Objective MOS predictors are useful diagnostics but cannot replace semantic
  transcription, speaker evidence, true-peak/loudness checks, and human A/B.
- The official VoxCPM2 path supports prompt audio plus exact prompt transcript for
  stronger cloning. Cross-language use remains a local A/B decision because an
  English prompt transcript can also increase continuation leakage when segment
  boundaries or reference tails are poor.
