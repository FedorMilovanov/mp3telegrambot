# RTX 3060 CUDA probation

Updated: 2026-07-26.

## Scope

The local RTX 3060 has a confirmed history of `nvlddmkm` Event ID 153, LiveKernelEvent/WATCHDOG, CUBLAS failures, illegal memory access and BF16/FP16 failures. Short successful jobs prove that the failure is not constant; they do not erase the historical incidents.

VoxCPM2 production synthesis remains CPU-only. CUDA is allowed only through the staged probation launchers documented here.

## Non-negotiable rules

- save all work before testing;
- close CapCut, Premiere, Resolve, Topaz and other GPU-heavy applications;
- do not run FFmpeg/NVENC at the same time;
- do not change `TdrDelay`, `TdrLevel` or other TDR registry values;
- do not run automatic retries after any failure;
- stop after the first CUDA exception, timeout, driver reset, Event ID 153/4101/14 or selected WHEA event;
- never treat a passed smoke test as permission for an unattended full render.

## Probe architecture

The synthetic launcher runs every stage in a fresh child process and checks new Windows events after each stage.

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

Containment:

```text
CUDA_LAUNCH_BLOCKING=1
PYTORCH_NO_CUDA_MEMORY_CACHING=1
TORCH_SHOW_CPP_STACKTRACES=1
per-process CUDA memory fraction: 10%
separate process per stage
45-second default timeout per stage
no automatic retry
```

The launcher restores the previous process environment after every run.

## Discovered CUDA environments

```text
Primary probation environment:
C:\AI-Archive\VoxCPM2-paused-RTX3060\environment\voxcpm2-torch271\Scripts\python.exe
PyTorch 2.7.1+cu126 / CUDA runtime 12.6

Secondary diagnostic environment:
C:\AI-Archive\VoxCPM2-paused-RTX3060\tests\voxcpm2-test\Scripts\python.exe
PyTorch 2.11.0+cu126 / CUDA runtime 12.6
```

The primary archived VoxCPM2 environment is used first. The secondary environment is a diagnostic cross-check, not an automatic retry target after a hardware or driver failure.

## Passed result: Init

Run directory:

```text
C:\AI-Archive\RTX3060-CUDA-PROBATION\20260726-035648
```

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

| Checkpoint | P-state | Temperature | Power | GPU utilization | Memory utilization | VRAM used |
|---|---:|---:|---:|---:|---:|---:|
| Before init | P8 | 43 C | 15.23 W | 19% | 6% | 1563 MiB |
| After init | P0 | 46 C | 44.88 W | 3% | 0% | 1563 MiB |

No new `nvlddmkm` Event ID 14/153, Display 4101 or selected WHEA event was detected.

## Passed result: Quick

```text
init:   passed
memory: passed
fp32:   passed
```

| Checkpoint | P-state | Temperature | Power | GPU utilization | Memory utilization | VRAM used |
|---|---:|---:|---:|---:|---:|---:|
| Before init | P8 | 44 C | 15.80 W | 25% | 10% | 1611 MiB |
| After init | P0 | 47 C | 45.12 W | 3% | 0% | 1611 MiB |
| After memory | P0 | 47 C | 45.13 W | 4% | 0% | 1611 MiB |
| After FP32 | P5 | 46 C | 44.97 W | 6% | 1% | 1611 MiB |

Results:

- the exact 64 MiB CPU-to-GPU-to-CPU transfer returned the expected data;
- twelve synchronized 1024x1024 FP32 matrix multiplications returned the expected probe values;
- no CUDA exception, timeout or selected Windows event occurred.

## Passed result: Mixed

Run directory:

```text
C:\AI-Archive\RTX3060-CUDA-PROBATION\20260726-040713
```

```text
init:   passed
memory: passed
fp32:   passed
fp16:   passed
```

| Checkpoint | P-state | Temperature | Power | GPU utilization | Memory utilization | VRAM used |
|---|---:|---:|---:|---:|---:|---:|
| Before init | P8 | 44 C | 15.03 W | 21% | 7% | 1503 MiB |
| After init | P0 | 48 C | 45.60 W | 3% | 0% | 1503 MiB |
| After memory | P0 | 48 C | 45.74 W | 4% | 0% | 1503 MiB |
| After FP32 | P5 | 48 C | 44.22 W | 8% | 3% | 1503 MiB |
| After FP16 | P0 | 49 C | 45.95 W | 3% | 0% | 1503 MiB |

