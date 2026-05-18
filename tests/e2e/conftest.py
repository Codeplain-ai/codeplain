import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTAINER_WORKDIR = "/home/e2e/work"


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
    if not key:
        pytest.skip("CODEPLAIN_API_KEY not set")
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
