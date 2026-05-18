from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PLAIN = REPO_ROOT / "examples" / "example_hello_world_python" / "hello_world_python.plain"

# if it takes longer, it's stuck
RENDER_TIMEOUT_SECONDS = 180


def test_render_and_run_hello_world_python(e2e_container, exec_in_container, copy_to_container):
    copy_to_container(e2e_container, EXAMPLE_PLAIN)

    rc, out, err = exec_in_container(e2e_container, "codeplain --help")
    assert rc == 0, f"codeplain not on PATH after install:\nstdout:\n{out}\nstderr:\n{err}"

    rc, out, err = exec_in_container(
        e2e_container,
        "codeplain --headless --build-folder build/ hello_world_python.plain",
        timeout=RENDER_TIMEOUT_SECONDS,
    )
    assert rc == 0, f"codeplain render failed (rc={rc}):\nstdout:\n{out}\nstderr:\n{err}"

    generated = "build/hello_world_python/hello_world.py"
    rc, _, _ = exec_in_container(e2e_container, f"test -f {generated}")
    assert rc == 0, f"expected generated file at {generated} but it was not produced"

    rc, out, err = exec_in_container(e2e_container, f"python3 {generated}")
    assert rc == 0, f"generated python failed (rc={rc}):\nstdout:\n{out}\nstderr:\n{err}"
    assert "hello, Mars" in out.lower(), f"unexpected stdout from generated app: {out!r}"
