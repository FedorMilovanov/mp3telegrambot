# Dub Studio final Windows gate

- Result: **FAIL**
- Source: b42ad8252062ebb2c771cc190902a4baaedc3393
- Focused compile/tests/Ruff: failure
- Physical media render remains local-only; run /dubcheck.

..................................                                       [100%]
B023 Function definition does not bind loop variable `model_class`
   --> tests\test_semantic_tts_guard.py:135:24
    |
133 |             @staticmethod
134 |             def from_pretrained(*args, load_denoiser=False, **kwargs):
135 |                 return model_class()
    |                        ^^^^^^^^^^^
136 |
137 |         fake_module = types.ModuleType("voxcpm")
    |

Found 1 error.
