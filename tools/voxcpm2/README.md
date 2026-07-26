# VoxCPM2 tools

Experimental dubbing tools. VoxCPM2 synthesis is CPU-only on the current machine. The damaged RTX 3060 is permitted only for an isolated NVENC encode after a valid CPU/video-copy master already exists.

## Documentation

- [`docs/voxcpm2/CURRENT_STATE.md`](../../docs/voxcpm2/CURRENT_STATE.md) — exact current review, ENG25 mix, block delays and NVENC trial.
- [`docs/voxcpm2/PRODUCTION_RUNBOOK.md`](../../docs/voxcpm2/PRODUCTION_RUNBOOK.md) — repeatable production and remaster workflow.
- [`docs/voxcpm2/README.md`](../../docs/voxcpm2/README.md) — full operational handbook and research notes.
- [`docs/voxcpm2/HANDOFF_FOR_AI.md`](../../docs/voxcpm2/HANDOFF_FOR_AI.md) — compact context for another AI/developer.
- [`docs/voxcpm2/EXPERIMENT_LOG.md`](../../docs/voxcpm2/EXPERIMENT_LOG.md) — append-only run history and exact failures.
- [`docs/voxcpm2/REFERENCE_AUDIO_PLAYBOOK.md`](../../docs/voxcpm2/REFERENCE_AUDIO_PLAYBOOK.md) — reference selection and A/B policy.
- [`docs/voxcpm2/INTEGRATION_PLAN.md`](../../docs/voxcpm2/INTEGRATION_PLAN.md) — path from laboratory scripts to LiveDub.
- [`docs/voxcpm2/QUALITY_RESEARCH_2026-07-26.md`](../../docs/voxcpm2/QUALITY_RESEARCH_2026-07-26.md) — primary-source quality sweep.
- [`docs/voxcpm2/MODEL_COMPARISON_2026-07-26.md`](../../docs/voxcpm2/MODEL_COMPARISON_2026-07-26.md) — model comparison.
- [`docs/voxcpm2/SOURCES.md`](../../docs/voxcpm2/SOURCES.md) — curated primary-source index.

## Current completed-render correction

Owner listening review established two required changes:

```text
Original English: leave at 25% = linear gain 0.25
Russian timing: move four blocks later by 220,160,100,40 ms
```

Do not rerun VoxCPM2. Use the completed Russian timeline.

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

The tool creates a CPU/video-copy reference first and only then attempts NVENC:

```text
MacArthur_FINAL_DELAYED_RUSSIAN_ONLY.mp4
MacArthur_FINAL_ENG25_DELAYED_VIDEO_COPY.mp4
MacArthur_FINAL_ENG25_DELAYED_NVENC.mp4
MacArthur_FINAL_ENG25_DELAYED_NVENC.report.json
```

The NVENC test uses software decode and CPU audio processing. It does not use NVDEC, CUDA filters, PyTorch CUDA or VoxCPM2 CUDA.

## Full CPU production launcher

Use only when speech itself must be regenerated:

```powershell
.\tools\voxcpm2\windows\Run-MacArthur-Final-CPU.ps1 `
    -RepoRoot (Get-Location).Path `
    -PackageDir "C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL" `
    -OriginalLevel 0.25 `
    -Steps 16 `
    -Cfg 1.80 `
    -OpenOutput
```

Local paths:

```text
C:\AI-Archive\VoxCPM2-CPU-TEST\.venv
C:\AI-Archive\VoxCPM2-paused-RTX3060
C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL
```

## Constant-gain remaster without timing changes

```powershell
.\tools\voxcpm2\windows\Remaster-MacArthur-Constant-Gain.ps1 `
    -OriginalGain 0.25 `
    -OpenOutput
```

## Safety

VoxCPM2 must report:

```text
CUDA available: False
```

Never use the RTX 3060 for the expensive synthesis. During the NVENC-only trial, reject the GPU output when any of these occur:

- `nvlddmkm` Event ID 14 or 153;
- Display Event ID 4101;
- driver reset, freeze or corruption;
- FFmpeg NVENC failure.

The CPU/video-copy master remains the fallback and should not be deleted.

## Main files

```text
segmented_cpu_dub.py
  Historical segmented engine retained for regression tests.

validate_run.py
  Validates generated reports without importing torch.

production_preflight.py
  Fails before model loading when source, references, scripts, model, disk or CPU isolation are wrong.

remaster_delayed_nvenc.py
  Repositions completed Russian blocks, remasters with gain 0.25, creates a video-copy master and attempts isolated h264_nvenc.

quality_sweep.py
  Generates a compact CFG/steps sweep for one ending-sensitive phrase.

windows/Run-MacArthur-Final-CPU.ps1
  Self-contained CPU synthesis workflow.

windows/Remaster-MacArthur-Constant-Gain.ps1
  Rebuilds only the fixed-gain master.

windows/Remaster-MacArthur-Delayed-NVENC.ps1
  Current ENG25 timing correction and RTX 3060 NVENC trial.

examples/macarthur_raasabpj_iw/segments_ru_final.json
  Accepted four-block timing plan.
```

## Tests and CI

```powershell
python -m pytest `
    tests/test_voxcpm2_tools.py `
    tests/test_voxcpm2_quality_sweep.py `
    tests/test_voxcpm2_production_preflight.py `
    tests/test_voxcpm2_diagnostic_pack.py `
    tests/test_voxcpm2_remaster_delayed_nvenc.py `
    -q
```

`.github/workflows/voxcpm2-windows.yml` compiles all VoxCPM2 Python tools, parses every PowerShell launcher through the PowerShell AST, runs lightweight tests and applies fatal Ruff checks.
