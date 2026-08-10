"""Client-side context preloading for agent sessions.

Trace analysis of agent renders showed sessions spending their first 10-20 turns
re-acquiring context the client already has on disk: memory notes, linked resources,
the module's own source files, the previous FRID's diff, and a sibling module's
conformance tests used as a structural template. Each of those turns re-reads the
whole accumulated conversation, so discovery turns dominate render cost.

This module builds those contents up front, under explicit character budgets, so the
agent actions can seed them into the session's initial (prompt-cached) context. All
builders are best-effort and never raise — a missing preload only costs the agent
tool round-trips, exactly as before.
"""

import os
import shutil
import subprocess

import file_utils
import git_utils
import repo_map
from memory_management import MemoryManager
from plain2code_console import console

# Character budgets. The preloads live in session-stable content, so they are paid
# once per session as a cache write — generous budgets beat tool round-trips (each
# round-trip re-reads the full conversation), but unbounded content would crowd out
# the context window on big modules.
MEMORY_NOTE_MAX_CHARS = 10_000
MEMORY_TOTAL_MAX_CHARS = 40_000
LINKED_RESOURCE_MAX_CHARS = 24_000
LINKED_RESOURCES_TOTAL_MAX_CHARS = 80_000
SOURCE_FILE_MAX_CHARS = 20_000
SOURCE_FILES_TOTAL_MAX_CHARS = 100_000
IMPLEMENTATION_DIFF_MAX_CHARS = 60_000
TEST_EXAMPLE_TOTAL_MAX_CHARS = 30_000
ENVIRONMENT_BRIEF_MAX_CHARS = 4_000

_MANIFEST_FILE_NAMES = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "go.mod",
    "Cargo.toml",
)

_VERSION_PROBE_TIMEOUT_SECONDS = 5


