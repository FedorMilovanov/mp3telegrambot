# VoxCPM2 quality research sweep — 2026-07-26

This note records a focused review of more than 50 primary pages: official VoxCPM2 documentation, current source files, model configuration, release notes, upstream GitHub issues, PyTorch CPU guidance and FFmpeg filter documentation.

The purpose is not to collect generic advice. Every conclusion below is tied to an observed local failure or a concrete next experiment.

## Current local baseline

```text
voxcpm:        2.0.3
PyTorch:       2.13.0+cpu
Device:        CPU only
CUDA visible:  False
Threads:       10
Model:         local openbmb/VoxCPM2 snapshot
Current test:  MacArthur V3.2 NoChew
```

The installed `voxcpm==2.0.3` is still the latest official VoxCPM package release as of this review. Do not replace it merely because `main` contains newer code paths. First compare the installed API signature with current source and port only proven fixes.

## Highest-value findings

### 1. Keep `min_len` at the natural default unless a segment is demonstrably incomplete

Current official source defines `min_len=2`. A high `min_len` does not mean “protect the last consonant”; it prevents the stop head from ending generation. The local V3.1 setting of roughly 92–95% of the target window plausibly forced the model to continue after the sentence had naturally ended, producing pause-then-chewing tails.

Decision:

```text
first attempt: min_len=2
retry only:    modest min_len, based on an actually too-short candidate
never:         derive min_len from 90%+ of a video timing window
```

### 2. Never stretch short model output merely to fill a subtitle window

FFmpeg `atempo < 1` slows everything: speech, silence, breaths and model artifacts. It can make a weak tail sound much worse.

Decision:

```text
short candidate: preserve natural speed and pad silence
long candidate:  accelerate only within a guarded range
```

This is now the core V3.2 rule.

### 3. `retry_badcase` is length-based, not semantic or acoustic QA

Current source caps generation using text-token length and `retry_badcase_ratio_threshold`; it does not detect:

- repeated final syllables;
- pause followed by a weak restart;
- clipped final consonants;
- wrong words;
- speaker drift;
- room/echo transfer.

Decision: retain custom endpoint detection and later add ASR/text-completeness checks. Do not assume `retry_badcase=True` means a segment is publication-safe.

### 4. Lower CFG is the next high-value A/B axis

Official guidance describes `2.0` as balanced and warns that `2.0–3.0` increases artifact risk on difficult inputs. For noisy, buzzy or unstable long-form output, the official guide specifically suggests trying roughly `1.5–1.6`.

The local tests have concentrated around `2.0–2.1`. That has not yet been properly challenged.

Next controlled sweep:

```text
CFG:   1.55 / 1.75 / 1.95
Steps: 10
Mode:  reference-only
Ref:   B extended
Text:  one ending-sensitive 7–10 second Russian segment
```

Only after choosing CFG:

```text
Steps: 10 / 16
```

More diffusion steps can improve detail and naturalness, but they are not expected to fix stop-head logic by themselves.

### 5. B remains the best production reference, but cleaner matters more than longer

Official practical reference range is 5–30 seconds. B uses 24 seconds and is therefore already near the upper end of the recommended range.

Adding more of the same hall recording may improve timbre identity while also strengthening room coloration. The next major improvement in similarity is more likely to come from a cleaner 15–25 second close-mic MacArthur source than from extending the present Short beyond 24 seconds.

Reference acceptance target:

- one speaker;
- 15–25 seconds;
- close microphone;
- no applause/music;
- minimal room tail;
- no clipped start/end;
- no more than about one second of edge silence;
- representative calm and emphatic intonation.

### 6. Ultimate/combined cloning stays excluded for English-to-Russian production

Official architecture notes report a slight similarity improvement from combined mode, but upstream cross-language reports describe source-language accent leakage in Ultimate mode. The local D sample was also the worst subjective result.

Decision:

```text
production default: reference-only
Ultimate:           isolated research only
```

### 7. Final-word cutoff is a real upstream failure mode

Upstream issue #213 reports random truncation of final syllables or consonants under default generation parameters. Therefore endpoint damage is not necessarily introduced by FFmpeg.

Decision: generate multiple candidates for ending-sensitive segments, then select using:

1. target-text ASR completeness;
2. endpoint energy continuity;
3. absence of pause-restart tail;
4. human listening.

### 8. Reference-tail leakage is version- and mode-sensitive

Issue #272 reports reference-tail chirps or fragments leaking into generated audio. Community observations conflict: adding reference-tail silence helped some Ultimate-mode runs, while another implementation found that blank padding could worsen the conditioning artifact.

Decision: do not universally append silence. Test these as separate reference variants:

```text
raw clean edge
+250 ms trailing silence
+500 ms trailing silence
```

Use the same text, CFG, steps and seed where the installed API supports seed.

### 9. Segmenting remains mandatory

Official guidance lists long text as a trigger for buzzing, speed drift, endless generation and KV-cache growth. Upstream issue #302 also describes voice drift during longer prompt/reference-conditioned generation.

Production target:

```text
normal segment: 6–12 sec
complex segment: up to ~15 sec
avoid:           isolated one-word segments and very long one-pass paragraphs
```

A four-block 48-second Short is a sensible upper-level structure, but an individual block still needs retries and endpoint QA.

