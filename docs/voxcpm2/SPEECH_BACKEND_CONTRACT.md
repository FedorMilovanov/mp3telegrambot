# Replaceable speech backend contract

Production orchestration owns timing, semantic QA, speaker QA, checkpoints,
source-bed policy and final media QA. It does not own a model-specific command
line shape.

A backend adapter owns only:

1. model discovery and identity;
2. runtime paths/interpreter;
3. renderer command construction;
4. master command construction;
5. child-process environment policy;
6. low-level synthesis session/model call;
7. declared capabilities.

The generic clean core calls `build_renderer_command()`,
`build_master_command()` and `process_environment()` through
`services.speech_backends.SpeechBackend`. The direct candidate loop obtains an
engine session through `open_session()` and calls its neutral `generate()`
method; the generic direct and translation runtimes call
`_run_speech_and_master()` rather than binding orchestration to a model name.
VoxCPM2-specific
`VoxCPM.from_pretrained`, KV-cache setup and `model.generate()` keyword mapping
live in `services/speech_backends/voxcpm2.py`.
VoxCPM2 is currently the only registered implementation, but a future backend
must not require changes to the timeline, block grouping, checkpoint schema,
source prosody policy or AAC/source-bed QA.

Backend-specific options must stay inside the adapter. The durable project request
contains only the canonical backend selector plus stable concepts such as
references, text/timing input, deterministic seed, output paths and QA
destinations. `generic_project_runtime.validate_request_payload()` resolves
aliases and writes the canonical `speech_backend` id; unknown engines fail before
queueing or preflight.

The process environment is also backend-owned. CPU-only flags, CUDA visibility,
Hub offline mode, thread variables and model-specific cleanup variables must not
be assembled in `clean_production_core.py`. The adapter returns a
`BackendProcessEnvironment` under `speech-backend-process-environment-v1` and
the core applies it without interpreting model-specific values.

Backend fingerprints include the adapter implementation, selected backend
identity and environment/command policies. Changing the backend invalidates
incompatible checkpoints before rendering.
