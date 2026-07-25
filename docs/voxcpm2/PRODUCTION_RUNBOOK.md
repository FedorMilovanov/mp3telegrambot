# VoxCPM2 production runbook

This runbook turns the MacArthur laboratory work into a repeatable Windows CPU workflow. It is intentionally stricter than the early experiment scripts: all folders and references are prepared automatically, a lightweight preflight runs before model loading, and PowerShell launchers are parsed in GitHub Actions.

## Current accepted synthesis baseline

The last owner-approved baseline is **V3.2 NoChew**:

```text
Device:            CPU only
CUDA visible:      False
Clone mode:        reference-only
Segments:          4 thought-sized blocks
Segments 1-3:      B extended 24-second reference
Segment 4:         C composite reference
min_len:           natural default on first attempt
Short output:      never slowed down
Tail restart:      detected and conservatively removed
English bed:       constant gain; no sidechain
```

Owner review of V3.2:

- phrase endings were complete;
- the repeated chewing/swallowed-word defect was no longer audible;
- speech was substantially better than previous versions;
- remaining work is final candidate quality, speaker similarity, room coloration and publication mix.

Do not regress to:

- `min_len` derived from 90%+ of the subtitle window;
- slowing short output to fill the whole window;
- end fades that touch spoken consonants;
- seven very short independently generated fragments;
- Ultimate mode as the English-to-Russian default;
- speech-triggered sidechain ducking of the original speaker.

## Volume terminology: reduction is not final gain

This caused a real production misunderstanding and must be stated numerically.

| Spoken request | Correct linear gain | Approximate change |
|---|---:|---:|
| “reduce original by 25%” | `0.75` | `-2.50 dB` |
| “leave original at 25%” | `0.25` | `-12.04 dB` |
| “reduce original by 22%” | `0.78` | `-2.16 dB` |
| “reduce original by 30%” | `0.70` | `-3.10 dB` |

The current publication intent is interpreted as:

```text
Russian voice: 100%
Original English: constant 70-78% of its source level
Default: 75%
Sidechain: disabled
```

Parameter names must say `OriginalGain`, not an ambiguous `OriginalLevel` or “minus percent”.

## Production files

Repository:

```text
tools/voxcpm2/production_preflight.py
tools/voxcpm2/windows/Run-MacArthur-Final-CPU.ps1
tools/voxcpm2/windows/Remaster-MacArthur-Constant-Gain.ps1
tools/voxcpm2/examples/macarthur_raasabpj_iw/segments_ru_final.json
tools/voxcpm2/examples/macarthur_raasabpj_iw/subtitles_ru_final.srt
```

Local accepted package:

```text
C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL
```

Local work root:

```text
C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL
```

The self-contained repository launcher:

1. creates all required work directories;
2. reuses a previously downloaded source video when available;
3. downloads the source only when no local copy exists;
4. creates B and C references when missing;
5. runs production preflight;
6. launches CPU-only synthesis;
7. creates a constant-gain mix and two-pass master;
8. copies final subtitles.

## Preflight contract

`production_preflight.py` runs without importing torch or VoxCPM. It blocks expensive model loading when any of the following is wrong:

- Python executable missing;
- package scripts missing or syntactically invalid;
- FFmpeg/ffprobe missing;
- source video missing or unreadable;
- final segments JSON invalid or outside source duration;
- B/C references missing;
- model snapshot missing;
- less than the configured free-disk threshold;
- `CUDA_VISIBLE_DEVICES` is not exactly `-1`.

A successful preflight writes:

```text
<work-root>\logs\production_preflight.json
```

## First production render

From a clone of the repository:

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

Note: the historical launcher parameter is still named `OriginalLevel`; pass `0.75` explicitly. A future API cleanup should rename it to `OriginalGain` with backward compatibility.

## Remaster without regenerating speech

When the Russian timeline already exists, never rerun VoxCPM2 merely to change the English bed.

```powershell
.\tools\voxcpm2\windows\Remaster-MacArthur-Constant-Gain.ps1 `
    -PackageDir "C:\AI-Archive\MacArthur_Shorts_VoxCPM2_CPU_FINAL" `
    -WorkRoot "C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-FINAL" `
    -OriginalGain 0.75 `
    -OpenOutput
```

This runs only FFmpeg/loudness mastering. It preserves the accepted Russian synthesis and produces a gain-tagged mixed MP4.

Suggested listening comparison:

```text
OriginalGain 0.70
OriginalGain 0.75
OriginalGain 0.78
```

Do not compare all three through a new synthesis run. The voice track must remain identical so only the mix ratio changes.

## Publication acceptance gate

A final MP4 is not publication-ready until all checks pass:

### Speech content

- every intended Russian sentence is present;
- no swallowed final word or consonant;
- no pause-then-chewing restart;
- no repeated syllable or hallucinated word;
- theological terminology is accurate;
- subtitle wording matches the final spoken meaning.

### Voice and prosody

- speaker identity is acceptably close to MacArthur;
- first three blocks retain B’s stable delivery;
- final block has a convincing terminal cadence;
- no abrupt prosodic reset feels like a different speaker;
- no excessive English-accent leakage.

### Audio engineering

- compare Russian-only before blaming the mix for echo;
- original English remains at one constant gain;
- no sidechain pumping;
- no clipping;
- final master report exists;
- integrated loudness and true peak match the chosen publication target;
- beginning and ending are not clipped;
- media duration matches the source.

### Reproducibility

- synthesis JSON report retained;
- preflight JSON retained;
- master JSON retained;
- exact steps, CFG, references and seeds recorded;
- selected candidate per segment recorded;
- source URL and source duration recorded.

## Repository quality controls

The dedicated workflow:

```text
.github/workflows/voxcpm2-windows.yml
```

performs:

- Python compilation for every VoxCPM2 tool;
- PowerShell AST parsing for every launcher;
- lightweight VoxCPM2 regression tests;
- fatal Ruff checks.

A launcher with an unmatched parenthesis, broken quote or encoding-induced parse error must fail CI before being shared.

## Next optimization priorities

In order of expected value:

1. finish and listen to the current final candidate run;
2. remaster the identical Russian WAV at gains 0.70, 0.75 and 0.78;
3. add ASR completeness scoring per generated candidate;
4. add endpoint phoneme/consonant confidence rather than energy-only detection;
5. find a cleaner close-mic 15-25 second MacArthur reference;
6. compare CFG 1.55/1.75/1.95 on one ending-sensitive phrase;
7. compare Steps 10/16 only after CFG is fixed;
8. add resumable per-segment manifests and selected-segment regeneration;
9. compare the accepted VoxCPM2 output against Chatterbox Multilingual V3 and Qwen3-TTS on the same Russian text/reference.

The largest remaining quality gains are more likely to come from candidate selection, reference acoustics and objective content checking than from blindly increasing diffusion steps.
