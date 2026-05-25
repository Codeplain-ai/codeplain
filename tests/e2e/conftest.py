import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTAINER_WORKDIR = "/home/e2e/work"
INSTALL_PS1 = REPO_ROOT / "install" / "powershell" / "install.ps1"


def _docker_available() -> bool:
    return shutil.which("docker") is not None


@pytest.fixture(scope="session")
def docker_image() -> str:
    if not _docker_available():
        pytest.skip("docker not available on host")
    return os.environ.get("CODEPLAIN_E2E_IMAGE", "codeplain-e2e:latest")


@pytest.fixture(scope="session")
def api_key() -> str:
    key = os.environ.get("CODEPLAIN_API_KEY")

    if key is None:
        pytest.skip("CODEPLAIN_API_KEY not set")
    if key == "":
        pytest.fail("CODEPLAIN_API_KEY is set but empty")
    return key


@pytest.fixture
def e2e_container(docker_image: str, api_key: str):
    name = f"codeplain-e2e-{uuid.uuid4().hex[:12]}"
    run = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-e",
            f"CODEPLAIN_API_KEY={api_key}",
            "-e",
            "CODEPLAIN_INSTALL_NONINTERACTIVE=1",
            docker_image,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        pytest.fail(f"docker run failed: {run.stderr}")
    container_id = run.stdout.strip()

    try:
        install = subprocess.run(
            ["docker", "exec", container_id, "bash", "/home/e2e/install.sh"],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        assert install.returncode == 0, (
            f"install.sh failed inside container (rc={install.returncode})\n"
            f"stdout:\n{install.stdout}\nstderr:\n{install.stderr}"
        )

        yield container_id
    finally:
        subprocess.run(
            ["docker", "stop", container_id],
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.fixture
def exec_in_container():
    def _exec(container_id: str, cmd: str, workdir: str = CONTAINER_WORKDIR, timeout: int = 600):
        result = subprocess.run(
            ["docker", "exec", "-w", workdir, container_id, "bash", "-lc", cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout, result.stderr

    return _exec


@pytest.fixture
def copy_to_container():
    def _copy(container_id: str, src: Path, dest: str = CONTAINER_WORKDIR + "/"):
        subprocess.run(
            ["docker", "cp", str(src), f"{container_id}:{dest}"],
            capture_output=True,
            text=True,
            check=True,
        )

    return _copy


# No docker on this runner. The install.ps1 and Codeplain CLI runs directly on the windows VM.
@pytest.fixture(scope="session")
def codeplain_exe(api_key: str) -> Path:
    if sys.platform != "win32":
        pytest.skip("Windows-only fixture")

    env = {
        **os.environ,
        "CODEPLAIN_API_KEY": api_key,
        "CODEPLAIN_INSTALL_NONINTERACTIVE": "1",
    }
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(INSTALL_PS1)],
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"install.ps1 failed (rc={result.returncode})\n" f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    # uv tool installs binaries to %USERPROFILE%\.local\bin (see install.ps1
    # line ~102). Resolve the absolute path so our subprocess doesn't depend
    # on the current process's stale PATH — install.ps1 writes the user PATH
    # to the registry, but the python process running pytest already cached
    # its env at startup.
    exe = Path(os.environ["USERPROFILE"]) / ".local" / "bin" / "codeplain.exe"
    if not exe.exists():
        pytest.fail(f"codeplain.exe not found at expected location: {exe}")
    return exe
