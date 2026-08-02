# Universal VoxCPM2 dubbing hardening

This runtime is project-wide. It contains no video ID, speaker name, quotation,
or one-off subtitle text.

## Production invariants

- Final semantic TTS blocks are validated before voice-reference preparation and
  model weight loading.
- IDs and timing values are type-strict: booleans, fractional IDs, non-finite
  values, duplicate IDs, overlaps, impossible tail guards and inconsistent
  `speech_slot` values fail before synthesis.
- Text that cannot fit the exact speech slot fails with segment-level density and
  measured tempo evidence instead of spending hours on deterministic candidates.
- Retry epochs are scoped to the exact text, timing, model/profile, prepared
  reference, current renderer code and the production runtime-contract marker.
- A changed SRT, reference, model snapshot, installed VoxCPM runtime or renderer
  implementation starts a clean retry scope.
- Repeated measured timing failures are persisted. An unchanged blocked repeat
  fails immediately and does **not** consume another retry epoch.
- Newly measured timing or adaptive-budget failures advance the exact scope once.
- One exact synthesis scope is capped at three failed epochs. Explicit counters
  are reconciled with retained history using the highest safe value.
- Candidate budgets remain adaptive: safe blocks use at most three attempts,
  while difficult first-epoch blocks keep all five rescue profiles.
- Existing hard quality gates, the 1.36x fit ceiling and the no-best-of-bad rule
  remain authoritative.

## Resume, cache and speed invariants

- The backend session is lazy. A checkpoint-only resume does not load VoxCPM2
  weights; the real session opens immediately before the first missing segment.
- Loaded audio specifications are validated again at the lazy boundary. Invalid
  sample rates, cache sizes or `seconds_per_step` fail closed.
- A failed capability probe disables continuation context rather than assuming it
  is supported.
- Prepared references may be reused only when source/output SHA-256, sample rate,
  duration, pitch/activity, clipping, spectral evidence, cache schema and the
  active reference-analysis implementation all match.
- Legacy reference reports are re-analysed once instead of being trusted blindly.
- File hashes are accepted only when file size and modification time remain stable
  throughout fingerprinting.
- Model-internal tqdm (`0/86..86/86`), including ANSI-coloured output, is not
  interpreted as project progress.
- Candidate progress is emitted with segment, attempt, exact-scope epoch and risk
  band.

## Retry diagnostics

The retry JSON keeps both counters:

- `raw_retry_epoch` is the append-only diagnostic counter for the segment;
- `scope_retry_epoch` / `last_scope_epoch` is the actual epoch for the exact
  synthesis input and is the value shown to the operator.

Changing another scope no longer makes the displayed retry number jump for the
current text.

## Compatibility and evidence

The previously audited implementations remain immutable `_base.py` snapshots.
Public import paths stay stable and install these invariants at import time.
Runtime fingerprints include the active facades, base snapshots, candidate and
cadence analysis, pronunciation/monolith code, retry/recovery contracts,
renderer wrapper and speech-backend planning modules.

Focused tests verify timing, marker tamper handling, retry accounting, lazy-session
validation, versioned reference caching, runtime-marker binding, progress and
recovery contracts without loading model weights. A real Windows CPU render and
listening QA remain required before claiming weighted synthesis acceptance.
