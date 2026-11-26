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

### Read-only follow-up: WHEA, crash classes and firmware identity (2026-11-26)

- PCI IDs from WMI/NVIDIA: 10DE:2504, subsystem 1043:884D, current VBIOS 94.06.25.00.CB, Windows driver 32.0.16.2140. PnP problem code 0; PCIe x16, Gen1 idle / Gen4 max; replay and rollover counters again zero. ASUS [official model page](https://www.asus.com/motherboards-components/graphics-cards/tuf-gaming/tuf-rtx3060-o12g-gaming/) confirms a physical Dual BIOS switch. The mismatch with the historically reported 94.06.2F.40.67 could be a different selected ROM, but switch position and both ROMs are not verified. Do not flash or change the switch while powered.
- System event log retention starts in July 2026; the historical March 0x10E is outside that window. One retained SystemErrorReporting 1001 dated September 12 records 0x7E with first argument 0xC0000005 (access violation). Faulting module unresolved without dump. No debugger present in checked PATH/SDK locations; remote process is not elevated, dump directories return access denied. CrashDumpEnabled=3, AutoReboot=0, LogEvent=1, Overwrite=1. No privilege, ACL or registry changes.
- The one retained WHEA-Logger event (ID 3, November 12 08:03:52 MSK) was parsed in-place using bounded CPER header/section offsets from [UEFI 2.10 Appendix N](https://uefi.org/specs/UEFI/2.10/Apx_N_Common_Platform_Error_Record.html). CPER length 316, record and section severity 3 (informational), notification GUID 3d61a466-ab40-409a-a698-f362d464b38f (Boot); one section of length 116 at offset 200, GUID 93a41c2f-a09f-e7c2-ac1f-f2488f03eec3. This is not the standard PCIe section GUID d995e954-bbc1-430f-ad91-b44dcb3c6f35. Unknown vendor payload not decoded; do not treat this event as PCIe AER evidence or declare it definitively benign. Historical HWiNFO Recovery Count remains a separate lead.
- Last 90 days of retained Application WER: 710 records of code 141 without named bucket and 504 with LKD_0x141_Tdr:6_IMAGE_nvlddmkm.sys_Ampere; three records of code 193 (two without bucket, one dxgkrnl live dump). Counts are report records, not unique incidents. The currently retained signature is Tdr:6; earlier Tdr:C is operator-reported. Do not infer the meaning of the suffix without driver/dump evidence.
- Last 90 days of Kernel-Power 41: seven BugcheckCode=340 (0x154 UNEXPECTED_STORE_EXCEPTION), two 239 (0xEF CRITICAL_PROCESS_DIED), two zero, one 268435582 (0x1000007E). [Microsoft Event 41](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/event-id-41-restart) stores decimal codes; [0x154](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/bug-check-0x154--unexpected-store-exception), [0xEF](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/bug-check-0xef--critical-process-died) and [0x7E](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/bug-check-0x7e--system-thread-exception-not-handled) are not GPU-specific. Do not attribute every reboot to the GPU.
- Separate workstation reliability lead: one disk event 154 on November 6 for Disk 2 (hardware I/O error), two NTFS 130 repair records for G: on November 12, nine volmgr 161 and one volmgr 162 in the 90-day query. Current Disk 2 is a WDC WD1002FAEX SATA HDD hosting G:/H:, while system C: is on a different NVMe drive. Historical disk number must be corroborated before attributing the I/O event to a physical device. All four disks reported Healthy/OK by Windows, but detailed Get-StorageReliabilityCounter returned PermissionDenied. Health status does not exclude bad sectors or link faults. Treat storage as a parallel issue, not established explanation of CUDA/TDR. Prioritize existing-data backup and read-only SMART/transport diagnostics with owner-authorized access; no write-heavy disk scan or repair while data is at risk.

No new GPU kernel, memory stress, firmware write, power or clock changes were made in this follow-up. CUDA production HOLD remains unchanged. Next high-value evidence is a local authorized elevated read-only dump analysis, physical confirmation of Dual BIOS state and independent electrical measurements; do not repeat crash workloads to obtain more of the same symptoms.

### Primary historical HWiNFO telemetry recovered (read-only, 2026-11-26)

Located original CSV recorded 2026-03-24, 04:54:12.649–05:37:31.945 local time, 3,162,380 bytes, SHA-256 fd0d8e2d7a47ccc0b8617ea9c0849c92148a580d4e7c5fa2bca9a771b6c54112. Parsed 1,300 valid data rows, 556 columns, ~2 s cadence. Raw CSV retained outside Git. The time window overlaps the operator-reported WATCHDOG-20260324-0458 filename; exact crash second remains unknown.

- Recovery Count: 53 → 154 (+101), 82 sampled intervals with increments. Of these, 77 coincided with a sampled PCIe link-speed change (2.5/5/16 GT/s); five had the same speed at both samples, which does not exclude intermediate retraining. Across all 1,299 intervals, all 77 sampled speed changes coincided with an increment. Receiver, Replay, Replay Rollover, LCRC, Correctable/Non-Fatal/Fatal and lane 0–15 error counters were zero throughout. Recovery is used for PCIe retraining/speed changes as well as error recovery ([PCI-SIG LTSSM FAQ](https://pcisig.com/faq?keys=LTSSM)); the observed counter is therefore not independent evidence of a PHY defect. Do not equate it with an AER error count.
- Across the full session GPU power 11.73–50.856 W, temperature 39.1–47.5 C, hotspot 49.1–58.1 C. Lowest sampled board +12 V 11.712 V, GPU PCIe 11.559 V, 8-pin 11.601 V, Misc0 11.537 V, all at 04:58:22.728. These March readings do not independently confirm the later reported post-reball 10.4–10.5 V minima.

| Local time | Board 12 V | GPU slot V | 8-pin V | Misc0 V | GPU W | GPU clock MHz | Core load % | Recovery | Link GT/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 04:58:18.720 | 11.856 | 11.727 | 11.730 | 11.627 | 23.977 | 1837 | 7 | 67 | 16 |
| 04:58:22.728 | 11.712 | 11.559 | 11.601 | 11.537 | 27.994 | 1837 | 5 | 69 | 16 |
| 04:58:24.736 | 11.808 | 11.682 | 11.644 | 11.606 | 46.427 | 1882 | 100 | 69 | 16 |
| 04:58:26.742 | 11.856 | 11.735 | 11.709 | 11.622 | 50.856 | 1882 | 4 | 69 | 16 |
| 04:58:28.736 | 11.976 | 11.835 | 11.905 | 11.842 | 42.274 | 330 | 0 | 71 | 2.5 |
| 04:58:34.747 | 11.976 | 11.931 | 12.067 | 11.983 | 27.005 | 382 | 0 | 74 | 2.5 |

Correlated board and GPU sensor dips merit external PSU/cable and board-power-path measurement, even though the technician used another PSU. Sensor readings are not calibrated probes, and their ~2 s sampling and possibly asynchronous updates cannot establish transient chronology, causality or the precise TDR moment. This is evidence against overconfident localization to PCIe or VRM alone, not proof of PSU failure.

### Storage/crash correlation follow-up (read-only)

All seven retained 0x154 Kernel-Power 41 records and both 0xEF records had volmgr 161 within three minutes; 0x1000007E had volmgr 162. No nvlddmkm 153 in those same +/-3 minute reboot windows; reboot timestamps are not exact crash timestamps, so absence does not exclude earlier GPU activity. volmgr 161 reported HarddiskVolume3 and BugCheckProgress 0x00040049 or 0x00060049 in checked samples. Volume-device mapping via fltmc denied; do not identify HarddiskVolume3 from partition number alone. Pagefile on C: allocated 49152 MiB, current usage 167 MiB at sample; C: free 26.48 GiB. SMART WMI FailurePredictStatus/Data denied, no smartctl or CrystalDiskInfo found in checked PATH/install locations; HWiNFO 8.46 and GPU-Z 2.69 installed. No admin access, SMART diagnosis remains open. Prioritize backing up unique G:/H: data and obtaining authorized read-only SMART raw attributes before any disk stress or repair. No GPU stress or hardware/software configuration changes in this wave.

### Recovered June report and July primary experiment artifacts (read-only, 2026-11-26)

Original June 10 diagnostic report records driver 595.79, Toolkit 12.6.85, 170 W PL, 28 nvlddmkm events in its preceding seven-day window, and several WER Python records. The retained Application log for June 9–11 independently returned 22 LiveKernelEvent 141 report records: 10 with bucket LKD_0x141_Tdr:C_IMAGE_nvlddmkm.sys_Ampere and 12 without a named bucket. Thus the older Tdr:C signature is now directly corroborated; report records need not represent distinct incidents.

**Legacy diagnostic methodology defect:** the original report prints two ECC alerts for [N/A], matching current root diagnose_gpu.ps1 logic (literal N/A comparison misses brackets). Unsupported ECC telemetry is not a positive error count. Also, dmon is passive sampling, not a CUDA compute stress test; GPU utilization of zero during passive monitoring does not establish a reset. The memory section queries free/used capacity, not malloc/fill/readback integrity. The PyTorch section calls current_device without guarding cuda availability and can confound a CPU build or import failure with an absent/broken GPU. Treat these historical labels/verdict as invalid evidence; actual event counts remain useful. Do not execute this legacy script under the current HOLD because it can attempt CUDA matmul automatically. Future maintenance should separate passive telemetry, unsupported counters, skipped probes, and explicitly authorized bounded compute tests. No script/runtime changes were made in this research wave.

Read 22 existing structured stage reports from the RTX3060 July archive without rerunning anything: 15 passed, seven failed. These are stage/report counts, not independent trial counts. Five failed reports contain actual CUDA/cuBLAS exceptions; two are software/harness errors (unexpected seed keyword and uninitialized latent_pred) and must not be counted as hardware evidence. Two early empty probation folders and an incomplete recovery folder were not counted as runs.

| July 27 local time / run | Primary report result |
|---|---|
| 00:58:19 | Load-only PASS, 10.449 s load; report shows free 6,751,780,864 of 12,884,377,600 bytes after load, but allocator counters zero, so do not equate allocator counters to total GPU use |
| 01:03:35 | One-step generation, torch 2.7.1+cu126, illegal memory access |
| 01:23:37 | Zero-shot no reference, torch 2.7.1+cu126, BF16 CUBLAS_STATUS_INTERNAL_ERROR |
| 01:29:10 | Isolated BF16 GEMM PASS: m/k/n 32/1024/4096, 256/1024/4096, 128/2048/8192, four repeats each; reported shapes and checksums, not a full numerical reference comparison |
| 01:33:19 | Zero-shot torch 2.11.0+cu126, illegal memory access |
| 01:38:13 | TypeError unexpected seed keyword — software failure |
| 01:42:52 | UnboundLocalError latent_pred — software failure |
| 01:48:17 | BF16 CUBLAS_STATUS_EXECUTION_FAILED, torch 2.11.0+cu126, cuDNN disabled, no reference |
| 02:02:20 | FP16 CUBLAS_STATUS_EXECUTION_FAILED, torch 2.11.0+cu126, cuDNN disabled, no reference |

All seven failed generation reports recorded the same post-load allocation 5,027,139,072 bytes and reservation 5,085,593,600 bytes, with fraction limit 0.70. This weakens simple model-load OOM as an explanation; it does not exclude transient allocation pressure during generation. The retained System log has 14 nvlddmkm 153 records across July 26–28; exact per-run correlation was not established in this query. The 20.023 s FP32 synthetic stage passed. Together the primary records corroborate workload-dependent instability across PyTorch versions and FP16/BF16, rather than a blanket inability to execute CUDA. No causal component localization.

### Storage follow-up: current volume mapping

Non-elevated read-only QueryDosDeviceW returned C: -> HarddiskVolume3, G: -> HarddiskVolume14, H: -> HarddiskVolume15, PhysicalDrive2 -> Harddisk2/DR2. Therefore the currently mapped device in volmgr 161 is the system C: volume (NVMe), not the separate G:/H: HDD. Historical mapping at the event date is not independently established. The disk 154 record contains Harddisk2/DR2, matching current WDC HDD mapping, subject to the same historical-number limitation. Keep both storage paths in the investigation: system NVMe/pagefile/dump writer and separate HDD I/O/NTFS repair. Current C: pagefile allocation 49,152 MiB, usage 167 MiB, free disk space ~26.5 GiB; insufficient pagefile size is not established. SMART WMI FailurePredictStatus/Data denied. HWiNFO and GPU-Z installed; no additional post-reball sensor CSV identified in bounded top-level directory search. Do not infer absence elsewhere. No new stress tests, disk writes, registry or permission changes.

### Exact CUDA/TDR correlation and storage-channel evidence (2026-11-26)

Correlated the original July 27 JSON run completion timestamps with retained System and Application event records. All times local MSK. Each of the five CUDA/cuBLAS failures has two nvlddmkm 153 events and two subsequent WER 141 report records (not necessarily distinct incidents).

| Run failure | JSON finish | First NVIDIA 153 | Second NVIDIA 153 | System RecordIds | First WER 141 |
|---|---|---|---|---|---|
| One-step illegal address | 01:03:56.272 | 01:03:53.833 | 01:03:57.038 | 46957, 46958 | 01:04:14.606 |
| BF16 cuBLAS internal error | 01:24:01.074 | 01:24:00.834 | 01:24:01.507 | 46961, 46962 | 01:24:06.000 |
| Torch 2.11 illegal address | 01:33:52.422 | 01:33:52.101 | 01:33:53.224 | 46963, 46964 | 01:33:57.313 |
| BF16 without cuDNN | 01:48:30.337 | 01:48:30.114 | 01:48:31.230 | 46967, 46968 | 01:48:35.331 |
| FP16 without cuDNN | 02:02:39.794 | 02:02:39.564 | 02:02:40.981 | 46970, 46971 | 02:02:44.151 |

All five second WER reports carry LKD_0x141_Tdr:6_IMAGE_nvlddmkm.sys_Ampere. No NVIDIA 153 or WER 141 in the corresponding execution windows of the two Python harness failures (01:38:13–24 and 01:42:52–01:43:05), or isolated BF16 GEMM PASS (01:29:10–11). This distinguishes application/harness errors from confirmed GPU subsystem timeouts. Correlation does not distinguish an initiating hardware defect from driver/invalid-workload causes.

Storage investigation:
- All seven retained 0x154 reboots and two 0xEF reboots carry BugcheckInfoFromEFI=true, nonzero parameter 1, parameters 2–4 zero. No exception record or process identity can be recovered from those fields alone; 0x154 parameter 2 normally points to exception information. Do not equate 0x154 with confirmed SSD failure.
- Current standard Microsoft SATA AHCI and NVM Express drivers both 10.0.26100.9974. System NVMe model GIGABYTE AG470S1TB-SI B10, firmware EIOM31.4; G:/H: WD1002FAEX firmware 05.01D05. No retained stornvme/storahci System events returned in 90-day query. StorPort provider query failed, rather than proving absence; Storport/Admin and ATAPort channels disabled. Storage-NvmeDisk/Operational and Kernel-StoreMgr/Operational enabled but empty at inspection. No new WHEA error/warning records returned in checked Kernel-WHEA channels for the 90-day interval.
- Storage-ClassPnP/Operational events 503 and 506, 2026-11-06 13:01:28.566, corroborate the exact Disk 154 timestamp: failed upper-level paging write and failed Write SCSI SRB. This confirms a real failed write in the storage stack, not a generic GPU symptom. Physical disk attribution remains conditional on historical device mapping.
- NTFS/Operational returned 46 warning/error records in 90 days: 34 ID 141 (disk full), six ID 147 (high latency I/O), six ID 149 (high latency/failure summaries). For C: on November 23 14:33:49, browser file open took 30,389 ms; 15:03:03 summary reports three high latency I/Os over 3,603 seconds, zero failed reads/writes. On November 22 19:59:20, C: operation failed because disk full: free 2,857,377,792 bytes, reserved 2,851,799,040 bytes, requested 1,400,832 bytes; pagefile size 51,556,384,768 bytes. Another C: disk-full record at 01:50:25 showed free 4,270,350,336 bytes, reserved 4,265,598,976 bytes. Available unreserved space was only a few MiB despite nominal free space. Historical space pressure and latency are confirmed, but physical NVMe failure and causal connection to BSOD/Commander remain unproven.
- Current Balanced power plan AC PCIe ASPM moderate, DC maximum. This is a configuration observation, not proof of faulty ASPM. No power plan changes. No dump archives found in bounded top-level search; admin-only dump access and SMART remain blocked.

No new stress tests or configuration changes. A further grouped NTFS/SRB query was still executing at last remote result check; no unreturned output was used.
