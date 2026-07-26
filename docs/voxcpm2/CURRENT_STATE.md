# VoxCPM2 current state

Updated: 2026-07-26.

## Status summary

```text
V3.2 NoChew: owner-approved technical baseline
Final Steps 16 / CFG 1.80 render: completed locally
Publication approval: not yet granted
Current revision: ENG25 + per-block Russian delay
GPU policy: VoxCPM2 CPU only; RTX 3060 may be used for isolated NVENC only
```

## What is proven locally

- VoxCPM2 2.0.3 loads from the saved local snapshot on CPU.
- The damaged RTX 3060 can be fully excluded from synthesis.
- A 48.69-second Short can be synthesized as four thought-sized blocks.
- Reference-only cloning is better than local Ultimate sample D for English-to-Russian speech.
- B extended reference gives the strongest main delivery.
- C composite reference gives the best concluding cadence.
- High timing-derived `min_len` caused pause-then-chewing tails.
- Returning first attempts to natural `min_len=2` removed the repeated swallowed-ending defect in V3.2.
- Short generated speech must be padded with silence, never slowed merely to fill its subtitle window.
- Seven short segments sounded less fluid than four thought-sized segments.
- Dynamic sidechain ducking was rejected; the original speaker remains at one constant gain.

## Latest owner review

The final render completed and produced usable Russian audio, but two mix/timing revisions are required:

1. the English original is too loud;
2. the Russian voice begins slightly too early in some blocks.

The owner clarified the intended volume unambiguously:

```text
Original English must remain at 25% of source level.
Correct linear gain: 0.25.
This does NOT mean reducing by 25% to gain 0.75.
```

Do not regenerate VoxCPM2 for these changes. Reuse the completed Russian timeline.

## Current timing profile

The first delayed-remaster trial uses different offsets for each four-block segment:

| Block | Source window | Added delay |
|---:|---:|---:|
| 1 | 0.00–10.88 s | +220 ms |
| 2 | 10.88–24.16 s | +160 ms |
| 3 | 24.72–32.60 s | +100 ms |
| 4 | 33.20–48.694 s | +40 ms |

Rationale:

- the first block receives the strongest correction because the early entrance is most exposed at the beginning;
- later blocks receive progressively smaller shifts because some already sounded acceptably aligned;
- the final block receives only a minimal shift to protect its ending and existing 440 ms tail guard.

This is a listening profile, not a claimed universal optimum. Change only the defective block after review.

## RTX 3060 trial policy

The RTX 3060 remains prohibited for VoxCPM2 CUDA synthesis. The current experiment uses it only for final H.264 NVENC encoding:

```text
Decode:         software / CPU
Audio filters:  CPU
Loudness pass:  CPU
Video encode:   h264_nvenc only
Preset:         p5
Tune:           hq
Rate control:   VBR
CQ:             18
Target bitrate: 8 Mbit/s
Max rate:       14 Mbit/s
Buffer:         28 Mbit/s
```

No `-hwaccel cuda`, NVDEC, CUDA filters or torch CUDA are used. The CPU video-copy master is always created first and remains valid if NVENC fails.

The launcher checks `nvidia-smi` before and after encoding and inspects new Windows System events from providers `nvlddmkm` and `Display` with IDs 14, 153 and 4101.

## Current command

From the repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force

.\tools\voxcpm2\windows\Remaster-MacArthur-Delayed-NVENC.ps1 `
    -RepoRoot (Get-Location).Path `
    -OriginalGain 0.25 `
    -DelayMs 220,160,100,40 `
    -OpenOutput
```

This creates:

```text
MacArthur_FINAL_DELAYED_RUSSIAN_ONLY.mp4
MacArthur_FINAL_ENG25_DELAYED_VIDEO_COPY.mp4
MacArthur_FINAL_ENG25_DELAYED_NVENC.mp4
MacArthur_FINAL_ENG25_DELAYED_NVENC.report.json
```

## Acceptance order

1. Listen to `DELAYED_RUSSIAN_ONLY` and judge only timing and speech endings.
2. Compare it against the previous Russian-only render.
3. Listen to `ENG25_DELAYED_VIDEO_COPY`; this is the no-video-reencode reference.
4. Listen to the NVENC file and confirm that video quality did not visibly regress.
5. Inspect the JSON report for `nvenc_status` and exact delays.
6. Check whether Windows logged Event ID 153, 4101 or another `nvlddmkm`/Display failure.
7. If one block remains early, change only its delay value.
8. Do not rerun VoxCPM2 unless the spoken content itself is defective.

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
