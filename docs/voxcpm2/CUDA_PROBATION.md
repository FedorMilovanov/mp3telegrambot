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

## Discovered CUDA environments

The local archive contains two CUDA-enabled PyTorch environments:

```text
Primary probation environment:
C:\AI-Archive\VoxCPM2-paused-RTX3060\environment\voxcpm2-torch271\Scripts\python.exe
PyTorch 2.7.1+cu126 / CUDA runtime 12.6

Secondary test environment:
C:\AI-Archive\VoxCPM2-paused-RTX3060\tests\voxcpm2-test\Scripts\python.exe
PyTorch 2.11.0+cu126 / CUDA runtime 12.6
```

The primary environment is used first because it is the archived VoxCPM2 GPU environment. The secondary environment is retained as a diagnostic cross-check, not an automatic fallback after a hardware failure.

## Passed result: Init

Run directory:

```text
C:\AI-Archive\RTX3060-CUDA-PROBATION\20260726-035648
```

Profile:

```text
Profile:                  Init
PyTorch:                  2.7.1+cu126
CUDA runtime:             12.6
CUDA available:           true
Adapter:                  NVIDIA GeForce RTX 3060
Compute capability:       8.6
Total memory:             12,884,377,600 bytes
Free memory at init:      11,799,625,728 bytes
Multiprocessors:          28
Per-process memory limit: 10%
Context initialization:   passed
```

Telemetry:

| Stage | P-state | Temperature | Power | GPU utilization | Memory utilization | VRAM used |
|---|---:|---:|---:|---:|---:|---:|
| Before init | P8 | 43 C | 15.23 W | 19% | 6% | 1563 MiB |
| After init | P0 | 46 C | 44.88 W | 3% | 0% | 1563 MiB |

Windows event check:

```text
No nvlddmkm Event ID 14 or 153.
No Display Event ID 4101.
No selected WHEA event.
```

Interpretation:

- the CUDA driver and runtime can initialize this adapter;
- PyTorch can create and synchronize a CUDA context;
- the first real CUDA probation stage completed without a selected Windows hardware/driver event;
- this does not test host/device data integrity, matrix computation, FP16/BF16, long-duration stability or VoxCPM2.

The transition to P0 after context initialization is not itself a failure. The observed temperature and power remained moderate during this short stage.

## Next test: Quick

Run only after saving work and closing GPU-heavy applications:

```powershell
cd "C:\Users\Fedor\Projects\mp3telegrambot"
git pull origin main

.\tools\voxcpm2\windows\Test-RTX3060-CUDA-Probation.ps1 `
    -RepoRoot (Get-Location).Path `
    -CudaPython "C:\AI-Archive\VoxCPM2-paused-RTX3060\environment\voxcpm2-torch271\Scripts\python.exe" `
    -Profile Quick `
    -OpenLogs
```

`Quick` repeats context initialization and then adds:

```text
64 MiB exact CPU -> GPU -> CPU memory round-trip
12 synchronized FP32 matrix multiplications at 1024x1024
```

Do not run `Mixed` or `Standard` before the `Quick` reports and Windows events have been reviewed.

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

## Interpretation of Quick

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
