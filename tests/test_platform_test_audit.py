"""Tests for the client-side portability audit of a completed build."""

import pytest

from render_machine.platform_test_audit import PlatformBoundaryViolation, audit_build_folder, find_platform_references


def test_a_clean_build_passes(tmp_path):
    (tmp_path / "app.py").write_text("print('hello')\n")
    (tmp_path / "requirements.txt").write_text("pytest==8.3.2\n")

    assert find_platform_references(str(tmp_path)) == []
    audit_build_folder(str(tmp_path))  # does not raise


def test_a_helper_reference_in_content_fails_the_audit(tmp_path):
    (tmp_path / "app.py").write_text("subprocess.run(['codeplain-tty', 'send-text', 'x'])\n")

    with pytest.raises(PlatformBoundaryViolation, match="app.py"):
        audit_build_folder(str(tmp_path))


def test_an_environment_prefix_reference_fails_the_audit(tmp_path):
    subdir = tmp_path / "src"
    subdir.mkdir()
    (subdir / "config.py").write_text("token = os.environ.get('CODEPLAIN_TTY_TOKEN')\n")

    with pytest.raises(PlatformBoundaryViolation, match="config.py"):
        audit_build_folder(str(tmp_path))


def test_a_helper_named_file_fails_the_audit(tmp_path):
    (tmp_path / "codeplain-tty").write_text("#!/bin/sh\n")

    with pytest.raises(PlatformBoundaryViolation, match="codeplain-tty"):
        audit_build_folder(str(tmp_path))


def test_vendor_directories_are_not_audited(tmp_path):
    vendored = tmp_path / "node_modules" / "junk"
    vendored.mkdir(parents=True)
    (vendored / "noise.js").write_text("// codeplain-tty mentioned in a vendored comment\n")
    (tmp_path / "app.js").write_text("console.log('clean');\n")

    audit_build_folder(str(tmp_path))  # does not raise


def test_internal_test_trees_inside_the_build_folder_are_exempt(tmp_path):
    """A module's build folder can carry its conformance tests, and those are what the
    helper exists for. Auditing them aborted successful benchmark renders at CreateDist:
    the build was never published, `generated_code` stayed empty, and the delivered
    artifact was whatever the harness could salvage."""
    (tmp_path / "conformance_tests" / "init").mkdir(parents=True)
    (tmp_path / "conformance_tests" / "init" / "test_init.py").write_text(
        "subprocess.run(['codeplain-tty', 'wait-for', 'Password:'])\n"
    )
    (tmp_path / "conformance_tests" / "conformance_tests.json").write_text('{"codeplain-tty": true}\n')
    (tmp_path / "vault.py").write_text("print('hello')\n")

    assert find_platform_references(str(tmp_path)) == []
    audit_build_folder(str(tmp_path))  # does not raise


def test_delivered_code_is_still_audited_alongside_them(tmp_path):
    """Exempting the test tree must not exempt the application beside it."""
    (tmp_path / "conformance_tests").mkdir()
    (tmp_path / "conformance_tests" / "test_init.py").write_text("codeplain-tty wait-for\n")
    (tmp_path / "vault.py").write_text("os.environ['CODEPLAIN_TTY_ENDPOINT']\n")

    assert find_platform_references(str(tmp_path)) == ["vault.py"]


def test_the_renderers_own_log_is_not_audited(tmp_path):
    """codeplain.log records broker activity and module names, so auditing it reports the
    render's own diagnostics as if the application had referenced the helper. Seen on
    loglens, where the log was the single flagged file."""
    (tmp_path / "codeplain.log").write_text("DEBUG codeplain: the codeplain-tty broker thread started\n")
    (tmp_path / "cli.js").write_text("console.log('hi')\n")

    assert find_platform_references(str(tmp_path)) == []
