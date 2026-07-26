# VoxCPM2 production runbook

This runbook turns the MacArthur laboratory work into a repeatable Windows workflow. VoxCPM2 synthesis stays CPU-only. The damaged RTX 3060 may be used only as an isolated, disposable NVENC helper after a valid CPU master already exists.

## Current accepted synthesis baseline

The owner-approved technical baseline is **V3.2 NoChew**:

```text
Device:            CPU only
CUDA visible:      False
Clone mode:        reference-only
Segments:          4 thought-sized blocks
Segments 1-3:      B extended 24-second reference
Segment 4:         C composite reference
min_len:           natural default on first attempt
Short output:      never slowed down
Tail restart:      detected and conservatively removed
English bed:       constant gain; no sidechain
```

Do not regress to:

- `min_len` derived from 90%+ of the subtitle window;
- slowing short output to fill the whole window;
- end fades that touch spoken consonants;
- seven very short independently generated fragments;
- Ultimate mode as the English-to-Russian default;
- speech-triggered sidechain ducking;
- VoxCPM2 CUDA on the damaged RTX 3060.

## Current publication mix

The owner clarified the intended level after listening to the completed final render:

```text
Russian voice:     100%
Original English:  25% of source level
Linear gain:       0.25
Sidechain:          disabled
```

This is “leave the original at 25%”, not “reduce it by 25%”. Parameter names should use `OriginalGain` and the actual numeric gain must be written explicitly.

## Current per-block timing profile

The completed Russian timeline starts slightly too early in some places, but not equally in all four blocks. The first listening trial therefore uses separate positive delays:

| Block | Window | Delay |
|---:|---:|---:|
| 1 | 0.00–10.88 s | +220 ms |
| 2 | 10.88–24.16 s | +160 ms |
| 3 | 24.72–32.60 s | +100 ms |
| 4 | 33.20–48.694 s | +40 ms |

Delay the existing finished blocks. Do not regenerate or time-stretch speech merely to change entrance timing.

The fourth block is moved least because it already has the best terminal cadence and a 440 ms tail guard. The offsets are intentionally parameters so a later review can change one block without touching the others.

## Production files

Repository:

```text
tools/voxcpm2/production_preflight.py
tools/voxcpm2/remaster_delayed_nvenc.py
tools/voxcpm2/windows/Run-MacArthur-Final-CPU.ps1
tools/voxcpm2/windows/Remaster-MacArthur-Constant-Gain.ps1
tools/voxcpm2/windows/Remaster-MacArthur-Delayed-NVENC.ps1
tools/voxcpm2/examples/macarthur_raasabpj_iw/segments_ru_final.json
tools/voxcpm2/examples/macarthur_raasabpj_iw/subtitles_ru_final.srt
```

Local package:

```text
C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL
```

Local work root:

```text
C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL
```

## Full CPU synthesis

The self-contained production launcher:

1. creates all required work directories;
2. reuses a previously downloaded source video when available;
3. downloads the source only when no local copy exists;
4. creates B and C references when missing;
5. runs production preflight;
6. launches CPU-only synthesis;
7. creates a constant-gain master;
8. copies final subtitles.

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force

.\tools\voxcpm2\windows\Run-MacArthur-Final-CPU.ps1 `
    -RepoRoot (Get-Location).Path `
    -PackageDir "C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL" `
    -OriginalLevel 0.25 `
    -Steps 16 `
    -Cfg 1.80 `
    -OpenOutput
```

For an already completed Russian timeline, do not run this command again merely to adjust mix or timing.

## Constant-gain remaster only

```powershell
.\tools\voxcpm2\windows\Remaster-MacArthur-Constant-Gain.ps1 `
    -PackageDir "C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL" `
    -WorkRoot "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL" `
    -OriginalGain 0.25 `
    -OpenOutput
```

This preserves both the Russian synthesis and original video bitstream.

## Delayed ENG25 remaster with RTX 3060 NVENC trial

Run from the repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force

.\tools\voxcpm2\windows\Remaster-MacArthur-Delayed-NVENC.ps1 `
    -RepoRoot (Get-Location).Path `
    -PackageDir "C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL" `
    -WorkRoot "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL" `
    -OriginalGain 0.25 `
    -DelayMs 220,160,100,40 `
    -OpenOutput
```

