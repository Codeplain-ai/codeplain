import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only e2e")

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PLAIN = REPO_ROOT / "examples" / "example_hello_world_python" / "hello_world_python.plain"

# if it takes longer, it's stuck
RENDER_TIMEOUT_SECONDS = 180


def test_render_and_run_hello_world_python_windows(codeplain_exe: Path, api_key: str, tmp_path: Path):
    shutil.copy(EXAMPLE_PLAIN, tmp_path / "hello_world_python.plain")

    env = {**os.environ, "CODEPLAIN_API_KEY": api_key}

    result = subprocess.run(
        [str(codeplain_exe), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert result.returncode == 0, f"codeplain --help failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    result = subprocess.run(
        [
            str(codeplain_exe),
            "--headless",
            "--build-folder",
            "build/",
            "hello_world_python.plain",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=RENDER_TIMEOUT_SECONDS,
        env=env,
        check=False,
    )
    assert result.returncode == 0, (
        f"codeplain render failed (rc={result.returncode}):\n" f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    generated = tmp_path / "build" / "hello_world_python" / "hello_world.py"
    assert generated.exists(), f"expected generated file at {generated} but it was not produced"

    result = subprocess.run(
        ["python", str(generated)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )
    assert result.returncode == 0, f"generated python failed (rc={result.returncode}):\nstderr:\n{result.stderr}"
    assert "hello, world" in result.stdout.lower(), f"unexpected stdout from generated app: {result.stdout!r}"