### 10. Very short text can hallucinate

Upstream issue #357 documents extra trailing words/syllables for one-word Polish inputs. The official guide also notes that text naturally producing only about one second of speech is less stable.

Decision: do not split corrections into one-word or tiny standalone clips. Preserve at least one complete clause and several seconds of natural speech.

### 11. Repeated generation needs memory monitoring

Upstream reports describe memory growth or failure on subsequent runs. Even on CPU, the future bot worker should record process RSS before and after every segment and recycle the worker when growth exceeds a threshold.

Decision for future integration:

- one active synthesis job;
- load model once per job;
- release candidate arrays immediately;
- `gc.collect()` after each segment;
- checkpoint after each completed segment;
- restart worker between long jobs or on sustained RSS growth.

### 12. Objective quality reports are required

Upstream feature discussion proposes speaker similarity, speaking rate, pitch, style consistency and word error rate. Those are the right categories for this project.

Minimum report per candidate:

```text
source/reference ID
model snapshot
mode / CFG / steps / min_len / max_len
seed if supported
raw duration
leading/trailing silence
pause-restart detector result
clipping and peak level
speaking rate
ASR transcript and text coverage
speaker similarity when an embedding model is available
human notes
```

## Recommended quality ladder after V3.2

Do not rerender the whole Short for every parameter change.

### Gate A — endpoint probe

Use one Russian phrase that ends with several consonants and a definitive period.

Generate:

```text
B reference, CFG 1.55, steps 10
B reference, CFG 1.75, steps 10
B reference, CFG 1.95, steps 10
```

Reject candidates with:

- missing final word/syllable;
- post-silence restart;
- invented trailing material;
- strong metallic/room artifact.

### Gate B — diffusion detail

Using the winning CFG:

```text
steps 10
steps 16
```

Only choose 16 if the audible improvement justifies the CPU cost.

### Gate C — reference edge treatment

Using the winning CFG and steps:

```text
B raw edge
B + 250 ms trailing silence
B + 500 ms trailing silence
```

Do not alter denoise, EQ or punctuation in the same sweep.

### Gate D — cadence

Compare:

```text
B for all four blocks
B for blocks 1–3 + C for final block
```

This determines whether C's improved final cadence is worth any timbre discontinuity.

### Gate E — clean external reference

Only after the pipeline is stable, obtain a cleaner MacArthur source and compare it against B. This is the experiment most likely to improve both similarity and echo simultaneously.

## Things explicitly not to do

- Do not increase `min_len` to fill a video window.
- Do not slow short output to match timing.
- Do not increase CFG above 2 merely to “force” pronunciation.
- Do not expect more diffusion steps to solve semantic omissions.
- Do not enable denoising and change reference/CFG simultaneously.
- Do not use Ultimate as the default cross-language mode.
- Do not split into single-word repair clips.
- Do not accept a segment only because its duration looks plausible.
- Do not hide incomplete speech under the English background track.
- Do not enable CUDA on the current defective RTX 3060.

## Primary source groups reviewed

### Official documentation and model/source pages

- VoxCPM Quick Start
- Installation
- Usage Guide
- VoxCPM2 architecture/model guide
- API reference
- FAQ/troubleshooting
- Hugging Face model card
- Hugging Face model config
- PyPI 2.0.3/release metadata
- GitHub release notes
- `core.py`
- `model/voxcpm2.py`
- MiniCPM4 cache implementation
- MiniCPM4 model/cache setup
- model utilities
- text normalization

### High-value upstream issues

- #52 KV-cache/OOM and long-text generation
- #136 unintended singing behavior
- #202 LoRA output/data preparation
- #209 extended batch hang/state accumulation
- #213 final-word cutoff
- #219 speech-to-speech expectations
- #248 non-CUDA platform failure
- #256 CLI/version skew
- #271 fine-tuning and reference preparation
- #272 reference-tail chirp/click
- #285 accent control
- #293 attention performance
- #296 prefill/reference failure
- #302 long-form speaker drift
- #306 cross-language training strategy
- #316 alternative backends
- #321 Ultimate cross-language accent leakage
- #323 batch generation requirements
- #335 training-memory pressure
- #338 parallelism/thread contention
- #342 objective quality evaluation
- #344 repeated-run memory growth
- #357 short-utterance hallucination
- #359 prompt semantics
- #360 instruction text leaking into speech

### Runtime and media references

- PyTorch inference mode
- intra-op/inter-op CPU threading
- CPU oversubscription guidance
- `torch.compile` compatibility
- FFmpeg `atempo`
- FFmpeg `adelay`
- FFmpeg `amix`
- FFmpeg `loudnorm`
- FFmpeg `sidechaincompress`
- FFmpeg `apad`
- FFmpeg `atrim`
- Hugging Face offline/cache variables

## Current conclusion

The dominant remaining quality problem is not “too few model steps.” It is candidate selection and endpoint control around a stochastic stop head. The correct production architecture is therefore:

```text
multiple short candidates
        -> semantic/ASR completeness
        -> endpoint and tail-artifact checks
        -> speaker/prosody scoring
        -> choose best candidate
        -> moderate timing fit without slowdown
        -> exact timeline assembly
```

V3.2 tests the first half of this architecture. The next improvement should be a compact candidate sweep and objective scorer, not another blind full-video rerender.
