"""Tests for environment resolution in system_config."""

import pytest

from system_config import DEVELOPMENT_ENV, ENVIRONMENT_VAR, PRODUCTION_ENV, _resolve_environment, system_config

# An arbitrary environment name, deliberately neither production nor development.
EXPLICIT_ENV = "staging"


@pytest.fixture
def unset_environment_var(monkeypatch):
    monkeypatch.delenv(ENVIRONMENT_VAR, raising=False)


@pytest.mark.parametrize("installed", [True, False])
def test_explicit_environment_var_overrides_installed_state(monkeypatch, installed):
    monkeypatch.setenv(ENVIRONMENT_VAR, EXPLICIT_ENV)
    assert _resolve_environment(installed) == EXPLICIT_ENV


def test_installed_package_defaults_to_production(unset_environment_var):
    assert _resolve_environment(True) == PRODUCTION_ENV


def test_uninstalled_source_checkout_defaults_to_development(unset_environment_var):
    assert _resolve_environment(False) == DEVELOPMENT_ENV


def test_surrounding_whitespace_is_stripped_from_explicit_value(monkeypatch):
    monkeypatch.setenv(ENVIRONMENT_VAR, f"  {EXPLICIT_ENV}  ")
    assert _resolve_environment(True) == EXPLICIT_ENV


@pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "whitespace"])
@pytest.mark.parametrize(
    "installed,expected",
    [(True, PRODUCTION_ENV), (False, DEVELOPMENT_ENV)],
    ids=["installed", "uninstalled"],
)
def test_blank_environment_var_is_treated_as_unset(monkeypatch, blank, installed, expected):
    monkeypatch.setenv(ENVIRONMENT_VAR, blank)
    assert _resolve_environment(installed) == expected


def test_system_config_exposes_resolved_environment():
    assert isinstance(system_config.environment, str)
    assert system_config.environment


if __name__ == "__main__":
    pytest.main([__file__])
