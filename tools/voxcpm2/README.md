# VoxCPM2 tools

Experimental CPU-only dubbing tools. They are not yet imported by the Telegram bot runtime.

## Documentation

- [`docs/voxcpm2/README.md`](../../docs/voxcpm2/README.md) — full operational handbook and research notes.
- [`docs/voxcpm2/HANDOFF_FOR_AI.md`](../../docs/voxcpm2/HANDOFF_FOR_AI.md) — compact context for another AI/developer.
- [`docs/voxcpm2/EXPERIMENT_LOG.md`](../../docs/voxcpm2/EXPERIMENT_LOG.md) — append-only run history and exact failures.
- [`docs/voxcpm2/REFERENCE_AUDIO_PLAYBOOK.md`](../../docs/voxcpm2/REFERENCE_AUDIO_PLAYBOOK.md) — reference selection, preparation and A/B policy.
- [`docs/voxcpm2/INTEGRATION_PLAN.md`](../../docs/voxcpm2/INTEGRATION_PLAN.md) — path from laboratory scripts to LiveDub integration.
- [`docs/voxcpm2/QUALITY_RESEARCH_2026-07-26.md`](../../docs/voxcpm2/QUALITY_RESEARCH_2026-07-26.md) — 50+ primary-source quality sweep and concrete tuning ladder.

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

Do not use a CUDA fallback before the GPU is replaced.

## Files

```text
segmented_cpu_dub.py
  Loads the model once, generates each timed segment, fits it with FFmpeg,
  and creates one complete timeline WAV plus JSON report.

validate_run.py
  Checks the generated JSON report, CPU safety flag, segment files,
  duration agreement and tempo limits without importing torch.

quality_sweep.py
  Generates a compact CFG/steps grid for one ending-sensitive phrase and
  records duration, edge silence, clipping and pause-restart tail metrics.

windows/Run-MacArthur-Short-Segmented-CPU.ps1
  Downloads/reuses the Short, prepares the reference, invokes the segmented
  synthesizer, and creates a sidechain-ducked MP4.

windows/Run-MacArthur-Quality-Sweep.ps1
  Prepares the B extended MacArthur reference and runs the short endpoint
  quality sweep without rendering a full video.

examples/macarthur_raasabpj_iw/
  Source-aligned Russian segment JSON, SRT and exact English reference text.
```

## Validate a completed run

```powershell
$Python = "C:\AI-Archive\VoxCPM2-CPU-TEST\.venv\Scripts\python.exe"
$Report = "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-V2\audio\macarthur_ru_segmented_timeline.json"

& $Python .\tools\voxcpm2\validate_run.py $Report
```

Expected hard-success line:

```text
VALID: segmented VoxCPM2 report passed all hard checks
```

Warnings for a tempo outside `0.80..1.25` require listening review. A hard error outside `0.65..1.65`, a missing segment, CUDA usage or a duration mismatch blocks publication.

## Run the compact quality sweep

Do this only after the currently running full render has finished:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force

.\tools\voxcpm2\windows\Run-MacArthur-Quality-Sweep.ps1 `
    -RepoRoot (Get-Location).Path `
    -CfgValues "1.55,1.75,1.95" `
    -StepsValues "10"
```

The first sweep changes one axis only: CFG. After choosing the best CFG by endpoint metrics and listening, compare diffusion detail separately:

```powershell
.\tools\voxcpm2\windows\Run-MacArthur-Quality-Sweep.ps1 `
    -RepoRoot (Get-Location).Path `
    -CfgValues "<winning-CFG>" `
    -StepsValues "10,16"
```

The JSON objective ranking is only an artifact screen. Human listening still decides MacArthur similarity, natural cadence and room coloration.

## Tests

The lightweight tests do not load the model:

```powershell
python -m pytest `
    tests/test_voxcpm2_tools.py `
    tests/test_voxcpm2_quality_sweep.py `
    -q
```

They cover:

- arbitrary FFmpeg `atempo` decomposition;
- segment JSON overlap validation;
- final report duration checks;
- CPU-only safety enforcement;
- tempo rejection and review warnings;
- pause-restart tail detection;
- leading/trailing silence metrics;
- clipping-free clean ending detection.

## Quality profiles

Draft:

```powershell
-Steps 4 -CloneMode reference
```

Final reference-only baseline:

```powershell
-Steps 10 -CloneMode reference
```

Do not use `ultimate` as the default for English-to-Russian cloning. The local D test was worst, and upstream cross-language reports describe source-language accent leakage in Ultimate mode.
