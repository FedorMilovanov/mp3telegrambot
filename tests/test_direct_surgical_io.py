from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from tools.voxcpm2 import direct_surgical_io as io
from tools.voxcpm2 import direct_surgical_polish_v2


direct_surgical_polish_v2.install_global_polish()


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_lazy_session_opens_only_for_length_planning() -> None:
    calls = []

    class Spec:
        encode_sample_rate = 16000
        output_sample_rate = 48000
        seconds_per_step = 0.08
        cache_length = 4096

    class Session:
        audio_spec = Spec()

        def generate(self, request):
            return request

    class Backend:
        backend_id = "voxcpm2"

        def capabilities(self):
            return SimpleNamespace(continuation_context=True)

        def open_session(self, config):
            calls.append("open")
            return Session()

        def plan_generation_length(self, spec, request):
            return spec.seconds_per_step

        def plan_generation_profile(self, request):
            return request

    backend = io.LazyBackend(Backend(), encode=16000, output=48000, log=lambda x: None)
    session = backend.open_session(SimpleNamespace(options={"cache_length": 4096}))
    assert calls == []
    assert backend.plan_generation_profile("profile") == "profile"
    assert calls == []
    assert backend.plan_generation_length(session.audio_spec, "request") == 0.08
    assert calls == ["open"]
    assert session.generate("audio") == "audio"
    assert calls == ["open"]


def test_reference_cache_requires_full_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    output = tmp_path / "guarded" / "extended.wav"
    output.parent.mkdir()
    source.write_bytes(b"source")
    output.write_bytes(b"output")
    entry = io.enrich_reference_report(
        {
            "sha256": digest(output),
            "sample_rate": 16000,
            "duration": 8.0,
            "voiced_ratio": 0.5,
            "active_ratio": 0.8,
            "max_internal_gap": 0.1,
            "clipping_ratio": 0.0,
            "spectral_envelope": {"frames": 10, "bands": [0.1]},
        },
        source=source,
        hash_file=digest,
    )
    (output.parent / "references.json").write_text(
        json.dumps({"extended": entry}),
        encoding="utf-8",
    )
    cached = io.cached_reference(
        source=source,
        output=output,
        hash_file=digest,
        expected_sample_rate=16000,
    )
    assert cached is not None
    assert cached["reference_cache_hit"] is True
    assert cached["reference_cache_schema_version"] == 2
    output.write_bytes(b"tampered")
    assert io.cached_reference(
        source=source,
        output=output,
        hash_file=digest,
        expected_sample_rate=16000,
    ) is None
