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
8. **Upstream chunking alone did not solve duration instability.** A later sampled `fixed` CPU run split 682 normalized characters into eight chunks and returned 1048 frames / 85.52 seconds for a 46.277-second source. Upstream inter-chunk pauses are only short fractions of a second, so the excess duration was generated inside individual chunks. Alternative-model diagnostics must synthesize and retain each semantic phrase separately, validate each phrase against its authored slot, and reject only the offending phrase instead of losing the whole monologue.
9. **ONNX `fixed` mode ignores runtime sampling overrides.** The fixed sampled-frame graph bakes in text/audio temperature, top-p, top-k and repetition penalty at export time. V3/V4 passed custom CLI values that did not actually change generation. Use `sample-mode=full` for runtime tuning or deliberately re-export the ONNX graph.
10. **V4 confirmed text-dependent EOS failure.** Block 1 ended naturally at `80/102` frames; block 2 reached exactly `90/90` and expanded to 7.10 seconds for a 4.20-second slot. A frame count equal to the configured maximum is a cap hit and must fail even if post-trim audio exists.
11. **Short-text repetition and punctuation loss are known Nano ONNX defects.** Merge very short clauses into complete thoughts, end every TTS input with simple punctuation, avoid curly quotes/em dashes in the synthesis text, and keep chunks near 40–60 text tokens. Permit at most one targeted retry for the failed block, not a broad 5–10-candidate search.
12. **Reference audio needs an explicit A/B policy.** Use a clean, clear, stable reference with minimal processing. Test a short 3–4 second reference against a longer reference before assuming more audio improves similarity. Aggressive denoise and dynamic normalization can alter timbre. A rolling prompt from the previous generated tail is experimental continuity conditioning and must not silently replace the original-speaker identity anchor.
13. **Frame-cap checks must be per hidden text chunk, never aggregate.** The standard `infer_onnx.py` log reports total frames across every internal voice-clone chunk, while `max_new_frames` applies separately to each chunk. V5 incorrectly rejected totals such as `268/191` and `231/191`; read `result["chunk_results"]` and compare each chunk independently.
14. **Windows redirected stdout can fail after successful synthesis.** V6 generated WAV and wrote its UTF-8 JSON report, then Python 3.13 raised `UnicodeEncodeError` while printing Russian JSON through a `cp1252` redirected console. Force `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`, write full reports to UTF-8 files, and keep machine-readable stdout ASCII-safe.
15. **V7 proved that passing duration checks is not enough.** Every accepted semantic block was internally split into two independent voice-clone chunks, six blocks used separate processes/seeds, and the final mix omitted the original source audio entirely. The result changed speaker identity/accent across the monologue; block 6 also accepted only 3.93 seconds for a 6.84-second slot and padded the remainder. For identity-sensitive dubbing, use one persistent runtime/RNG, one direct single-chunk call per authored block, a longer minimally processed identity reference, and explicitly mix the original speaker at the operator-specified 18% level with the Russian track delayed by the configured lead-in.
16. **MOSS Nano is the natural-speech stage, not the final identity stage for MacArthur.** Repeated zero-shot tests kept natural Russian delivery but did not preserve John MacArthur’s identity or a stable accent. The CPU-only production experiment should therefore use a whole-track hybrid: MOSS creates the aligned Russian master, then Seed-VC V2 converts the entire master once against a single 20–22 second MacArthur reference. Run Seed-VC in an isolated Python 3.11/3.12 environment, force CPU/float32 because the upstream V2 CLI hard-codes float16, prefer one deterministic style-conversion pass with high intelligibility/similarity CFG, validate duration and speaker-embedding similarity, and only then mix the original MacArthur at 18% with the Russian track delayed by 520 ms. Do not train MOSS Nano on CPU as a quick fix; the official fine-tuning baseline is GPU-oriented.

## Fast comparison protocol

For quick perceptual comparison before bot integration:

- use **MOSS-TTS-Nano ONNX on CPU**, not the 8B dialogue model and not CUDA;
- force `execution-provider=cpu` and `CUDA_VISIBLE_DEVICES=-1`;
- for identity-sensitive tests, use a minimally processed 7–10 second original-speaker reference; shorter references remain an A/B diagnostic, not the default;
- keep one persistent runtime and RNG across the monologue; do not launch a new sampled process/seed for every block;
- call `synthesize_single_chunk` directly for each authored block when internal splitting changes voice identity;
- merge ultra-short phrases into complete semantic blocks and retain each block as a separate artifact;
- use `sample-mode=full` when changing sampling parameters; do not assume `fixed` accepted the CLI overrides;
- keep synthesis text around 40–60 tokens, use simple terminal punctuation, and keep decorative quotation marks only in subtitles;
- generate one primary candidate and at most one targeted retry for a failed block;
- give generation a frame budget larger than the authored slot, but fail closed only when the relevant single chunk reaches the cap;
- reject implausibly short accepted speech as well as runaway speech; do not hide missing or rushed content with long `apad` tails;
- final mix QA must verify that the original source track is actually present at the requested level and that the Russian lead-in delay was applied;
- for the MacArthur identity path, treat MOSS as the Russian naturalness/alignment source and apply one whole-track Seed-VC V2 conversion afterward; do not convert independent blocks with separate target embeddings;
- run Seed-VC in its own Python 3.11/3.12 venv, force `CUDA_VISIBLE_DEVICES=-1`, use `torch.float32` on CPU, and never install its archived dependency stack into the bot or MOSS environment;
- use full decode for diagnostics, trim only edge silence, and validate duration/repetition before muxing;
- evaluate voice similarity, pronunciation, noise, continuity, duration, and endings before adding any backend to the bot.

This protocol is an experiment, not permission to make MOSS the production default.

## Regression and artifact hygiene

- Add deterministic tests for translation preflight, equivalent-graph final QA, and monologue continuity before changing production behavior.
- Do not commit generated WAV/MP4 files, downloaded model weights, project directories, checkpoints, or raw runtime logs.
- Keep confirmed rules here concise; do not create one document per failed render.