The workflow performs these operations in order:

1. cuts the completed Russian timeline back into its four known windows;
2. places each block later by its own offset;
3. builds a new 24-bit, 48 kHz delayed Russian timeline;
4. calls the established two-pass master at `OriginalGain=0.25`;
5. creates a CPU/video-copy MP4 first;
6. then attempts a separate H.264 NVENC encode;
7. writes a JSON report whether NVENC succeeded, failed or was unavailable.

Outputs:

```text
MacArthur_FINAL_DELAYED_RUSSIAN_ONLY.mp4
MacArthur_FINAL_ENG25_DELAYED_VIDEO_COPY.mp4
MacArthur_FINAL_ENG25_DELAYED_NVENC.mp4
MacArthur_FINAL_ENG25_DELAYED_NVENC.report.json
```

The CPU/video-copy file is the quality reference and remains valid even when the GPU trial fails.

## RTX 3060 containment policy

The GPU trial deliberately uses only the hardware encoder:

```text
Software decode:   yes
CPU audio filters: yes
NVDEC:             no
CUDA filters:      no
PyTorch CUDA:      no
VoxCPM2 CUDA:      no
Video encoder:     h264_nvenc
Preset:            p5
Tune:              hq
Rate control:      VBR
CQ:                18
Bitrate:           8 Mbit/s
Max rate:          14 Mbit/s
Buffer:            28 Mbit/s
```

The launcher reads `nvidia-smi` before and after the trial and checks new Windows System events from `nvlddmkm` and `Display` for IDs 14, 153 and 4101.

A successful MP4 alone is not enough to call the card stable. Any driver reset, Event ID 153, Display 4101, corruption, freeze or NVENC error means the GPU result is rejected and the CPU/video-copy master is retained.

Do not disable TDR, modify registry timeout values, launch another GPU retry loop or run CapCut/CUDA inference alongside this test.

## Preflight contract

`production_preflight.py` runs without importing torch or VoxCPM. It blocks expensive model loading when any of the following is wrong:

- Python executable missing;
- package scripts missing or syntactically invalid;
- FFmpeg/ffprobe missing;
- source video missing or unreadable;
- final segments JSON invalid or outside source duration;
- B/C references missing;
- model snapshot missing;
- insufficient free disk space;
- `CUDA_VISIBLE_DEVICES` is not exactly `-1` during synthesis.

## Listening order

1. Listen to `MacArthur_FINAL_DELAYED_RUSSIAN_ONLY.mp4`.
2. Judge the four entrances and endings without English underneath.
3. Compare `ENG25_DELAYED_VIDEO_COPY` against the previous mixed render.
4. Compare the NVENC file visually against the video-copy reference.
5. Read the report and Windows event output.
6. If timing is wrong in one block, alter only the corresponding `DelayMs` value.

Suggested second-pass examples:

```powershell
# Move only block 1 slightly more
-DelayMs 260,160,100,40

# Keep blocks 3 and 4 unchanged
-DelayMs 220,160,0,0
```

## Publication acceptance gate

A final MP4 is not publication-ready until all checks pass:

- every intended Russian sentence is present;
- no swallowed final word or consonant;
- no pause-then-chewing restart;
- no repeated syllable or hallucinated word;
- theological wording is accurate;
- all four Russian entrances feel naturally placed;
- original English remains at constant gain `0.25`;
- no sidechain pumping;
- no clipping;
- beginning and ending are not cut;
- source and final durations agree;
- synthesis, master and delayed-remaster reports are retained;
- NVENC output is rejected if Windows logged a GPU/Display fault.

## Repository quality controls

`.github/workflows/voxcpm2-windows.yml` performs:

- compilation of every VoxCPM2 Python tool;
- PowerShell AST parsing for every launcher;
- lightweight timing/remaster regression tests;
- fatal Ruff checks.

The dedicated delayed-remaster tests verify the four offsets are translated into absolute timeline positions correctly.
