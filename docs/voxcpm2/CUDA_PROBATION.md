# RTX 3060 CUDA probation

Updated: 2026-11-26.

## Scope

The local RTX 3060 has a confirmed history of `nvlddmkm` Event ID 153, LiveKernelEvent/WATCHDOG, CUBLAS failures, illegal memory access and BF16/FP16 failures. Short successful jobs prove that the failure is not constant; they do not erase the historical incidents.

VoxCPM2 production synthesis remains CPU-only. CUDA is allowed only through the staged probation launchers documented here.

**2026-11-26 update:** the historical July stages below are not current authorization to run a GPU model smoke. See the consolidated record at the end; CUDA production and further stress testing remain HOLD pending independent repair/verification.

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

## Historical July plan: one VoxCPM2 model smoke (superseded by HOLD)

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

## 2026-11-26 — consolidated RTX 3060 diagnostic record

**Disposition: CUDA production HOLD; CPU-only contracts unchanged.** This is an evidence update, not a new runtime mode or permission to run further stress tests. Sources: operator's historical test and repair notes (not independently rerun), July probation and [postmortem](CUDA_RTX3060_POSTMORTEM_2026-07-27.md), direct read-only workstation observations and bounded diagnostic runs on 2026-11-26. Preserve raw evidence outside the public repository.

### Historical configuration and pre-reball evidence (operator-reported)

- ASUS TUF-RTX3060-O12G-GAMING, RTX 3060 12 GB LHR, GA106-302, Samsung GDDR6; Resizable BAR enabled. Used card.
- Two different platforms (i3-12100-class / DDR4 and i5-14600K / MSI B760 GAMING PLUS WIFI / 32 GB DDR5-6800) reproduced the defect; both initially used the same Thermaltake Smart SE 730W PSU. The repair technician reportedly tested on a separate bench/PSU; exact bench setup and waveforms were not provided.
- Windows 11 clean installation, DISM/SFC, multiple NVIDIA drivers (591.74, 595.71, Studio 595.79), clean driver install, PCIe Gen3, XMP disabled, PL 70%, core -200 MHz / memory -500 MHz did not resolve the defect.
- Reported original VBIOS 94.06.25.00.CC, then official ASUS update to 94.06.2F.40.67 without improvement. Current readback is different; see below. Do not infer firmware corruption or flash a ROM from this discrepancy.

| Historical test | Operator-reported result |
|---|---|
| FurMark ~15 min; OCCT VRAM | PASS, no reported VRAM errors |
| FFmpeg H.264 NVENC / NVDEC, 1080p60, 3600 frames | PASS, ~4.74x / ~12.7x |
| Heaven DX11 High 1680x1050 | PASS, score 6513, avg 258.6 FPS |
| Superposition 1080p High; Extreme OpenGL; 8K Optimized | PASS, scores 11257 / 4533 / 2778, max temperatures 77–79 C |
| Superposition VR Maximum DX/OpenGL | PASS, scores 9197 / 8121 |
| SPECviewperf 15 | maya, blender, catia, creo, energy, medical, snx passed; 3dsmax, solidworks, enscape (Vulkan DeviceLost), unreal_engine failed |
| DaVinci Resolve | Same clip opened without reported TDR, unlike CapCut |
| OCCT 3D Adaptive Light/Heavy | Crash about 5 s, before and after VBIOS update |
| 3DMark Fire Strike | GT2 failed / no results |
| Time Spy / Steel Nomad DX12 | CreateCommittedResource failure / DXGI_ERROR_DEVICE_HUNG (0x887a0006) |
| Witcher 3 | DX12 startup failure; DX11 intermittent application/system crash |
| CapCut | Black preview, UI stall, TDR; hardware decoding exacerbated symptom |

Historical logs include nvlddmkm 153, LiveKernelEvent 141, bugcheck 0x10E VIDEO_MEMORY_MANAGEMENT_INTERNAL, Kernel-Power 41, occasional volmgr 161, WHEA-Logger ID 3 and rising HWiNFO PCIe Recovery Count. These are reported observations, not decoded dump evidence. OCCT 3D Adaptive is a graphics workload, not proof of a CUDA-specific failure. Some failures occurred around 49 W / 50–60 C with sensor readings 11.54–11.61 V (slot), 11.5–11.7 V (8-pin); other gaming runs reached 97–99% utilization and 68–77 C with readings near 11.86–12.07 V.

### Post-reball evidence (operator-reported)

Reball changed time-to-failure, not the ultimate outcome. Light often stabilized (some runs much longer than 3 min); Heavy/Hard failures at 27, 32, 45, 50, 66, 78 s, up to ~226 s in reduced mode. Extreme stock reached 170 W / 100% / ~1732 MHz / 0.975 V / 58 C, then failed at ~26 s; reduced modes sometimes survived 2+ min. Core offset as low as -500 MHz and reduced PL extended runs but did not restore stability.

| Sensor | Idle | Minimum during reported heavy runs |
|---|---:|---:|
| PCIe slot +12 V | 11.92–11.96 V | ~11.15 V |
| 8-pin +12 V | 12.03 V | ~10.52–10.54 V |
| Misc0 input | 11.95–12.01 V | ~10.39–10.42 V |

