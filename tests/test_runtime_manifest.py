from __future__ import annotations

import sys
from types import ModuleType

import pytest

from services.runtime_manifest import (
    RUNTIME_MANIFEST_POLICY,
    RuntimeBootstrapError,
    RuntimeFeature,
    RuntimeFeatureState,
    RuntimeManifest,
    RuntimePhase,
)


def _module(name: str, **functions):
    module = ModuleType(name)
    for function_name, function in functions.items():
        setattr(module, function_name, function)
    sys.modules[name] = module
    return module


def test_required_feature_failure_is_fail_closed():
    name = "_test_runtime_required_failure"

    def explode():
        raise ValueError("broken")

    _module(name, install=explode)
    manifest = RuntimeManifest(
        (
            RuntimeFeature(
                "required",
                name,
                "install",
                RuntimePhase.PRE_MAIN,
            ),
        )
    )

    with pytest.raises(RuntimeBootstrapError, match="required"):
        manifest.install_phase(RuntimePhase.PRE_MAIN)

    payload = manifest.as_dict()
    assert payload["policy"] == RUNTIME_MANIFEST_POLICY
    assert payload["required_ready"] is False
    assert payload["features"]["required"]["state"] == RuntimeFeatureState.FAILED.value


def test_optional_failure_is_recorded_without_hiding_required_success():
    optional_name = "_test_runtime_optional_failure"
    required_name = "_test_runtime_required_success"

    def explode():
        raise RuntimeError("optional unavailable")

    calls = []

    def install_required():
        calls.append("required")

    _module(optional_name, install=explode)
    _module(required_name, install=install_required)
    manifest = RuntimeManifest(
        (
            RuntimeFeature(
                "optional",
                optional_name,
                "install",
                RuntimePhase.PRE_MAIN,
                required=False,
            ),
            RuntimeFeature(
                "required",
                required_name,
                "install",
                RuntimePhase.PRE_MAIN,
            ),
        )
    )

    manifest.install_phase(RuntimePhase.PRE_MAIN)
    manifest.require_ready()

    payload = manifest.as_dict()
    assert calls == ["required"]
    assert payload["required_ready"] is True
    assert payload["features"]["optional"]["state"] == RuntimeFeatureState.FAILED.value
    assert payload["features"]["required"]["state"] == RuntimeFeatureState.INSTALLED.value


def test_required_dependency_failure_stops_dependent_feature():
    dependency_name = "_test_runtime_dependency_failure"
    dependent_name = "_test_runtime_dependent"
    calls = []

    def fail_dependency():
        raise RuntimeError("no dependency")

    def install_dependent():
        calls.append("dependent")

    _module(dependency_name, install=fail_dependency)
    _module(dependent_name, install=install_dependent)
    manifest = RuntimeManifest(
        (
            RuntimeFeature(
                "dependency",
                dependency_name,
                "install",
                RuntimePhase.PRE_MAIN,
                required=False,
            ),
            RuntimeFeature(
                "dependent",
                dependent_name,
                "install",
                RuntimePhase.PRE_MAIN,
                dependencies=("dependency",),
            ),
        )
    )

    with pytest.raises(RuntimeBootstrapError, match="blocked"):
        manifest.install_phase(RuntimePhase.PRE_MAIN)

    assert calls == []
    assert manifest.as_dict()["features"]["dependent"]["state"] == "failed"


def test_main_module_is_passed_only_to_declared_installers():
    name = "_test_runtime_main_module"
    received = []

    def install(main_module):
        received.append(main_module)

    _module(name, install=install)
    manifest = RuntimeManifest(
        (
            RuntimeFeature(
                "post-main",
                name,
                "install",
                RuntimePhase.POST_MAIN,
                requires_main=True,
            ),
        )
    )
    main_module = ModuleType("_test_main")

    manifest.install_phase(RuntimePhase.POST_MAIN, main_module=main_module)
    manifest.require_ready()

    assert received == [main_module]


def test_explicit_false_is_a_failure_for_boolean_guards():
    name = "_test_runtime_false_guard"
    _module(name, install=lambda: False)
    manifest = RuntimeManifest(
        (
            RuntimeFeature(
                "guard",
                name,
                "install",
                RuntimePhase.PRE_MAIN,
                false_is_failure=True,
            ),
        )
    )

    with pytest.raises(RuntimeBootstrapError, match="returned False"):
        manifest.install_phase(RuntimePhase.PRE_MAIN)
