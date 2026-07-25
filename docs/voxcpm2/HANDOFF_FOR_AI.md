# VoxCPM2 handoff for another AI or developer

Read this file before changing, running or advising on the local VoxCPM2 pipeline.

## Owner goal

Create a reliable Russian dubbing pipeline for English Christian sermons and Shorts:

- preserve the original speaker's voice when appropriate;
- use a checked Russian translation;
- keep the English original audible at one constant reduced gain;
- align Russian speech to source timing without damaging natural delivery;
- perform ASR, theological and technical QA before publication;
- later integrate the accepted path with `mp3telegrambot` LiveDub.

## Current status

```text
Accepted baseline: V3.2 NoChew
Final candidate:   Steps 16 / CFG 1.80, currently under review
Production status: not yet approved for channel upload
```

Owner review of V3.2:

- substantially better than previous versions;
- no recurring swallowed words;
- phrase endings sounded clear;
- no obvious chewing artifact;
- complete 48.69-second Short exists.

Do not regress the V3.2 endpoint behavior.

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
Python:       3.11.9
voxcpm:       2.0.3
PyTorch:      2.13.0+cpu
CPU venv:     C:\AI-Archive\VoxCPM2-CPU-TEST\.venv
Model archive:C:\AI-Archive\VoxCPM2-paused-RTX3060
Final package:C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL
Final work:   C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL
```

Known model snapshot:

```text
C:\AI-Archive\VoxCPM2-paused-RTX3060\models\voxcpm2-model-cache\models--openbmb--VoxCPM2\snapshots\bffb3df5a29440629464e5e839f4d214c8714c3d
```

## Confirmed CPU benchmark

```text
Output duration: 10.72 sec
Synthesis:       101.37 sec
RTF:             9.46
Estimated 42:23: 6.68 hours
```

CPU synthesis is slow but usable.

## UX rule for assisting the owner

Prefer one complete PowerShell block or a downloadable ZIP.

Do not instruct the owner to:

- paste Python directly into PowerShell;
- manually maintain Python indentation;
- open a file and search/replace lines when a PowerShell patch can do it;
- perform a chain of contradictory edits;
- rerun expensive synthesis to change only the final mix.

Before distributing a PowerShell launcher:

1. parse it with the PowerShell AST using an absolute path;
2. keep command arguments in arrays rather than fragile backtick chains;
3. avoid encoding-sensitive decorative output;
4. create required directories automatically;
5. prepare or verify source and references automatically;
6. run `production_preflight.py` before loading the model.

Dedicated CI:

```text
.github/workflows/voxcpm2-windows.yml
```

## Known failures and correct interpretation

### Cache 512 vs prompt 550

```text
The expanded size of the tensor (512) must match the existing size (550)
```

Cause: prompt/reference prefill exceeded the locally configured cache.

### `KV cache is full`

Cause: autoregressive generation reached the cache limit.

Working segmented cache setup:

```python
cache_dtype = next(model.tts_model.parameters()).dtype
cache_device = model.tts_model.device

model.tts_model.base_lm.setup_cache(
    1, 4096, cache_device, cache_dtype
)
model.tts_model.residual_lm.setup_cache(
    1, 4096, cache_device, cache_dtype
)
```

Do not assume the saved snapshot's original 512-position configuration is adequate.

### Premature stop / incomplete one-pass generation

```text
Video:            48.69 sec
Generated Russian:19.20 sec
Synthesis:        213.80 sec
RTF:              11.14
Required atempo:  0.394299
```

Do not stretch incomplete speech to the full video. Use thought-sized segments.

### Swallowed endings and chewing tails

V2 had weak endings and choppy joins. V3.1 produced a repeated pattern:

```text
normal speech -> silence -> weak speech-like restart
```

Root cause: `min_len` was forced to approximately 92-95% of a video timing window. The model had naturally finished but was prevented from stopping.

Current rule:

```text
First attempt min_len: 2
Retry minimum: modest and only when output is demonstrably incomplete
Never derive min_len from 90%+ of the subtitle window
```

### Slowdown of short output

`atempo < 1` stretches pauses, breaths and model artifacts as well as speech.

Current rule:

```text
Short output: preserve natural speed and pad silence
Long output: accelerate only within a guarded range
```

### PowerShell parser and encoding failures

The first final launcher had an unmatched expression. Subsequent manual rewriting corrupted Cyrillic and caused cascading parse errors.

Current response:

- use ASCII-safe launchers;
- use argument arrays;
- validate every `.ps1` in Windows CI;
- use absolute paths for parser checks;
- never treat “file not found under System32” as a syntax error.

### Missing source/directories/references

The first final launcher assumed a prepared work root.

Current response:

- create every directory;
- reuse or download source automatically;
- generate B/C references automatically;
- preflight all assets before model loading.

## Accepted production architecture

Use four thought-sized blocks for the current MacArthur Short:

```text
Block 1: 00.000-10.880, B extended reference
Block 2: 10.880-24.160, B extended reference
Block 3: 24.720-32.600, B extended reference
Block 4: 33.200-48.694, C composite reference
```

For every block:

1. inject the approved reference again;
2. use reference-only cross-language cloning;
3. generate at least two candidates for a final render;
4. use fixed recorded seeds;
5. detect clipping, abnormal edge silence and pause-restart tails;
6. select the best candidate;
7. clean only a confirmed post-silence tail;
8. never fade the spoken ending;
9. never slow a short candidate;
10. accelerate only if it exceeds the protected speech slot;
11. pad to the exact source window;
12. assemble one timeline and master once.

## Reference policy

Current production references:

```text
B extended:
0.00-24.00 seconds of the source
Used for blocks 1-3

