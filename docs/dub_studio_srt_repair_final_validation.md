# Dub Studio ready-SRT repair final validation

- Result: **FAIL**
- Ready-SRT source-window fallback, Quality v4.2 worker and dubcheck contracts.
- Compile/tests/Ruff: failure

..................................F.........                             [100%]
================================== FAILURES ===================================
________ test_hardened_worker_installs_tree_cancel_and_version_marker _________

tmp_path = WindowsPath('C:/Users/runneradmin/AppData/Local/Temp/pytest-of-runneradmin/pytest-0/test_hardened_worker_installs_0')

    def test_hardened_worker_installs_tree_cancel_and_version_marker(tmp_path: Path) -> None:
        original_terminate = worker._terminate_process
        original_register = DubStore.register_worker
        original_heartbeat = DubStore.worker_heartbeat
        original_finish_job = DubStore.finish_job
        try:
            hardened_worker.install_hardening()
            assert worker._terminate_process is hardened_worker._terminate_process_tree
            assert DubStore.register_worker is hardened_worker._register_versioned_worker
            assert DubStore.worker_heartbeat is hardened_worker._heartbeat_versioned_worker
            assert DubStore.finish_job is hardened_worker._finish_job_with_root_cause
>           assert hardened_worker._RUNTIME_VERSION == "dub-worker-quality-v4.1"
E           AssertionError: assert 'dub-worker-quality-v4.2' == 'dub-worker-quality-v4.1'
E             
E             - dub-worker-quality-v4.1
E             ?                       ^
E             + dub-worker-quality-v4.2
E             ?                       ^

tests\test_dub_worker.py:70: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_dub_worker.py::test_hardened_worker_installs_tree_cancel_and_version_marker - AssertionError: assert 'dub-worker-quality-v4.2' == 'dub-worker-quality-v4.1'
  
  - dub-worker-quality-v4.1
  ?                       ^
  + dub-worker-quality-v4.2
  ?                       ^
