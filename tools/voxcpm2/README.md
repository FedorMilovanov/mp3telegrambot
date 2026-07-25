# VoxCPM2 tools

Experimental CPU-only dubbing tools. They are not yet imported by the Telegram bot runtime.

Full operational handbook:

- [`docs/voxcpm2/README.md`](../../docs/voxcpm2/README.md)
- [`docs/voxcpm2/HANDOFF_FOR_AI.md`](../../docs/voxcpm2/HANDOFF_FOR_AI.md)

## Current Windows example

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force

.\tools\voxcpm2\windows\Run-MacArthur-Short-Segmented-CPU.ps1 `
    -RepoRoot (Get-Location).Path `
    -Steps 4 `
    -CloneMode reference
```

The launcher expects the current local laboratory paths:

```text
C:\AI-Archive\VoxCPM2-CPU-TEST\.venv
C:\AI-Archive\VoxCPM2-paused-RTX3060
```

It creates work files outside the repository:

```text
C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-V2
```

## Safety

The local RTX 3060 is hardware-defective for this workload. The tool must remain CPU-only and must report:

```text
CUDA available: False
```

## Files

```text
segmented_cpu_dub.py
  Loads the model once, generates each timed segment, fits it with FFmpeg,
  and creates one complete timeline WAV plus JSON report.

windows/Run-MacArthur-Short-Segmented-CPU.ps1
  Downloads/reuses the Short, prepares the reference, invokes the segmented
  synthesizer, and creates a sidechain-ducked MP4.

examples/macarthur_raasabpj_iw/
  Source-aligned Russian segment JSON, SRT and exact English reference text.
```

## Quality profiles

Draft:

```powershell
-Steps 4 -CloneMode reference
```

Final A/B:

```powershell
-Steps 10 -CloneMode reference
-Steps 10 -CloneMode ultimate
```

Do not switch to `ultimate` until the reference-only run has completed and been reviewed. Cross-language Ultimate cloning can preserve source-language articulation more strongly.
