# Universal VoxCPM2 dubbing hardening

This change is project-wide. It contains no video ID, speaker name, quotation,
or one-off SRT text.

## Runtime invariants

- Final semantic TTS blocks are checked before voice-reference preparation and
  model loading.
- Physically overloaded text fails with the exact segment, speech slot and
  required tempo instead of spending hours on deterministic candidates.
- Candidate progress is separate from model-internal `0/86..86/86` tqdm.
- Retry epochs are scoped to the exact text, timing, model/profile and prepared
  reference fingerprint. Editing an SRT starts a clean scope.
- One unchanged scope is capped at three failed synthesis epochs.
- Candidate budgets are adaptive: safe blocks stop after at most three attempts;
  difficult blocks retain the full rescue profiles.
- Existing hard gates, the `1.36x` fit ceiling and the no-best-of-bad rule remain
  authoritative.
- Runtime fingerprints include the active package facade, backend adapter,
  compatibility base snapshots and universal hardening modules.

## Compatibility design

The previous audited implementations are preserved as immutable `_base.py`
snapshots. Public module paths stay unchanged and install universal invariants
at import time. This keeps current entrypoints, monkeypatch seams and project
fingerprints compatible while making the behavior available to every future
ready-SRT direct dubbing job.

## Acceptance boundary

Hosted tests verify contracts and orchestration without loading model weights.
A real Windows CPU run remains required for weighted synthesis and listening QA.
