# VoxCPM2 current state

Updated: 2026-07-26.

## Status summary

```text
V3.2 NoChew: owner-approved technical baseline
Final Steps 16 / CFG 1.80 run: in progress
Final publication approval: not yet granted
GPU policy: CPU only; CUDA must remain hidden
```

## What is proven locally

- VoxCPM2 2.0.3 loads from the saved local snapshot on CPU.
- The damaged RTX 3060 can be fully excluded from the process.
- A 48.69-second Short can be synthesized as four thought-sized blocks.
- Reference-only cloning is better than local Ultimate sample D for English-to-Russian speech.
- B extended reference gives the strongest main delivery.
- C composite reference gives the best concluding cadence.
- High timing-derived `min_len` caused pause-then-chewing tails.
- Returning first attempts to natural `min_len=2` removed the repeated swallowed-ending defect in V3.2.
- Short generated speech must be padded with silence, never slowed merely to fill its subtitle window.
- Seven short segments sounded less fluid than four thought-sized segments.
- Dynamic sidechain ducking was rejected by the owner; the original speaker should remain at one constant gain.

## V3.2 owner review

The owner reported that V3.2 was substantially better than all previous versions:

- no obvious swallowed words;
- endings sounded clear;
- the full Short was coherent;
- overall result was usable as the basis for a publication render.

Remaining concerns:

- maximize similarity to MacArthur;
- reduce room/echo coloration where possible;
- confirm final four-block candidate selection;
- choose a constant original-English gain;
- inspect the final mastered MP4 before channel upload.

## Current final experiment

Profile:

```text
Steps:             16
CFG:               1.80
Candidates:        2 per segment, third only when suspicious
Seed policy:       fixed and recorded
References:        B for segments 1-3, C for segment 4
NoChew:            enabled
Short slowdown:    forbidden
Intermediate WAV:  24-bit / 48 kHz
Master target:     -14 LUFS, -1 dBTP
```

Status: running locally. Do not mark successful until the generated Russian-only MP4, mixed MP4, synthesis JSON and master JSON have been listened to and inspected.

## Important mix correction

The phrase “reduce the original by 25%” means linear gain `0.75`, not `0.25`.

The currently running local launcher may produce an initial mix at `0.25`. Do not discard or rerun the expensive Russian synthesis. Use the repository remaster tool on the completed Russian timeline:

```powershell
.\tools\voxcpm2\windows\Remaster-MacArthur-Constant-Gain.ps1 `
    -OriginalGain 0.75
```

Generate 0.70, 0.75 and 0.78 variants only if needed. They reuse the same Russian WAV and are cheap to create.

## Local paths

```text
CPU venv:
C:\AI-Archive\VoxCPM2-CPU-TEST\.venv

Model archive:
C:\AI-Archive\VoxCPM2-paused-RTX3060

Final package:
C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL

Final work root:
C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL
```

## Next action after the running job

1. Listen to `FINAL_RUSSIAN_ONLY` first.
2. Check all four endings and the final cadence.
3. Read synthesis JSON for selected attempts, seeds, tail trimming and tempo.
4. Remaster the same Russian WAV at `OriginalGain=0.75`.
5. Compare mixed and Russian-only echo.
6. Approve or regenerate only the defective segment.
7. Record exact results in `EXPERIMENT_LOG.md`.

Do not launch another full-model sweep before evaluating the result already being generated.