Light sometimes recovered from a startup reading near 10.5 V to 11.7–11.8 V; softer Heavy modes often read 11.3–11.5 V at 8-pin. Recovery Count was reported around 478–484. Stock Extreme input power readings: slot 56.82 W, 8-pin 111.33 W, Misc0 21.74 W, NVVDD2 input 106.90 W; these channels can overlap and must not be summed as independent rails. Min/avg/max alone cannot establish event order or whether telemetry was valid during a reset. The technician's independent PSU test weakens, but does not rigorously eliminate, external power as a contributing factor.

### Direct observations, 2026-11-26 (MSK)

After reboot: Studio driver 621.40, reported CUDA driver support 13.5, installed Toolkit/nvcc 13.3.33, GPU 12 GiB, WDDM, VBIOS **94.06.25.00.CB**. Default Python 3.11.9 contained torch 2.12.0+cpu: torch.cuda.is_available() false is expected for this build, not a driver test. Do not reinstall it or change existing archived CPU/GPU environments. CUDA Driver API cuInit, cuDriverGetVersion (13050), cuDeviceGetCount (1) succeeded. Device PL 120 W vs default 170 W is intentional: existing logon task GPU-PowerLimit-120W executes nvidia-smi -pl 120. Leave it unchanged.

| ID | Diagnostic | Observed result |
|---|---|---|
| N01 | Context and 32 MiB cuMemsetD32 / DtoH exact verification | PASS, exit 0 |
| N02 | Handwritten PTX load | INVALID TEST: CUDA 218 invalid PTX; not evidence of GPU failure |
| N03 | NVRTC compile and custom CUDA pattern kernel; 512 MiB allocation, four patterns with exact readback | PASS, 2 GiB cumulative, 1.59 s, exit 0 |
| N04 | NVRTC pattern kernel; 6 GiB allocation; two large dispatches planned | First 6 GiB pattern PASS; second cuCtxSynchronize returned CUDA 700, cleanup also returned 700, exit 1 |
| N05 | 6 GiB cuMemsetD32, 32 MiB blocks, two patterns, exact DtoH compare | PASS, 12 GiB cumulative, 2.22 s; cleanup and exit 0 |
| N06 | NVRTC bounds-checked kernel, explicit ctypes signatures, 6 GiB allocation, 192 short dispatches per pattern, two patterns with exact readback | PASS, 12 GiB cumulative, 15.04 s, cleanup and exit 0 |

N04 temporally coincided with two nvlddmkm 153 records at 15:52:06–07 and a WER LiveKernelEvent 141 report. The initial kernel did not use explicit ctypes signatures or a per-thread bound; this is a test-design limitation, not proof of defective silicon or a TDR caused by one long kernel. N05/N06 are separate discriminating tests, not retries of N04. No additional nvlddmkm/Display/WHEA system events were detected after N05/N06 in the checked window through 16:01:12. Post-test temperatures 45–50 C are snapshots, not continuous hotspot maxima. The 14-day System query returned 167 nvlddmkm 153 records (historical incidents confirmed); WER reports can duplicate one incident. The first attempted remote extended diagnostic timed out at transport level; no execution/result was confirmed.

Further read-only inspection: PCIe x16/x16, Gen1 at idle, Gen4 maximum; nvidia-smi Replays Since Reset = 0 and Replay Number Rollovers = 0 at one sample. These counters are not the same as HWiNFO Recovery Count and cannot rule out intermittent link faults. TdrDelay, TdrDdiDelay, TdrLevel, HwSchMode were absent in the checked GraphicsDrivers registry key. No WHEA records were returned by the current 14-day query; historical WHEA reports remain unverified. Both system WATCHDOG and Minidump directories returned UnauthorizedAccessException; debugger not found in checked PATH/SDK locations. Dump existence and bugcheck parameters are unresolved, not negative findings.

### Interpretation and next discriminating evidence

- Short kernel and exact memory PASS do not establish stability of all physical VRAM, large dispatches, graphics, mixed engine workloads, or actual model inference. 6 GiB is the allocated test region, not all 12 GiB; cumulative bytes are not unique capacity.
- TDR 141 is an engine timeout; 0x10E is a video-memory-manager bugcheck; CUDA 700 is illegal address. None individually localizes a physical component. A reset may invalidate contexts and produce secondary CUDA/API errors.
- Prioritize electrical/board diagnostics: separate known-good PSU and native cables (confirm bench conditions), time-correlated scope measurements of actual 12 V at connector/board and VRM outputs NVVDD/FBVDD/PEX rails, ripple and phase behavior, inspect power path, current sense, connectors, filtering, PCIe signaling, GPU/BGA/package. Distinguish whether rail disturbance precedes the reset or follows it. Software sensor minima are leads, not calibrated oscilloscope readings.
- Next software evidence: authorized elevated **read-only** dump analysis (WinDbg bugcheck arguments, !analyze -v, WHEA records if present), verify VBIOS and physical BIOS switch position if applicable without flashing, compare PCIe counters against a same-run baseline. No unapproved privilege escalation or raw dump upload.
- Current native driver API results do not supersede July model-shaped CUBLAS failures. No further GPU stress, model loads, production CUDA, TDR changes, clock/voltage/PL changes, firmware updates or automatic retry without independent repair/verification and a separately authorized bounded test. Existing CPU INT8 large-v3 / VoxCPM2 CPU-only policy remains authoritative.

