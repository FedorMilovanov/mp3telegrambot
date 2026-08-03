# VoxCPM2 / Dub Studio agent contract

Scope: `tools/voxcpm2/**`. Read this before changing direct dubbing, mastering, continuity, final-media QA, or alternative-model experiments.

## Hardware policy

- The operator-confirmed RTX 3060 is unreliable for this workflow. Do **not** auto-enable CUDA for recovery, comparison, or local model experiments.
- CPU is the default execution path unless the operator explicitly reverses this decision after a new hardware validation.
- Alternative-model comparisons must use an isolated environment and must not mutate the bot's Python environment.

## Confirmed production findings — 2026-08-03

Project: `dub-ba15009b7a`, ready-SRT direct mode.

1. **Translation preflight failed.** The supplied SRT split Romans 10:9 as `сердцем веришь: Бог воскресил...`; the required conjunction was missing. The phrase must read `...сердцем ... веровать/верить, что Бог воскресил Его из мёртвых...`. Grammar and scripture quotations must be checked before expensive synthesis.
2. **Speech continuity was audibly broken.** The rendered short contained five separate speech islands with long gaps, abrupt transitions from residual segment noise to digital zero, and terminal sentence prosody at technical block endings. This made one monologue sound like unrelated phrases.
3. **Noise and silence are different defects.** Remove hiss/room-noise from the generated speech without a hard gate. Use short fades and natural authored pauses; do not create a noticeable noise-to-zero step at every segment boundary.
4. **Technical SRT/semantic blocks must not dictate terminal prosody.** Consecutive blocks inside one argument should continue the same breath, energy, and intonational direction. A block boundary is not automatically a sentence ending.
5. **Final media QA produced a false rejection.** The direct master used only the Russian branch, but the post-AAC regression compared differently processed signals: the final branch had additional high-pass/gain processing while the control branch did not. With a cloned voice from the same speaker, waveform correlation can be misattributed to residual source dialogue.
6. **Source-leakage QA must compare equivalent signal graphs.** Prefer final AAC versus its actual pre-encode master, or apply the identical processing chain to the control branch. Do not infer English leakage only from regression against a same-speaker cloned Russian track.
7. **The first MOSS Nano CPU quick test hit the frame cap, not a normal stop.** Custom settings `greedy`, `voice_clone_max_text_tokens=512`, and `max_new_frames=850` produced two chunks and exactly `1700 = 2 × 850` frames. The 136.24-second WAV contained useful speech around 36.35–68.81 and 119.70–136.24 seconds, separated by very long digital-silence regions. The test wrapper then trimmed the first 46.277 seconds, hiding most generated speech. Do not trim/pad a runaway generation into a deliverable. Reject frame-cap hits and implausible duration/silence before muxing.

## Fast comparison protocol

For quick perceptual comparison before bot integration:

- use **MOSS-TTS-Nano ONNX on CPU**, not the 8B dialogue model and not CUDA;
- force `execution-provider=cpu` and `CUDA_VISIBLE_DEVICES=-1`;
- use one clean 7–12 second source-voice reference;
- generate one candidate per chunk, not 3–5 variants;
- prefer the model's sampled `fixed` mode over forced greedy for cross-language voice cloning;
- keep text chunks bounded (roughly the upstream default scale), use full decode for diagnostics, and cap frames per chunk;
- fail closed when every chunk reaches `max_new_frames`, output duration is implausible, or long digital silence dominates;
- evaluate voice similarity, pronunciation, noise, continuity, duration, and endings before adding any backend to the bot.

This protocol is an experiment, not permission to make MOSS the production default.

## Regression and artifact hygiene

- Add deterministic tests for translation preflight, equivalent-graph final QA, and monologue continuity before changing production behavior.
- Do not commit generated WAV/MP4 files, downloaded model weights, project directories, checkpoints, or raw runtime logs.
- Keep confirmed rules here concise; do not create one document per failed render.
