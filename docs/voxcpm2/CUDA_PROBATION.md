# RTX 3060 CUDA probation

Updated: 2026-07-26.

## Scope

The local RTX 3060 has a history of `nvlddmkm` Event ID 153, LiveKernelEvent/WATCHDOG, CUBLAS failures, illegal memory access and BF16/FP16 failures. A successful short NVENC encode proves only that the hardware video encoder can complete that job. It does not prove CUDA compute stability.

VoxCPM2 production synthesis remains CPU-only. CUDA may be tested only through the staged probation launcher below.

## Non-negotiable rules

- save all work before testing;
- close CapCut, Premiere, Resolve, Topaz and other GPU-heavy applications;
- do not run FFmpeg/NVENC at the same time;
- do not change `TdrDelay`, `TdrLevel` or other TDR registry values;
- do not run automatic retries after any failure;
- stop after the first CUDA exception, timeout, driver reset, Event ID 153/4101/14 or WHEA event;
- never treat a passed smoke test as permission for an unattended VoxCPM2 CUDA render.

## Probe architecture

The launcher runs every stage in a fresh child process and checks Windows events after each one.

Profiles:

```text
Init:
  CUDA context initialization only

Quick:
  init
  64 MiB exact host/device memory round-trip
  12 synchronized 1024x1024 FP32 matrix multiplications

Mixed:
  Quick stages
  12 synchronized 1024x1024 FP16 matrix multiplications

Standard:
  Mixed stages
  20-second synchronized FP32 sustained loop
```

Debug containment:

```text
CUDA_LAUNCH_BLOCKING=1
PYTORCH_NO_CUDA_MEMORY_CACHING=1
TORCH_SHOW_CPP_STACKTRACES=1
per-process CUDA memory fraction: 10%
separate process per stage
45-second default timeout per stage
no automatic retry
```

The launcher restores the previous process environment after the test.

## First test

Run only `Quick` first:

```powershell
cd "C:\Users\Fedor\Projects\mp3telegrambot"
git pull origin main

.\tools\voxcpm2\windows\Test-RTX3060-CUDA-Probation.ps1 `
    -RepoRoot (Get-Location).Path `
    -Profile Quick `
    -OpenLogs
```

The launcher searches these CUDA-PyTorch environments:

```text
C:\AI\voxcpm2_env\Scripts\python.exe
C:\AI-Archive\VoxCPM2-paused-RTX3060\.venv\Scripts\python.exe
C:\AI-Archive\VoxCPM2-CUDA-TEST\.venv\Scripts\python.exe
C:\AI-Archive\VoxCPM2-GPU-TEST\.venv\Scripts\python.exe
system python
```

When discovery fails, pass the exact old CUDA Python path:

```powershell
-CudaPython "C:\path\to\cuda-env\Scripts\python.exe"
```

## Events that block further testing

The launcher stops on:

```text
nvlddmkm: 14, 153
Display: 4101
Microsoft-Windows-WHEA-Logger: 17, 18, 19, 46, 47
stage timeout
nonzero child-process exit
JSON report status other than passed
```

## Interpretation

A passed `Quick` profile means only:

- the CUDA driver initialized;
- a 64 MiB transfer round-trip matched the expected pattern;
- small synchronous FP32 matrix kernels completed;
- no selected GPU/WHEA event was observed during those stages.

It does not prove:

- FP16/BF16 stability;
- CUBLAS stability under model-shaped workloads;
- long-duration CUDA stability;
- VoxCPM2 stability;
- absence of intermittent hardware faults.

Only after reviewing a clean `Quick` result may `Mixed` be considered. `Standard` is not the next automatic step.

## Logs

Each run is written under:

```text
C:\AI-Archive\RTX3060-CUDA-PROBATION\<timestamp>
```

Each stage has:

```text
<stage>.report.json
<stage>.stdout.txt
<stage>.stderr.txt
```

Retain failed reports and the exact PowerShell output. Do not rerun until the failure has been classified.
