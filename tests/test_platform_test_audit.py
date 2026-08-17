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
