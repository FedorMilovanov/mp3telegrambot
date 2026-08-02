# Universal VoxCPM2 dubbing hardening

This runtime is project-wide. It contains no video ID, speaker name, quotation,
or one-off subtitle text.

## Production invariants

- Final semantic TTS blocks are validated before voice-reference preparation and
  model weight loading.
- Text that cannot fit the exact speech slot fails with segment-level density and
  tempo evidence instead of spending hours on deterministic candidates.
- Retry epochs are scoped to the exact text, timing, model/profile, prepared
  reference and active renderer implementation.
- A changed SRT, reference, model or runtime starts a clean retry scope.
- Repeated measured timing failures are persisted. An unchanged blocked repeat
  fails immediately and does **not** consume another retry epoch.
- Newly measured timing or adaptive-budget failures advance the exact scope once.
- One exact synthesis scope is capped at three failed epochs; retained history is
  diagnostic and is not the sole enforcement mechanism.
- Candidate budgets remain adaptive: safe blocks use at most three attempts,
  while difficult first-epoch blocks keep all five rescue profiles.
- Existing hard quality gates, the 1.36x fit ceiling and the no-best-of-bad rule
  remain authoritative.

## Resume and speed invariants

- The backend session is lazy. A checkpoint-only resume does not load VoxCPM2
  weights; the real session opens immediately before the first missing segment.
- Prepared references may be reused only when source/output SHA-256, sample rate,
  duration, pitch/activity, clipping and spectral evidence all match.
- Reference cache mismatches fail open to a full re-analysis, never to blind reuse.
- Model-internal tqdm (`0/86..86/86`), including ANSI-coloured output, is not
  interpreted as project progress.
- Candidate progress is emitted with segment, attempt, exact-scope epoch and risk
  band.

## Compatibility and evidence

The previously audited implementations remain immutable `_base.py` snapshots.
Public import paths stay stable and install these invariants at import time.
Runtime fingerprints include the active facades, retry/recovery contracts,
renderer wrapper and speech-backend execution/profile modules.

Hosted tests verify timing, retry, marker, lazy-session, reference-cache,
progress and recovery contracts without loading model weights. A real Windows
CPU render and listening QA remain required before claiming weighted synthesis
acceptance.