def build_memory_preload(memory_folder: str) -> tuple[dict[str, str], list[str]]:
    """Read memory notes for inlining, within budget.

    Returns (contents, all_names): contents maps note path -> content for the notes
    that fit the budget (smallest first, so one huge note cannot evict all the small
    ones); all_names lists every note so the server can index the ones not inlined.
    """
    try:
        all_names = MemoryManager.list_memory_files(memory_folder)
        sized = []
        for name in all_names:
            path = os.path.join(memory_folder, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size <= MEMORY_NOTE_MAX_CHARS:
                sized.append((size, name, path))

        contents: dict[str, str] = {}
        remaining = MEMORY_TOTAL_MAX_CHARS
        for size, name, path in sorted(sized):
            if size > remaining:
                break
            text = _read_text(path)
            if text is None:
                continue
            contents[name] = text
            remaining -= len(text)
        return contents, all_names
    except Exception as e:
        console.warning(f"Could not preload memory notes (continuing without them): {e}")
        return {}, []


def build_linked_resources_preload(linked_resources: dict[str, str] | None) -> tuple[dict[str, str], list[str]]:
    """Split linked resources into inline content and path-only leftovers.

    The resources are declared in the spec for this FRID, so relevance is already
    established — agents read most of them anyway, one tool round-trip at a time.
    Small resources are inlined (smallest first); oversized ones and everything past
    the total budget stay as paths for the agent to read on demand.
    """
    if not linked_resources:
        return {}, []
    try:
        inline: dict[str, str] = {}
        leftover_paths: list[str] = []
        remaining = LINKED_RESOURCES_TOTAL_MAX_CHARS
        for path, content in sorted(linked_resources.items(), key=lambda item: len(item[1] or "")):
            content = content or ""
            if len(content) <= LINKED_RESOURCE_MAX_CHARS and len(content) <= remaining:
                inline[path] = content
                remaining -= len(content)
            else:
                leftover_paths.append(path)
        return inline, sorted(leftover_paths)
    except Exception as e:
        console.warning(f"Could not preload linked resources (continuing with paths only): {e}")
        return {}, sorted(linked_resources.keys())


def module_changed_files(build_folder: str) -> set[str] | None:
    """Files added or changed by the CURRENT module, i.e. since the inherited base copy.

    The build folder is cumulative — a `requires` module inherits every prior module's
    code — so "all files in the build folder" is the wrong relevance signal. Diffing
    the working tree against the base-folder-copy commit (or the initial commit for a
    base module) isolates the current module's own code. Returns None when git cannot
    tell (callers then fall back to relevance-only selection).
    """
    try:
        return {os.path.normpath(file_name) for file_name in git_utils.diff(build_folder).keys()}
    except Exception as e:
        console.debug(f"Could not determine the module's own files from git: {e}")
        return None


def build_source_files_preload(
    build_folder: str, relevance_text: str = "", module_files: set[str] | None = None
) -> dict[str, str]:
    """Full content of the module's most relevant source files, within budget.

    Implementation agents read their own module's files before writing anything;
    conformance agents read them to learn the public surface. The build folder also
    holds all inherited required-module code, so only two kinds of files qualify:
    the current module's own files (module_files, from module_changed_files()) and
    files anywhere in the tree whose name stem appears in the relevance text (spec
    terms — typically the inherited interfaces the spec builds on). Inherited files
    matching neither are deliberately excluded; the codebase map still covers them.

    Priority when the budget is tight: own+relevant, then own, then inherited-but-
    relevant — smallest first within each tier. When module_files is None (git could
    not isolate the module's code), only relevance-matched files are included.
    """
    try:
        file_names = file_utils.list_all_text_files(build_folder)
        tokens = {token.lower() for token in repo_map._IDENTIFIER_RE.findall(relevance_text or "")}

        candidates = []
        for file_name in file_names:
            path = os.path.join(build_folder, file_name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > SOURCE_FILE_MAX_CHARS:
                continue
            stem = os.path.splitext(os.path.basename(file_name))[0].lower()
            boosted = stem in tokens
            own = module_files is not None and os.path.normpath(file_name) in module_files
            if own and boosted:
                tier = 0
            elif own:
                tier = 1
            elif boosted:
                tier = 2
            else:
                continue
            candidates.append((tier, size, file_name, path))

        contents: dict[str, str] = {}
        remaining = SOURCE_FILES_TOTAL_MAX_CHARS
        for _, size, file_name, path in sorted(candidates):
            if size > remaining:
                continue
            text = _read_text(path)
            if text is None:
                continue
            contents[file_name] = text
            remaining -= len(text)
        return contents
    except Exception as e:
        console.warning(f"Could not preload source files (continuing without them): {e}")
        return {}


def build_implementation_diff_preload(render_context) -> str:
    """Markdown-formatted diff of the implementation changes for the current FRID.

    For conformance test rendering this is the exact code the tests must exercise —
    it spares the agent re-deriving "what is new" from the whole module.
    """
    try:
        from render_machine.implementation_code_helpers import ImplementationCodeHelpers

        diff_by_file = ImplementationCodeHelpers.get_code_diff(
            render_context.build_folder,
            render_context.plain_source_tree,
            render_context.frid_context.frid,
        )
        if not diff_by_file:
            return ""
        parts = []
        for file_name, file_diff in diff_by_file.items():
            parts.append(f"### {file_name}\n```diff\n{file_diff}\n```")
        text = "\n\n".join(parts)
        if len(text) > IMPLEMENTATION_DIFF_MAX_CHARS:
            text = text[:IMPLEMENTATION_DIFF_MAX_CHARS] + "\n… [diff truncated — read the files for the rest]"
        return text
    except Exception as e:
        console.warning(f"Could not preload implementation diff (continuing without it): {e}")
        return ""


def build_conformance_test_example_preload(
    module_tests_folders: list[str], exclude_folder: str | None = None
) -> dict[str, str]:
    """A worked example from the most recent existing conformance test suite.

    Every module's first conformance session reads a sibling suite to copy its
    structure (manifest, folder layout, how credentials/config are wired). Candidate
    suites are the immediate subfolders of the given module tests folders (the
    current module's and its required modules'). Returns {path: content} for the
    most recently touched suite's manifest files plus its smallest test files,
    within budget. Empty dict when no prior suite exists (first functionality of
    the first module).
    """
    try:
        best_folder = _find_most_recent_suite(module_tests_folders, exclude_folder)
        if not best_folder:
            return {}
        return _select_example_files(best_folder)
    except Exception as e:
        console.warning(f"Could not preload a conformance test example (continuing without it): {e}")
        return {}


def _find_most_recent_suite(module_tests_folders: list[str], exclude_folder: str | None) -> str | None:
    exclude = os.path.normpath(os.path.abspath(exclude_folder)) if exclude_folder else None
    best_folder = None
    best_mtime = -1.0
    for tests_folder in module_tests_folders or []:
        if not tests_folder or not os.path.isdir(tests_folder):
            continue
        for suite_dir in sorted(os.listdir(tests_folder)):
            suite_path = os.path.join(tests_folder, suite_dir)
            if not os.path.isdir(suite_path) or suite_dir.startswith("."):
                continue
            if exclude and os.path.normpath(os.path.abspath(suite_path)) == exclude:
                continue
            mtime = os.path.getmtime(suite_path)
            if mtime > best_mtime:
                best_mtime = mtime
                best_folder = suite_path
    return best_folder


def _select_example_files(suite_folder: str) -> dict[str, str]:
    """Pick the suite's manifests plus its smallest test files, within budget."""
    manifest_files = []
    test_files = []
    for current_dir, dir_names, file_names in os.walk(suite_folder):
        dir_names[:] = [d for d in dir_names if d not in repo_map._EXCLUDED_DIRS]
        for file_name in sorted(file_names):
            path = os.path.join(current_dir, file_name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if file_name in _MANIFEST_FILE_NAMES:
                manifest_files.append((size, path))
            elif "test" in file_name.lower():
                test_files.append((size, path))

    contents: dict[str, str] = {}
    remaining = TEST_EXAMPLE_TOTAL_MAX_CHARS
    selected = sorted(manifest_files)[:2] + sorted(test_files)[:2]
    for size, path in selected:
        if size > remaining:
            continue
        text = _read_text(path)
        if text is None:
            continue
        display_path = os.path.relpath(path, os.getcwd())
        if display_path.startswith(".."):
            display_path = path
        contents[display_path] = text
        remaining -= len(text)
    return contents


def build_environment_brief(build_folder: str) -> str:
    """Facts about the local toolchain, so agents stop probing for them turn by turn.

    Trace analysis showed sessions burning 10+ turns discovering whether a library
    exists in the local dependency cache, which build tool versions are installed,
    and whether the machine is online. The brief states what is installed for the
    ecosystems the module actually uses (detected from its manifest files) and what
    the local dependency cache holds.
    """
    try:
        ecosystems = _detect_ecosystems(build_folder)
        if not ecosystems:
            return ""

        lines: list[str] = []
        if "java" in ecosystems:
            _append_probe(lines, "java", ["java", "-version"])
            _append_probe(lines, "maven", ["mvn", "-version"])
            _append_maven_cache_summary(lines)
        if "node" in ecosystems:
            _append_probe(lines, "node", ["node", "--version"])
            _append_probe(lines, "npm", ["npm", "--version"])
        if "python" in ecosystems:
            _append_probe(lines, "python", ["python3", "--version"])
            _append_probe(lines, "pip", ["python3", "-m", "pip", "--version"])
        if "go" in ecosystems:
            _append_probe(lines, "go", ["go", "version"])
        if "rust" in ecosystems:
            _append_probe(lines, "cargo", ["cargo", "--version"])

        if not lines:
            return ""
        lines.append(
            "Treat this as ground truth for tool availability — do not re-probe it. If a dependency "
            "is not in the local cache listed above, verify it can be resolved before designing around it."
        )
        text = "\n".join(lines)
        return text[:ENVIRONMENT_BRIEF_MAX_CHARS]
    except Exception as e:
        console.warning(f"Could not build environment brief (continuing without it): {e}")
        return ""


def _detect_ecosystems(build_folder: str) -> set[str]:
    manifest_to_ecosystem = {
        "pom.xml": "java",
        "build.gradle": "java",
        "build.gradle.kts": "java",
        "package.json": "node",
        "requirements.txt": "python",
        "pyproject.toml": "python",
        "go.mod": "go",
        "Cargo.toml": "rust",
    }
    ecosystems: set[str] = set()
    for _current_dir, dir_names, file_names in os.walk(build_folder):
        dir_names[:] = [d for d in dir_names if d not in repo_map._EXCLUDED_DIRS]
        for file_name in file_names:
            ecosystem = manifest_to_ecosystem.get(file_name)
            if ecosystem:
                ecosystems.add(ecosystem)
    return ecosystems


def _append_probe(lines: list[str], label: str, command: list[str]) -> None:
    if shutil.which(command[0]) is None:
        lines.append(f"- {label}: NOT installed (`{command[0]}` not on PATH)")
        return
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_VERSION_PROBE_TIMEOUT_SECONDS,
        )
        output = (result.stdout or result.stderr or "").strip().splitlines()
        if output:
            lines.append(f"- {label}: {output[0].strip()}")
    except Exception:
        pass  # A failed probe just means one fewer line in the brief.


def _append_maven_cache_summary(lines: list[str], max_entries: int = 40) -> None:
    repository = os.path.expanduser("~/.m2/repository")
    if not os.path.isdir(repository):
        return
    try:
        entries = sorted(d for d in os.listdir(repository) if os.path.isdir(os.path.join(repository, d)))
    except OSError:
        return
    if not entries:
        return
    shown = ", ".join(entries[:max_entries])
    more = f", … +{len(entries) - max_entries} more" if len(entries) > max_entries else ""
    lines.append(f"- local maven cache (`~/.m2/repository` top-level groups): {shown}{more}")


def _read_text(path: str) -> str | None:
    """Read a file as UTF-8 text; None for unreadable or binary files."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if b"\0" in raw[:1024]:
            return None
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
