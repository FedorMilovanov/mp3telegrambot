from services import _runtime_install_detail


def test_void_installer_result_is_rendered_as_installed() -> None:
    assert _runtime_install_detail(None) == "installed"


def test_explicit_installer_detail_is_preserved() -> None:
    assert _runtime_install_detail("policy=v2") == "policy=v2"


def test_blank_installer_detail_uses_component_label() -> None:
    assert _runtime_install_detail("   ", installed_label="ready") == "ready"
