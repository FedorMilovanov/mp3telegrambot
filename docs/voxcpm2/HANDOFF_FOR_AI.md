# VoxCPM2 handoff for another AI or developer

Use this file as the compact project context before changing or running the local VoxCPM2 pipeline.

## Owner goal

Create a reliable Russian dubbing pipeline for English Christian sermons and Shorts:

- preserve the original speaker's voice when appropriate;
- use a checked Russian translation;
- keep the English original quietly audible underneath;
- align Russian speech to exact source timings;
- perform ASR and semantic QA before publication;
- later integrate this path with `mp3telegrambot` LiveDub.

## Absolute hardware rule

The local NVIDIA RTX 3060 is confirmed hardware-defective for this workload.

Observed:

- repeated CUDA driver resets;
- `nvlddmkm` Event ID 153;
- LiveKernelEvent / WATCHDOG;
- CUBLAS failures;
- illegal memory access;
- BF16/FP16 failures;
- driver replacement did not fix it;
- reball was already performed.

Therefore:

```text
DO NOT RUN VOXCPM2 WITH CUDA ON THIS MACHINE.
```

Every process must set before importing torch:

```python
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
```

Expected log:

```text
CUDA available: False
```

Do not suggest another CUDA retry until the GPU is replaced.

## Local environment

```text
Python: 3.11.9
voxcpm: 2.0.3
PyTorch: 2.13.0+cpu
CPU venv: C:\AI-Archive\VoxCPM2-CPU-TEST\.venv
Model archive: C:\AI-Archive\VoxCPM2-paused-RTX3060
```

Known model snapshot:

```text
C:\AI-Archive\VoxCPM2-paused-RTX3060\models\voxcpm2-model-cache\models--openbmb--VoxCPM2\snapshots\bffb3df5a29440629464e5e839f4d214c8714c3d
```

## Confirmed CPU benchmark

```text
Output duration: 10.72 sec
Synthesis: 101.37 sec
RTF: 9.46
Estimated 42:23 sermon: 6.68 hours
```

CPU synthesis is slow but usable and stable.

## UX rule for assisting the owner

Prefer one complete PowerShell command block or a downloadable ZIP.

Do not instruct the owner to:

- paste Python directly into PowerShell;
- manually maintain Python indentation;
- perform several contradictory edits such as “replace this, then a better version, then another version”.

When changing source files, use PowerShell automation, backups, syntax checks, and a single restart command.

## Known failures

### Windows console encoding

Symptom:

```text
UnicodeEncodeError: 'charmap' codec can't encode characters
```

Fix stdout/stderr UTF-8 and set `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`.

### Unsupported `seed`

The installed 2.0.3 call path rejected `seed`. Check the installed signature instead of assuming the latest `main` API.

### Cache 512 vs prompt 550

Symptom:

```text
The expanded size of the tensor (512) must match the existing size (550)
```

Cause: prompt/reference prefill exceeded the locally configured cache.

### `KV cache is full`

Cause: same cache limit during autoregressive generation.

Working patch after loading the model:

```python
cache_length = 2048  # segmented Shorts default
cache_dtype = next(model.tts_model.parameters()).dtype
cache_device = model.tts_model.device

model.tts_model.base_lm.setup_cache(
    1, cache_length, cache_device, cache_dtype
)
model.tts_model.residual_lm.setup_cache(
    1, cache_length, cache_device, cache_dtype
)
```

8192 was tested and worked, but 2048 is sufficient and cheaper for short segments.

### Premature stop / incomplete long generation

MacArthur one-pass test:

```text
Video: 48.69 sec
Generated Russian: 19.20 sec
Synthesis: 213.80 sec
RTF: 11.14
atempo: 0.394299
```

The launcher correctly rejected it. Do not stretch incomplete speech to the full video.

## Required architecture

Generate short timed segments, not one long target.

For each segment:

1. provide the same clean reference;
2. calculate model step duration;
3. set `min_len` near 88% of target duration in steps;
4. set `max_len` near 135%;
5. generate raw WAV;
6. fit moderately with FFmpeg `atempo`;
7. pad/trim to exact window;
8. place with `adelay`;
9. combine and loudness-normalize once.

This is also the long-sermon architecture.

## Clone-mode policy

For English reference -> Russian target:

```text
Default: reference-only
```

```python
reference_wav_path=reference
```

Reason: it clones timbre without requiring the English transcript and usually carries less English articulation into Russian.

A/B alternative after a successful default run:

```text
combined / ultimate
```

```python
reference_wav_path=reference
prompt_wav_path=reference
prompt_text=exact_transcript
```

It may increase similarity but may also preserve the source-language accent more strongly.

## Reference policy

Use one speaker, 5–12 seconds, complete phrases, no music, no applause, no clipping, minimal room sound.

Safe initial filter:

```text
highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2
```

Do not use aggressive `afftdn` by default. The first test produced good diction but a noisy, reverberant “garage” character.

## Current MacArthur test

Source:

```text
https://youtube.com/shorts/RAaSAbPj-iw
```

Use seven source-aligned segments. The reference-only V2 script is stored under `tools/voxcpm2/`.

## Integration direction

Do not import torch during normal Telegram bot startup.

Use a lazy, one-job-at-a-time service:

```text
services/voxcpm2_runtime.py
services/voxcpm2_reference.py
services/voxcpm2_segmenter.py
services/voxcpm2_synth.py
services/voxcpm2_timeline.py
services/voxcpm2_qa.py
pipelines/livedub_voxcpm2.py
```

Reuse existing project components:

- `services/livedub_mix.py` for sidechain and loudness policy;
- `services/livedub_qa.py` and long QA for semantic checking;
- faster-whisper transcript assets;
- existing stop/cancellation and progress mechanisms.

## Before claiming success

Verify all of the following:

- `CUDA available: False`;
- every expected segment exists;
- each segment has a JSON record;
- final audio duration matches source;
- no extreme atempo correction is hidden;
- Russian ASR contains the full translated content;
- theological meaning and Scripture references are checked;
- first/last words are not clipped;
- English original remains audible at the intended level;
- output paths and logs are reported truthfully.

Full handbook: `docs/voxcpm2/README.md`.
