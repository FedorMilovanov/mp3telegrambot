# VoxCPM2 tools

Production dubbing tools for local VoxCPM2 voice transfer and Russian Shorts assembly.

## Current runtime contract

VoxCPM2 synthesis is **CPU-only** on the current machine.

```text
CUDA_VISIBLE_DEVICES=-1
torch.cuda.is_available() == False
```

The RTX 3060 produced repeatable `nvlddmkm Event ID 153` during real VoxCPM2 BF16 and FP16 model workloads. The one-off CUDA probes and their launchers have been removed from the working tree. The preserved technical conclusion is in [`docs/voxcpm2/CUDA_RTX3060_POSTMORTEM_2026-07-27.md`](../../docs/voxcpm2/CUDA_RTX3060_POSTMORTEM_2026-07-27.md).

## John Piper Shorts — ready production command

Source: `Four Marks You Belong to Christ | John Piper Clip`.

The package follows the accepted John MacArthur scheme:

- zero-shot cloning from the speaker's own source audio;
- extended and composite reference profiles;
- five literal Russian semantic blocks;
- LocDiT 16 / CFG 1.80;
- multi-candidate selection and NoChew tail-restart detection;
- no slowdown of short successful candidates;
- constant original English background at 18%;
- final master at -14 LUFS / -1 dBTP;
- video stream copy.

Run from the repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\tools\voxcpm2\examples\john_piper_z20py4yqhyq\Run-John-Piper-FINAL-CPU.ps1
```

Or in one line, including the repository update:

```powershell
cd "C:\Users\Fedor\Projects\mp3telegrambot"; git pull origin main; Set-ExecutionPolicy -Scope Process Bypass -Force; .\tools\voxcpm2\examples\john_piper_z20py4yqhyq\Run-John-Piper-FINAL-CPU.ps1
```

Upload-ready output:

```text
C:\AI-Archive\John-Piper-Short-Z20Py4yQhYQ-FINAL\output\John_Piper_Russian_Dub_FINAL_UPLOAD.mp4
```

Full example documentation:
[`examples/john_piper_z20py4yqhyq/README_RU.md`](examples/john_piper_z20py4yqhyq/README_RU.md).

## John MacArthur

The accepted MacArthur CPU workflow remains the reference implementation. Use the completed timeline for remaster-only changes; regenerate speech only when the Russian wording or selected voice candidate must change.

```powershell
.\tools\voxcpm2\windows\Run-MacArthur-Final-CPU.ps1 `
    -RepoRoot (Get-Location).Path `
    -PackageDir "C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL" `
    -OriginalLevel 0.25 `
    -Steps 16 `
    -Cfg 1.80 `
    -OpenOutput
```

## Production quality rules

- reference-only voice cloning;
- references are mono 16 kHz, filtered and loudness-normalized;
- model remains loaded once per production run;
- at least two candidates per semantic block;
- third candidate only for suspicious restart, clipping, or severe underlength;
- detect and trim short tail restarts only inside confirmed silence;
- never slow a short successful candidate;
- reject tempo correction above the configured safety limit;
- intermediate speech uses 24-bit WAV;
- final mixed and Russian-only results are both retained;
- temporary candidate WAVs are deleted unless diagnostics are requested;
- final manifest records source, engine, references, synthesis parameters and output paths.

## Main documentation

- [`docs/voxcpm2/CUDA_RTX3060_POSTMORTEM_2026-07-27.md`](../../docs/voxcpm2/CUDA_RTX3060_POSTMORTEM_2026-07-27.md) — final CUDA decision;
- [`docs/voxcpm2/CURRENT_STATE.md`](../../docs/voxcpm2/CURRENT_STATE.md) — operational state;
- [`docs/voxcpm2/PRODUCTION_RUNBOOK.md`](../../docs/voxcpm2/PRODUCTION_RUNBOOK.md) — production and remaster workflow;
- [`docs/voxcpm2/REFERENCE_AUDIO_PLAYBOOK.md`](../../docs/voxcpm2/REFERENCE_AUDIO_PLAYBOOK.md) — reference selection;
- [`docs/voxcpm2/EXPERIMENT_LOG.md`](../../docs/voxcpm2/EXPERIMENT_LOG.md) — historical run log;
- [`docs/voxcpm2/HANDOFF_FOR_AI.md`](../../docs/voxcpm2/HANDOFF_FOR_AI.md) — compact handoff context.

## Existing reusable tools

```text
segmented_cpu_dub.py
  Historical segmented CPU engine retained for regression tests.

validate_run.py
  Validates generated reports without importing torch.

production_preflight.py
  Rejects missing source, references, scripts, model, disk space or CPU isolation.

quality_sweep.py
  Generates a compact CFG/steps sweep for a selected phrase.

windows/Run-MacArthur-Final-CPU.ps1
  Accepted MacArthur CPU production launcher.

examples/john_piper_z20py4yqhyq/Run-John-Piper-FINAL-CPU.ps1
  Self-contained John Piper CPU production launcher.
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

`.github/workflows/voxcpm2-windows.yml` compiles Python tools, parses PowerShell launchers through the PowerShell AST, runs lightweight tests and applies fatal Ruff checks.
