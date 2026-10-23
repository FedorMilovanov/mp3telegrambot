# Dub Studio Quality v4.2 — validation record

- **Текущий статус: PASS** (перепроверено 2026-10-23)
- Область: плотностной детектор начала речи (`measure_timing_quality`),
  регрессии «30 мс чирп» и «одиночный клик перед речью».
- Compile / tests / ruff: зелёные.

## Как проверено (2026-10-23)

```bash
python -m pytest -q \
  tests/test_dub_quality_v4.py::test_timing_qa_rejects_thirty_ms_chirp_before_real_speech \
  tests/test_dub_quality_v4.py::test_timing_qa_rejects_isolated_click_before_speech
# ..                                                                       [100%]

python -m pytest -q tests/test_dub_quality_v4.py
# ...........                                                              [100%]
```

Оба теста, которые в отчёте ниже помечены как падающие, проходят. Причина
расхождения: отчёт был снят до правки детектора и с тех пор не обновлялся —
файл назывался «latest», но описывал состояние, которого уже не было.
Именно поэтому этот документ теперь содержит дату и команду проверки, а не
только вывод.

## История: исходный FAIL (до исправления, оставлен для контекста)

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