Twelve synchronized 1024x1024 FP16 matrix multiplications completed with the expected probe values. A manual event-log query covering the entire Mixed window found no new Event ID 153 and no selected GPU/Display/WHEA event.

The same one-day query still showed many historical `nvlddmkm` Event ID 153 records from 2026-07-25. Therefore the fault history remains confirmed and intermittent.

## Passed result: Standard

Run directory:

```text
C:\AI-Archive\RTX3060-CUDA-PROBATION\20260726-041549
```

```text
init:      passed
memory:    passed
fp32:      passed
fp16:      passed
sustained: passed
```

| Checkpoint | P-state | Temperature | Power | GPU utilization | Memory utilization | VRAM used |
|---|---:|---:|---:|---:|---:|---:|
| Before init | P8 | 45 C | 15.41 W | 22% | 7% | 1506 MiB |
| After init | P0 | 49 C | 45.53 W | 1% | 0% | 1506 MiB |
| After memory | P0 | 49 C | 45.98 W | 0% | 0% | 1506 MiB |
| After FP32 | P0 | 49 C | 45.70 W | 0% | 0% | 1506 MiB |
| After FP16 | P0 | 49 C | 45.73 W | 0% | 0% | 1506 MiB |
| After sustained | P3 | 54 C | 45.29 W | 0% | 0% | 1506 MiB |

The 20-second synchronized FP32 sustained loop passed. A separate baseline-RecordId query confirmed that no new GPU, Display or selected WHEA event appeared after the Standard run began.

## Interpretation after Standard

The RTX 3060 is not failing on every CUDA use. The following paths have now completed successfully in isolated tests:

- CUDA driver/runtime initialization;
- a limited exact VRAM transfer path;
- small synchronized FP32 matrix work;
- small synchronized FP16 matrix work;
- a short sustained FP32 loop;
- short isolated NVENC H.264 encoding.

This still does not prove:

- BF16 stability;
- VoxCPM2 model loading and generation stability;
- model-shaped CUBLAS behavior;
- large-memory stability;
- multi-minute or unattended CUDA stability;
- absence of intermittent hardware faults.

## Next permitted test: one VoxCPM2 model smoke

The next step is one model load and one short generation, in a separate process, with no retry. It uses a 70% per-process VRAM ceiling; if the model cannot fit, the correct result is a controlled OOM failure rather than raising the limit.

```powershell
cd "C:\Users\Fedor\Projects\mp3telegrambot"
git pull origin main

.\tools\voxcpm2\windows\Test-RTX3060-VoxCPM2-Model-Smoke.ps1 `
    -RepoRoot (Get-Location).Path `
    -CudaPython "C:\AI-Archive\VoxCPM2-paused-RTX3060\environment\voxcpm2-torch271\Scripts\python.exe" `
    -Steps 4 `
    -Cfg 1.80 `
    -MemoryFraction 0.70 `
    -TimeoutSeconds 300 `
    -OpenLogs
```

The model smoke:

- loads the local snapshot offline;
- uses the accepted B extended reference;
- generates only `Это короткая проверка работы системы.`;
- disables TF32 for the probe;
- runs one generation at four steps;
- records model load time, generation time and CUDA memory peaks;
- checks only Windows events newer than the baseline System RecordId;
- stops on timeout, CUDA error, malformed report or new event;
- never retries automatically.

A passed model smoke is still not permission for a full GPU production render. The next decision must be made from the report, WAV, peak VRAM and event log.

## Events that block further testing

```text
nvlddmkm: 14, 153
Display: 4101
Microsoft-Windows-WHEA-Logger: 17, 18, 19, 46, 47
stage timeout
nonzero child-process exit
JSON report status other than passed
screen freeze, corruption or driver reset
```

## Logs

Synthetic probation runs:

```text
C:\AI-Archive\RTX3060-CUDA-PROBATION\<timestamp>
```

Model smoke runs:

```text
C:\AI-Archive\RTX3060-VOXCPM2-MODEL-SMOKE\<timestamp>
```

Retain failed reports, stdout, stderr and exact PowerShell output. Do not rerun after a failure until it has been classified.