C composite:
0.00-10.88 plus 33.20-43.20 seconds
Used for the final block
```

Reason:

- B had the best opening and stable general delivery;
- C had the strongest terminal cadence;
- D Ultimate was the worst local test;
- A introduced an unnatural isolated pause.

Current safe preparation:

```text
highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2
```

Do not use aggressive `afftdn` by default.

A cleaner 15-25 second close-mic MacArthur reference is a higher-value future improvement than simply making the current reverberant reference longer.

## Clone-mode policy

For English reference -> Russian target:

```text
Production default: reference-only
```

```python
reference_wav_path=reference
```

Ultimate/combined is research-only for this use case. The local D sample was worst, and cross-language reports describe source-language accent leakage.

## Mix policy

The owner rejected speech-triggered ducking. The original speaker should remain at one constant gain.

Important terminology:

```text
reduce original by 25% = gain 0.75
leave original at 25%  = gain 0.25
```

Current intended range:

```text
OriginalGain: 0.70-0.78
Default test: 0.75
Sidechain:    disabled
```

Changing the gain must run only the remaster tool, not VoxCPM2 again:

```text
tools/voxcpm2/windows/Remaster-MacArthur-Constant-Gain.ps1
```

## Production tools

```text
tools/voxcpm2/production_preflight.py
tools/voxcpm2/windows/Run-MacArthur-Final-CPU.ps1
tools/voxcpm2/windows/Remaster-MacArthur-Constant-Gain.ps1
tools/voxcpm2/examples/macarthur_raasabpj_iw/segments_ru_final.json
tools/voxcpm2/examples/macarthur_raasabpj_iw/subtitles_ru_final.srt
```

Full runbook:

```text
docs/voxcpm2/PRODUCTION_RUNBOOK.md
```

Current status:

```text
docs/voxcpm2/CURRENT_STATE.md
```

## Integration direction

Do not import torch during normal Telegram bot startup.

Use a lazy, one-job-at-a-time worker with:

- incremental per-segment manifest;
- cancellation and progress;
- selected-segment regeneration;
- model loaded once;
- CPU-only environment before imports;
- candidate ASR completeness checks;
- exact timing and publication reports.

Reuse existing LiveDub components for:

- source acquisition;
- translation/theological QA;
- faster-whisper transcripts;
- final publication and media policy;
- cancellation/progress.

Do not reuse dynamic sidechain policy for this owner's chosen mix. Constant original gain is required.

## Before claiming success

Verify all of the following:

- `CUDA available: False`;
- preflight JSON exists;
- every expected segment has multiple attempts and one selected candidate;
- selected seeds and parameters are recorded;
- no short candidate was slowed;
- no pause-restart chewing tail remains;
- all intended Russian words are present;
- theological meaning is correct;
- first and last consonants are intact;
- final audio duration matches source;
- Russian-only output is reviewed before judging mix echo;
- original English uses one documented constant gain;
- synthesis JSON and master JSON exist;
- output paths and status are reported truthfully;
- owner explicitly approves the final upload MP4.
