import ast
from pathlib import Path
from types import SimpleNamespace

from services import gemini_analyze


def test_gemini_analyze_audio_logs_observability_events():
    source = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"alog_gemini_response", "alog_gemini_run"}
    ]
    assert len(calls) >= 4
    assert 'task="audio_analysis"' in source
    assert "json_valid=parsed is not None" in source
    assert 'error="max_tokens"' in source
    assert 'error="empty_response"' in source


def test_gemini_analyze_masks_errors_before_observability_logging():
    source = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    assert "safe_err = _mask_api_key(str(e))" in source
    assert 'error=f"{err_type}: {safe_err[:300]}"' in source


def test_finish_reason_logging_survives_malformed_response(caplog):
    class BrokenResponse:
        @property
        def candidates(self):
            raise RuntimeError("malformed response")

    caplog.set_level("DEBUG", logger=gemini_analyze.__name__)
    gemini_analyze._log_gemini_finish_reason(SimpleNamespace(candidates=[SimpleNamespace(finish_reason="STOP")]))
    gemini_analyze._log_gemini_finish_reason(BrokenResponse())

    assert "finish_reason=STOP" in caplog.text
    assert "finish-reason inspection failed" in caplog.text
