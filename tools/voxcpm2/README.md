# VoxCPM2 tools

Experimental CPU-only dubbing tools. They are not yet imported by the Telegram bot runtime.

## Documentation

- [`docs/voxcpm2/CURRENT_STATE.md`](../../docs/voxcpm2/CURRENT_STATE.md) — exact current local status and next action after the running final job.
- [`docs/voxcpm2/PRODUCTION_RUNBOOK.md`](../../docs/voxcpm2/PRODUCTION_RUNBOOK.md) — repeatable final-render workflow, preflight contract, gain semantics and publication gate.
- [`docs/voxcpm2/README.md`](../../docs/voxcpm2/README.md) — full operational handbook and research notes.
- [`docs/voxcpm2/HANDOFF_FOR_AI.md`](../../docs/voxcpm2/HANDOFF_FOR_AI.md) — compact context for another AI/developer.
- [`docs/voxcpm2/EXPERIMENT_LOG.md`](../../docs/voxcpm2/EXPERIMENT_LOG.md) — append-only run history and exact failures.
- [`docs/voxcpm2/REFERENCE_AUDIO_PLAYBOOK.md`](../../docs/voxcpm2/REFERENCE_AUDIO_PLAYBOOK.md) — reference selection, preparation and A/B policy.
- [`docs/voxcpm2/INTEGRATION_PLAN.md`](../../docs/voxcpm2/INTEGRATION_PLAN.md) — path from laboratory scripts to LiveDub integration.
- [`docs/voxcpm2/QUALITY_RESEARCH_2026-07-26.md`](../../docs/voxcpm2/QUALITY_RESEARCH_2026-07-26.md) — 50+ primary-source quality sweep and concrete tuning ladder.
- [`docs/voxcpm2/MODEL_COMPARISON_2026-07-26.md`](../../docs/voxcpm2/MODEL_COMPARISON_2026-07-26.md) — VoxCPM2 compared with Qwen3-TTS, Chatterbox, Fish, IndexTTS2 and other competitors.
- [`docs/voxcpm2/SOURCES.md`](../../docs/voxcpm2/SOURCES.md) — curated primary-source index.

## Production Windows launcher

The repository launcher is self-contained around the accepted extracted final package. It creates directories, reuses or downloads the source, creates B/C references, runs preflight, launches CPU synthesis and creates the constant-gain master.

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force

.\tools\voxcpm2\windows\Run-MacArthur-Final-CPU.ps1 `
    -RepoRoot (Get-Location).Path `
    -PackageDir "C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL" `
    -OriginalLevel 0.75 `
    -Steps 16 `
    -Cfg 1.80 `
    -OpenOutput
```

`0.75` means the original is reduced by 25%. `0.25` means the original is left at only 25% and is approximately 12 dB quieter. See the production runbook before changing mix values.

The launcher expects the current local model and environment paths:

```text
C:\AI-Archive\VoxCPM2-CPU-TEST\.venv
C:\AI-Archive\VoxCPM2-paused-RTX3060
```

It creates work files outside the repository:

```text
C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL
```

## Remaster without another synthesis

Changing only the English bed must not trigger another VoxCPM2 run:

```powershell
.\tools\voxcpm2\windows\Remaster-MacArthur-Constant-Gain.ps1 `
    -PackageDir "C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL" `
    -WorkRoot "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL" `
    -OriginalGain 0.75 `
    -OpenOutput
```

This reuses:

```text
C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL\audio\macarthur_ru_final_timeline.wav
```

## Safety

The local RTX 3060 is hardware-defective for this workload. Every production process must set the environment before importing torch and must report:

```text
CUDA available: False
```

Do not use a CUDA fallback before the GPU is replaced.

## Files

```text
segmented_cpu_dub.py
  Original segmented laboratory engine. It is retained for regression history,
  but its old timing-derived min_len/end-fade behavior is not the accepted
  production profile.

validate_run.py
  Checks the generated JSON report, CPU safety flag, segment files,
  duration agreement and tempo limits without importing torch.

production_preflight.py
  Fails before model loading when package scripts, source, references, segment
  timings, model snapshot, disk space or CUDA isolation are wrong.

quality_sweep.py
  Generates a compact CFG/steps grid for one ending-sensitive phrase and
  records duration, edge silence, clipping and pause-restart tail metrics.

windows/Run-MacArthur-Final-CPU.ps1
  Self-contained source/reference/preflight/synthesis/master workflow.

windows/Remaster-MacArthur-Constant-Gain.ps1
  Rebuilds only the final master at a fixed original-English gain.

windows/Run-MacArthur-Short-Segmented-CPU.ps1
  Historical V2 launcher kept for reproducibility; not the final profile.

windows/Run-MacArthur-Quality-Sweep.ps1
  Prepares the B extended reference and runs the short endpoint quality sweep.

examples/macarthur_raasabpj_iw/segments_ru_final.json
  Accepted four-block production timing plan.

examples/macarthur_raasabpj_iw/subtitles_ru_final.srt
  Final four-block Russian subtitle file.
```

## Production preflight

The preflight runs without torch:

```powershell
$Python = "C:\AI-Archive\VoxCPM2-CPU-TEST\.venv\Scripts\python.exe"

& $Python .\tools\voxcpm2\production_preflight.py `
    --python-exe $Python `
    --package-dir "C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL" `
    --work-root "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL" `
    --model-root "C:\AI-Archive\VoxCPM2-paused-RTX3060" `
    --source-video "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL\source\source.mp4" `
    --segments-json .\tools\voxcpm2\examples\macarthur_raasabpj_iw\segments_ru_final.json `
    --extended-reference "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL\references\B_extended_24s.wav" `
    --composite-reference "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL\references\C_composite_21s.wav"
```

It checks package syntax, FFmpeg, source duration, final segment bounds, B/C references, local model files, free disk and `CUDA_VISIBLE_DEVICES=-1`.

## Validate a completed segmented run

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

Do this only after the currently running full render has finished and been reviewed:

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

## Tests and CI

The lightweight tests do not load the model:

```powershell
python -m pytest `
    tests/test_voxcpm2_tools.py `
    tests/test_voxcpm2_quality_sweep.py `
    tests/test_voxcpm2_production_preflight.py `
    -q
```

The dedicated Windows workflow:

```text
.github/workflows/voxcpm2-windows.yml
```

compiles all VoxCPM2 Python tools, parses every PowerShell launcher with the PowerShell AST, runs the lightweight tests and applies fatal Ruff checks. A broken quote, unmatched parenthesis or encoding-damaged launcher must fail before distribution.

## Quality profiles

Historical draft:

```powershell
-Steps 4 -CloneMode reference
```

Accepted NoChew baseline:

```text
reference-only
B extended for blocks 1-3
C composite for final block
natural min_len first attempt
no slowdown of short candidates
four thought-sized blocks
```

Current final experiment:

```text
Steps 16
CFG 1.80
multiple candidates per segment
fixed recorded seeds
24-bit intermediate WAV
constant original gain
```

Do not use `ultimate` as the default for English-to-Russian cloning. The local D test was worst, and upstream cross-language reports describe source-language accent leakage in Ultimate mode.
