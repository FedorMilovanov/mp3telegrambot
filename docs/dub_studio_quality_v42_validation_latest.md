# Dub Studio Quality v4.2 validation

- Result: **FAIL**
- Density-based sustained speech onset; 30 ms chirp regression.
- Compile/tests/Ruff: failure

.......F.F........                                                       [100%]
================================== FAILURES ===================================
__________ test_timing_qa_rejects_thirty_ms_chirp_before_real_speech __________

    def test_timing_qa_rejects_thirty_ms_chirp_before_real_speech() -> None:
        sample_rate = 16000
        audio = np.zeros(sample_rate, dtype=np.float32)
        chirp_start = int(sample_rate * 0.02)
        chirp_time = np.arange(int(sample_rate * 0.03), dtype=np.float32) / sample_rate
        audio[chirp_start : chirp_start + len(chirp_time)] = 0.30 * np.sin(2 * np.pi * 3500 * chirp_time)
        speech_start = int(sample_rate * 0.12)
        speech_time = np.arange(int(sample_rate * 0.70), dtype=np.float32) / sample_rate
        audio[speech_start : speech_start + len(speech_time)] = 0.12 * np.sin(2 * np.pi * 180 * speech_time)
    
        result = measure_timing_quality(audio, sample_rate)
    
>       assert result["passed"] is False
E       assert True is False

tests\test_dub_quality_v4.py:131: AssertionError
_____________ test_timing_qa_rejects_isolated_click_before_speech _____________

    def test_timing_qa_rejects_isolated_click_before_speech() -> None:
        sample_rate = 16000
        audio = np.zeros(sample_rate, dtype=np.float32)
        speech_start = int(sample_rate * 0.065)
        speech_end = int(sample_rate * 0.88)
        time = np.arange(speech_end - speech_start, dtype=np.float32) / sample_rate
        audio[speech_start:speech_end] = 0.12 * np.sin(2 * np.pi * 180 * time)
        audio[int(sample_rate * 0.015) : int(sample_rate * 0.015) + 4] = 0.8
        result = measure_timing_quality(audio, sample_rate)
>       assert result["passed"] is False
E       assert True is False

tests\test_dub_quality_v4.py:162: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_dub_quality_v4.py::test_timing_qa_rejects_thirty_ms_chirp_before_real_speech - assert True is False
FAILED tests/test_dub_quality_v4.py::test_timing_qa_rejects_isolated_click_before_speech - assert True is False
