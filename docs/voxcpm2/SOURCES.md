# VoxCPM2 source index

Curated primary-source reading list for the CPU dubbing pipeline. This is not a generic link dump: each item is tied to an implementation decision, failure mode or future integration task.

Last reviewed: 2026-07-25.

## Official VoxCPM documentation

1. [VoxCPM repository](https://github.com/OpenBMB/VoxCPM) — canonical code and project overview.
2. [Quick Start](https://voxcpm.readthedocs.io/en/latest/quickstart.html) — current Python/CLI entry points and explicit device selection.
3. [Installation](https://voxcpm.readthedocs.io/en/latest/installation.html) — supported Python/PyTorch ranges, CPU device forcing and `optimize=False` guidance.
4. [Usage Guide](https://voxcpm.readthedocs.io/en/latest/usage_guide.html) — prompt/reference modes, quality controls, denoising and generation parameters.
5. [VoxCPM 2 model guide](https://voxcpm.readthedocs.io/en/latest/models/voxcpm2.html) — reference-only cloning, 48 kHz output and VoxCPM2-specific behavior.
6. [FAQ and troubleshooting](https://voxcpm.readthedocs.io/en/latest/faq.html) — Windows/Triton and `torch.compile` compatibility guidance.
7. [Fine-tuning guide](https://voxcpm.readthedocs.io/en/latest/fine_tuning.html) — future LoRA/SFT direction; not required for current inference.
8. [Deployment index](https://voxcpm.readthedocs.io/en/latest/deployment/index.html) — official serving alternatives and ecosystem context.
9. [Version history](https://voxcpm.readthedocs.io/en/latest/version_history.html) — API changes across releases.
10. [VoxCPM ecosystem](https://voxcpm.readthedocs.io/en/latest/ecosystem.html) — CPU/ONNX/GGUF and other backends; evaluate separately before adopting.

## Canonical source code

11. [`src/voxcpm/core.py`](https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/core.py) — public `from_pretrained`, `generate`, prompt/reference checks, normalization and denoiser path.
12. [`src/voxcpm/model/voxcpm2.py`](https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/model/voxcpm2.py) — prompt-cache modes, stop head, `min_len/max_len`, autoregressive loop and 8192 default config.
13. [`src/voxcpm/modules/minicpm4/cache.py`](https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/modules/minicpm4/cache.py) — `StaticKVCache`, exact `KV cache is full` guard and prefill behavior.
14. [`src/voxcpm/modules/minicpm4/model.py`](https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/modules/minicpm4/model.py) — `setup_cache` and token-by-token forward path.
15. [`src/voxcpm/cli.py`](https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/cli.py) — current CLI arguments; useful for detecting differences from installed 2.0.3.
16. [`app.py`](https://github.com/OpenBMB/VoxCPM/blob/main/app.py) — official web-demo mode wiring and prompt UX.
17. [`pyproject.toml`](https://github.com/OpenBMB/VoxCPM/blob/main/pyproject.toml) — package metadata and dependency constraints.
18. [`scripts/train_voxcpm_finetune.py`](https://github.com/OpenBMB/VoxCPM/blob/main/scripts/train_voxcpm_finetune.py) — official training entry point for future experiments.
19. [`src/voxcpm/model/utils.py`](https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/model/utils.py) — generation seed and runtime device/dtype helpers.
20. [`src/voxcpm/utils/text_normalize.py`](https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/utils/text_normalize.py) — external text normalization behavior behind `normalize=True`.

## Model and release artifacts

21. [Hugging Face model card](https://huggingface.co/openbmb/VoxCPM2) — model scope, supported languages, limitations and usage example.
22. [Hugging Face model files](https://huggingface.co/openbmb/VoxCPM2/tree/main) — authoritative snapshot layout and file sizes.
23. [Hugging Face `config.json`](https://huggingface.co/openbmb/VoxCPM2/blob/main/config.json) — architecture, dtype, LM dimensions and snapshot-specific `max_length`.
24. [Hugging Face `tokenizer_config.json`](https://huggingface.co/openbmb/VoxCPM2/blob/main/tokenizer_config.json) — tokenizer metadata used by the model package.
25. [PyPI current project](https://pypi.org/project/voxcpm/) — current release history and package provenance.
26. [PyPI 2.0.3](https://pypi.org/project/voxcpm/2.0.3/) — exact installed release, upload date, hashes and source tag.
27. [VoxCPM paper, arXiv:2509.24650](https://arxiv.org/abs/2509.24650) — tokenizer-free architecture background and original evaluation.
28. [Apache-2.0 license](https://github.com/OpenBMB/VoxCPM/blob/main/LICENSE) — code/model usage obligations.

## High-value upstream issue evidence

Issues are primary observations, not automatically accepted fixes. Record whether a workaround is reproduced locally before promoting it into production.

29. [Issue #52 — long text, OOM/KV length](https://github.com/OpenBMB/VoxCPM/issues/52) — upstream advice to split very long text into separate inference calls.
30. [Issue #62 — `KV cache is full`](https://github.com/OpenBMB/VoxCPM/issues/62) — exact failure title matching the local run.
31. [Issue #136 — singing behavior](https://github.com/OpenBMB/VoxCPM/issues/136) — long/unstable generation symptom to include in listening QA.
32. [Issue #209 — extended batch hang](https://github.com/OpenBMB/VoxCPM/issues/209) — long-running session stability and state-accumulation risk.
33. [Issue #213 — word endings cut off](https://github.com/OpenBMB/VoxCPM/issues/213) — motivates first/last syllable acceptance checks.
34. [Issue #219 — speech-to-speech expectations](https://github.com/OpenBMB/VoxCPM/issues/219) — clarifies that prompt/reference inputs are conditioning, not arbitrary voice conversion.
35. [Issue #248 — non-CUDA platform inference failure](https://github.com/OpenBMB/VoxCPM/issues/248) — useful for platform-specific traceback comparison.
36. [Issue #256 — CLI `--device cpu`](https://github.com/OpenBMB/VoxCPM/issues/256) — version skew between docs and installed CLI.
37. [Issue #285 — accent control](https://github.com/OpenBMB/VoxCPM/issues/285) — evidence that accent control can be unreliable.
38. [Issue #293 — MiniCPM4 attention performance](https://github.com/OpenBMB/VoxCPM/issues/293) — cache/attention implementation analysis.
39. [Issue #296 — prefill length mismatch](https://github.com/OpenBMB/VoxCPM/issues/296) — prompt scaffolding and prefill alignment failure in serving.
40. [Issue #302 — long-form voice drift](https://github.com/OpenBMB/VoxCPM/issues/302) — source-level explanation for re-anchoring the reference on short segments.
41. [Issue #306 — cross-language accent training](https://github.com/OpenBMB/VoxCPM/issues/306) — language/accent transfer limitations.
42. [Issue #316 — CPU fallback for unsupported GPUs](https://github.com/OpenBMB/VoxCPM/issues/316) — current backend limits and CPU performance motivation.
43. [Issue #321 — Ultimate cloning accent leakage](https://github.com/OpenBMB/VoxCPM/issues/321) — supports reference-only as the first English-to-Russian A/B mode.
44. [Issue #323 — batch generation request](https://github.com/OpenBMB/VoxCPM/issues/323) — project demand for manifests, progress and batch segment handling.
45. [Issue #335 — training memory pressure](https://github.com/OpenBMB/VoxCPM/issues/335) — training-only resource warning; do not confuse with inference requirements.
46. [Issue #338 — vLLM-Omni parallelism](https://github.com/OpenBMB/VoxCPM/issues/338) — serving architecture and thread contention evidence.
47. [Issue #342 — cloning similarity/stability evaluation](https://github.com/OpenBMB/VoxCPM/issues/342) — motivates objective run reports beyond subjective listening.
48. [Issue #344 — memory growth on repeated generation](https://github.com/OpenBMB/VoxCPM/issues/344) — motivates one worker, explicit cleanup and long-session monitoring.
49. [Issue #357 — hallucination on very short utterances](https://github.com/OpenBMB/VoxCPM/issues/357) — avoid one-word synthesis segments.
50. [Issue #359 — prompt training semantics](https://github.com/OpenBMB/VoxCPM/issues/359) — continuation/Ultimate prompt behavior.
51. [Issue #360 — instruction text spoken aloud](https://github.com/OpenBMB/VoxCPM/issues/360) — style/control tags can leak into generated speech; verify ASR.

## PyTorch references used by the CPU path

52. [`torch.inference_mode`](https://pytorch.org/docs/stable/generated/torch.autograd.grad_mode.inference_mode.html) — disables autograd overhead during synthesis.
53. [`torch.set_num_threads`](https://pytorch.org/docs/stable/generated/torch.set_num_threads.html) — intra-op CPU thread control.
54. [`torch.set_num_interop_threads`](https://pytorch.org/docs/stable/generated/torch.set_num_interop_threads.html) — inter-op thread control and call-order restrictions.
55. [CPU threading and TorchScript inference notes](https://pytorch.org/docs/stable/notes/cpu_threading_torchscript_inference.html) — oversubscription and thread-pool behavior.
56. [`torch.compile`](https://pytorch.org/docs/stable/generated/torch.compile.html) — explains why optimization is a separate compatibility variable.
57. [`torch.nn.functional.scaled_dot_product_attention`](https://pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention.html) — attention path relevant to MiniCPM4 performance discussions.

## FFmpeg references used by timeline construction

58. [FFmpeg audio filters](https://ffmpeg.org/ffmpeg-filters.html#Audio-Filters) — canonical filter reference.
59. [`atempo`](https://ffmpeg.org/ffmpeg-filters.html#atempo) — per-segment duration fitting and supported factor range.
60. [`adelay`](https://ffmpeg.org/ffmpeg-filters.html#adelay) — place fitted segments at exact source timestamps.
61. [`amix`](https://ffmpeg.org/ffmpeg-filters.html#amix) — combine delayed segment tracks.
62. [`loudnorm`](https://ffmpeg.org/ffmpeg-filters.html#loudnorm) — EBU R128 normalization for final voice timeline.
63. [`sidechaincompress`](https://ffmpeg.org/ffmpeg-filters.html#sidechaincompress) — duck the English original under Russian speech.
64. [`apad`](https://ffmpeg.org/ffmpeg-filters.html#apad) — preserve exact timeline duration and silent gaps.
65. [`atrim`](https://ffmpeg.org/ffmpeg-filters.html#atrim) — enforce exact segment and final durations.
66. [`highpass`](https://ffmpeg.org/ffmpeg-filters.html#highpass) — conservative rumble removal for speech references.
67. [`lowpass`](https://ffmpeg.org/ffmpeg-filters.html#lowpass) — remove irrelevant high-frequency content before 16 kHz encoding.

## Hugging Face/offline cache references

68. [Hugging Face Hub environment variables](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables) — `HF_HUB_OFFLINE` and cache configuration.
69. [`snapshot_download`](https://huggingface.co/docs/huggingface_hub/guides/download#download-an-entire-repository) — local snapshot resolution and cache reuse.
70. [Cache system reference](https://huggingface.co/docs/huggingface_hub/guides/manage-cache) — avoid duplicating the 15+ GB model snapshot.

## Project-specific reading order for another AI

1. `HANDOFF_FOR_AI.md`.
2. `EXPERIMENT_LOG.md`.
3. `REFERENCE_AUDIO_PLAYBOOK.md`.
4. Official `core.py`, `voxcpm2.py`, `cache.py` and `model.py`.
5. Issues #52, #302, #321 and #344.
6. `INTEGRATION_PLAN.md`.
7. Current scripts under `tools/voxcpm2/`.

Do not use secondary benchmark posts as the source of truth for API signatures, supported devices or cache behavior. Prefer the installed package signature, official release tag and current official source, and explicitly document any version difference.
