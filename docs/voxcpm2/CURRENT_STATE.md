# VoxCPM2 current state

Updated: 2026-07-26.

## Status summary

```text
V3.2 NoChew: owner-approved technical baseline
Final Steps 16 / CFG 1.80 render: completed locally
Publication approval: not yet granted
Current revision: ENG25 + per-block Russian delay
RTX 3060 NVENC: one successful 48.69-second probation run
GPU policy: VoxCPM2 CPU only; RTX 3060 optional for isolated NVENC
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
- The RTX 3060 completed one isolated H.264 NVENC transcode with software decode and CPU audio processing.

## Latest owner review

The completed final render produced usable Russian audio, but two mix/timing revisions were required:

1. the English original was too loud;
2. the Russian voice began slightly too early in some blocks.

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

Technical caution: block 4 originally reaches the source end. A +40 ms shift is trimmed back to the 48.694-second video duration. The existing 440 ms tail guard should make this silence-only, but the final word and final breath must still be checked by ear. If the ending is affected, use `DelayMs 220,160,100,0`.

This is a listening profile, not a claimed universal optimum. Change only the defective block after review.

## RTX 3060 NVENC result

### First attempt

The first NVENC attempt failed before encoding any frame:

```text
cuInit(0) failed -> CUDA_ERROR_NO_DEVICE
```

Cause: the PowerShell session inherited `CUDA_VISIBLE_DEVICES=-1` from the CPU-only VoxCPM2 workflow. `nvidia-smi` could still list the adapter, but FFmpeg/NVENC could not initialize it.

This was not treated as a hardware failure because:

- zero frames were submitted;
- no TDR or driver reset occurred;
- no `nvlddmkm`/Display Event ID 14, 153 or 4101 was recorded.

The launcher now removes `CUDA_VISIBLE_DEVICES` only while the isolated remaster/NVENC child process runs, then restores the prior value.

### Second attempt

The corrected trial succeeded:

```text
Input video:      AV1 1080x1920, 29.97 fps
Output encoder:   h264_nvenc
Preset:           p5
Tune:             hq
Rate control:     VBR
CQ:               18
Target bitrate:   8 Mbit/s
Max rate:         14 Mbit/s
Buffer:           28 Mbit/s
Decode:           software / CPU
Audio processing: CPU
NVENC status:     success
```

GPU telemetry:

| Stage | P-state | Temperature | Power | GPU utilization | VRAM used |
|---|---:|---:|---:|---:|---:|
| Before | P8 | 45 C | 15.12 W | 21% | 1391 MiB |
| After | P2 | 50 C | 53.55 W | 9% | 1391 MiB |

Windows event check:

```text
No nvlddmkm/Display Event ID 14, 153 or 4101 detected.
```

Interpretation:

- the NVENC path is usable for at least one short isolated encode;
- temperature and power behavior were moderate;
- this does not prove CUDA compute stability;
- this does not yet prove long-duration NVENC stability;
- VoxCPM2 CUDA remains prohibited;
- every NVENC job must retain a CPU/video-copy fallback.

## Current outputs

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
4. Confirm that English at gain `0.25` is quiet enough but still intelligible underneath.
5. Listen to the NVENC file and confirm that video quality did not visibly regress.
6. Inspect the JSON report for `nvenc_status=success` and exact delays.
7. Check the fourth block's final word because its +40 ms delay reaches beyond the source duration and is trimmed.
8. If one block remains early, change only its delay value.
9. Do not rerun VoxCPM2 unless the spoken content itself is defective.

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
