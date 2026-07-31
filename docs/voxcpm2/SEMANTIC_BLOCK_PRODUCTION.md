# Semantic-block direct production

## Policy

`semantic-block-continuation-v1` is the direct ready-SRT production policy for
long-form monologue quality.

It deliberately does not use one giant unbounded model call and does not render
each short SRT cue as an independently acted phrase. The synthesis unit is a
contiguous semantic block:

- hard minimum: 7 seconds when the source permits it;
- target: 10.5 seconds;
- hard maximum: 15 seconds;
- maximum internal source gap: 1.2 seconds;
- the last short block is balanced against the previous block where possible.

Each block is one complete candidate. Candidates are never mixed sentence by
sentence. A failed block is regenerated as a complete block. When the backend
exposes VoxCPM2 prompt conditioning, the previous accepted block is supplied as
`prompt_wav_path` with its exact text while the fixed calm anchor remains the
identity reference. This is continuation context, not a replacement enrollment.

## Subtitle and synthesis separation

The SRT remains cue-level. The renderer receives block-level text and timing,
while the delivery SRT keeps the original cue texts and cue boundaries. This
prevents subtitle readability from forcing the TTS model back into one-sentence
mini-generations.

## Identity

Every direct production block uses the same calm `extended` enrollment. The
controlled expressive reference is not selected by block number and cannot
silently become a second speaker identity.

## Source prosody

The English/source F0 and energy contour can be recorded for diagnostics, but
for this policy they do not participate in candidate ranking and do not widen
speaker-identity limits. Russian cadence, pronunciation, noise, tail and
post-AAC gates remain active.

## Fallback

A fallback regenerates the whole semantic block. It must not splice a sentence
from another candidate or switch TTS engines inside a block. If a different
engine is ever benchmarked, re-render the full neighboring block or the full
clip and repeat the same identity and delivery QA.

## Release boundary

The policy is included in the renderer fingerprint and worker release
`dub-worker-quality-v6.6`. Existing checkpoints from the phrase-level policy
must be treated as incompatible.
