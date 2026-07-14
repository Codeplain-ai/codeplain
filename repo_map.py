"""Client-side repo map: a compact, budgeted orientation seed for agent sessions.

Agent sessions are seeded with spec text and folder paths only, so every session
used to re-derive the codebase layout with ls/grep/read_file round trips — the same
orientation reads repeated per session. This module generates a compact "codebase
map" (a file tree with public-signature outlines) plus a rolling per-module "code
brief" (one line per implemented FRID), which the agent actions inject into the
session's initial context.

The map is orientation, not ground truth: it may be slightly stale by the time the
agent acts on it, so its header instructs the agent to read files before editing
them. Generation is cheap — regex outlines with a per-file cache keyed by content
hash — and best-effort: failing to build the map must never fail a render.
"""

import hashlib
import json
import os
import re

from plain2code_console import console

# ~4k tokens. The map lives in the session-stable (prompt-cached) system content, so
# it is paid mostly once per session — but it still competes with specs and diffs for
# the context window, so over budget it degrades (outlines -> paths-only -> collapsed
# dirs) rather than grow. Full tool-ready paths make lines long; the budget accounts
# for that.
MAX_MAP_CHARS = 16_000
MAX_BRIEF_CHARS = 4_000
MAX_OUTLINE_LINES_PER_FILE = 24
MAX_OUTLINE_LINE_CHARS = 110
MAX_FILE_SIZE_BYTES = 1_000_000

CODEPLAIN_SUBFOLDER = ".codeplain"
CACHE_FILE_NAME = "repo_map_cache.json"
CODE_BRIEF_FILE_NAME = "code_brief.md"

MAP_HEADER = (
    "A file's path is <section root folder> + <directory line> + <file name>. Paths "
    "relative to a root folder also work as-is — the file tools resolve them against "
    "the implementation code and conformance tests folders. The map reflects session "
    "start and may be slightly stale; always read a file before editing it."
)

CODE_BRIEF_HEADER = (
    "# Module implementation history\n\n"
    "One line per implemented functionality (FRID): what it implemented and the files it touched.\n\n"
)

_EXCLUDED_DIRS = {
    ".git",
    CODEPLAIN_SUBFOLDER,
    ".memory",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".idea",
    ".gradle",
    "target",
}

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def _outline_java_like(stripped: str) -> bool:
    """Type declarations and public/protected members in Java-family syntax."""
    if not stripped.startswith(("public ", "protected ", "class ", "interface ", "enum ", "record ", "abstract ")):
        return False
    if any(keyword in stripped for keyword in ("class ", "interface ", "enum ", "record ")):
        return True
    # Method signatures have an argument list; fields have an assignment before any "(".
    if "(" not in stripped:
        return False
    return "=" not in stripped.split("(", 1)[0]


def _outline_python(stripped: str, indent: int) -> bool:
    return indent <= 4 and stripped.startswith(("def ", "async def ", "class "))


def _outline_go(stripped: str) -> bool:
    return stripped.startswith(("func ", "type "))


def _outline_js(stripped: str) -> bool:
    return stripped.startswith(("export ", "class ", "function ", "async function ", "interface "))


def _outline_kotlin(stripped: str) -> bool:
    return stripped.startswith(("class ", "data class ", "object ", "interface ", "fun ", "suspend fun "))


def _outline_ruby(stripped: str) -> bool:
    return stripped.startswith(("class ", "module ", "def "))


_EXTENSION_FAMILIES = {
    ".java": "java",
    ".cs": "java",
    ".scala": "java",
    ".py": "python",
    ".go": "go",
    ".js": "js",
    ".jsx": "js",
    ".ts": "js",
    ".tsx": "js",
    ".kt": "kotlin",
    ".rb": "ruby",
}


def _extract_outline(text: str, extension: str) -> list[str]:
    """Extract declaration lines (classes, public methods/functions) from source text.

    Regex-level fidelity is deliberate: the outline only needs to tell the agent
    which file to read, not parse the language. Unknown extensions get no outline
    (the file still appears in the tree).
    """
    family = _EXTENSION_FAMILIES.get(extension)
    if family is None:
        return []

    outline = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if family == "java":
            matched = _outline_java_like(stripped)
        elif family == "python":
            matched = _outline_python(stripped, indent)
        elif family == "go":
            matched = _outline_go(stripped)
        elif family == "js":
            matched = _outline_js(stripped)
        elif family == "kotlin":
            matched = _outline_kotlin(stripped)
        else:
            matched = _outline_ruby(stripped)
        if not matched:
            continue

        rendered = stripped.rstrip("{").rstrip()
        if len(rendered) > MAX_OUTLINE_LINE_CHARS:
            rendered = rendered[:MAX_OUTLINE_LINE_CHARS] + "…"
        # Preserve one level of nesting for indentation-scoped languages (methods).
        if family == "python" and indent > 0:
            rendered = "  " + rendered
        outline.append(rendered)
        if len(outline) >= MAX_OUTLINE_LINES_PER_FILE:
            outline.append("… [outline truncated]")
            break
    return outline


def _load_cache(cache_folder: str) -> dict:
    try:
        with open(os.path.join(cache_folder, CACHE_FILE_NAME), "r", encoding="utf-8") as f:
            cache = json.load(f)
        return cache if isinstance(cache, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache_folder: str, cache: dict) -> None:
    try:
        os.makedirs(cache_folder, exist_ok=True)
        with open(os.path.join(cache_folder, CACHE_FILE_NAME), "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except OSError:
        pass  # The cache is an optimization; losing it only costs re-extraction.


class _FileEntry:
    def __init__(self, rel_path: str, abs_path: str, lines: int, outline: list[str]):
        self.rel_path = rel_path
        self.abs_path = abs_path
        self.lines = lines
        self.outline = outline

    @property
    def stem(self) -> str:
        return os.path.splitext(os.path.basename(self.rel_path))[0].lower()


def _read_source_bytes(abs_path: str) -> bytes | None:
    """Read a file for outlining; None for oversized or binary files."""
    try:
        if os.path.getsize(abs_path) > MAX_FILE_SIZE_BYTES:
            return None
        with open(abs_path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if b"\0" in raw[:1024]:
        return None
    return raw


def _collect_entries(root: str, cache: dict) -> tuple[list[_FileEntry], bool]:
    """Walk a root folder and produce file entries, using/refreshing the outline cache."""
    entries = []
    cache_changed = False
    root = os.path.normpath(os.path.abspath(root))

    for current_dir, dir_names, file_names in os.walk(root):
        dir_names[:] = sorted(d for d in dir_names if d not in _EXCLUDED_DIRS)
        for file_name in sorted(file_names):
            abs_path = os.path.join(current_dir, file_name)
            rel_path = os.path.relpath(abs_path, root)

            raw = _read_source_bytes(abs_path)
            if raw is None:
                entries.append(_FileEntry(rel_path, abs_path, 0, []))
                continue

            digest = hashlib.sha1(raw).hexdigest()
            cached = cache.get(abs_path)
            if cached and cached.get("sha1") == digest:
                entries.append(_FileEntry(rel_path, abs_path, cached.get("lines", 0), cached.get("outline", [])))
                continue

            text = raw.decode("utf-8", errors="replace")
            lines = text.count("\n") + 1 if text else 0
            outline = _extract_outline(text, os.path.splitext(file_name)[1].lower())
            cache[abs_path] = {"sha1": digest, "lines": lines, "outline": outline}
            cache_changed = True
            entries.append(_FileEntry(rel_path, abs_path, lines, outline))

    return entries, cache_changed


def _relevance_tokens(relevance_text: str) -> set[str]:
    return {token.lower() for token in _IDENTIFIER_RE.findall(relevance_text or "")}


def _file_line(entry: _FileEntry, name: str) -> str:
    line_count = f"  ({entry.lines} lines)" if entry.lines else ""
    return f"{name}{line_count}"


def _render_boosted_block(root: str, entries: list[_FileEntry], boosted: set[str]) -> list[str]:
    """Task-implicated files (spec terms, failing-test output), as complete single-line
    paths with outlines. These are the reads that must not miss, so they get verbatim
    paths at every detail level — the only place the per-file path cost is paid."""
    lines = []
    for entry in sorted(entries, key=lambda e: e.rel_path):
        if entry.abs_path not in boosted:
            continue
        lines.append(_file_line(entry, os.path.join(root, entry.rel_path)))
        lines.extend(f"    {outline_line}" for outline_line in entry.outline)
    if lines:
        lines.insert(0, "Key files for this task (complete paths):")
    return lines


def _render_root(label: str, root: str, entries: list[_FileEntry], boosted: set[str], level: int) -> list[str]:
    """Render one root at a detail level: 0 = all outlines, 1 = names only,
    2+ = collapsed directories.

    Layout: directory lines are relative to the section's root folder (stated once on
    the section line), with bare file names indented beneath them. The root prefix is
    not repeated per line — repeating it would spend most of the budget re-encoding
    the same string — and the file tools resolve root-relative paths directly, so
    even a path used without the prefix reaches the right file. Root-level files sit
    under the full root folder line to avoid bare names resolving elsewhere. Boosted
    files additionally appear with complete paths in the block above the tree.
    """
    lines = [f"{label} — root folder: {root}/ ({len(entries)} files, directory lines relative to the root folder):"]
    lines.extend(_render_boosted_block(root, entries, boosted))

    by_dir: dict[str, list[_FileEntry]] = {}
    for entry in entries:
        by_dir.setdefault(os.path.dirname(entry.rel_path), []).append(entry)

    for dir_path in sorted(by_dir):
        dir_entries = sorted(by_dir[dir_path], key=lambda e: e.rel_path)
        dir_line = (dir_path + "/") if dir_path else (root + "/")

        if level >= 2:
            names = ", ".join(os.path.basename(e.rel_path) for e in dir_entries[:6])
            more = f", … +{len(dir_entries) - 6} more" if len(dir_entries) > 6 else ""
            lines.append(f"{dir_line} ({len(dir_entries)} files: {names}{more})")
            continue

        lines.append(dir_line)
        for entry in dir_entries:
            lines.append(f"  {_file_line(entry, os.path.basename(entry.rel_path))}")
            # Boosted outlines already appear in the block above — no need twice.
            if level == 0 and entry.abs_path not in boosted:
                lines.extend(f"    {outline_line}" for outline_line in entry.outline)

    return lines


def _display_root(path: str) -> str:
    """Project-root-relative form of a root folder for display, when it lies under
    the project root (the CWD, which is also what the file tools resolve against);
    otherwise the path as given."""
    rel = os.path.relpath(os.path.normpath(os.path.abspath(path)), os.getcwd())
    return path if rel.startswith("..") else rel


def generate_repo_map(
    roots: list[tuple[str, str]],
    cache_folder: str,
    relevance_text: str = "",
    max_chars: int = MAX_MAP_CHARS,
) -> str:
    """Generate the codebase map for the given (label, folder) roots.

    Degrades through detail levels until the result fits max_chars; returns "" when
    no root exists.
    """
    # Walk the normalized absolute path but display a project-root-relative path —
    # the rest of the agent's prompt refers to folders relative to the project root,
    # and an absolute build folder (e.g. from an absolute --build-folder) would bloat
    # every line with the same machine-specific prefix.
    existing_roots = [
        (label, _display_root(path), os.path.normpath(os.path.abspath(path))) for label, path in roots if path
    ]
    existing_roots = [(label, path, abs_path) for label, path, abs_path in existing_roots if os.path.isdir(abs_path)]
    if not existing_roots:
        return ""

    cache = _load_cache(cache_folder)
    collected = []
    cache_changed = False
    for label, display_path, abs_path in existing_roots:
        entries, changed = _collect_entries(abs_path, cache)
        collected.append((label, display_path, entries))
        cache_changed = cache_changed or changed
    if cache_changed:
        _save_cache(cache_folder, cache)

    tokens = _relevance_tokens(relevance_text)
    boosted = {entry.abs_path for _, _, entries in collected for entry in entries if entry.stem in tokens}

    header = [MAP_HEADER]
    example = _example_read_call(collected, boosted)
    if example:
        header.append(example)

    text = ""
    for level in range(3):
        parts = list(header)
        for label, path, entries in collected:
            parts.extend(_render_root(label, path, entries, boosted, level))
        text = "\n".join(parts)
        if len(text) <= max_chars:
            return text

    return text[:max_chars] + "\n… [map truncated to budget]"


def _example_read_call(collected: list, boosted: set[str]) -> str:
    """A worked path-join example using a real file from the map — models follow a
    concrete example far more reliably than a construction rule alone."""
    chosen = None
    for _, display_path, entries in collected:
        for entry in entries:
            if chosen is None or entry.abs_path in boosted:
                chosen = os.path.join(display_path, entry.rel_path)
                if entry.abs_path in boosted:
                    return f'Example: read_file("{chosen}")'
    return f'Example: read_file("{chosen}")' if chosen else ""


def build_repo_map_param(render_context, conformance_tests_folder: str | None = None, relevance_text: str = "") -> str:
    """Build the repo_map task param for an agent session; "" (omit the param) on any failure."""
    try:
        roots = [("Implementation code", render_context.build_folder)]
        if conformance_tests_folder:
            roots.append(("Conformance tests", conformance_tests_folder))
        cache_folder = os.path.join(render_context.build_folder, CODEPLAIN_SUBFOLDER)
        return generate_repo_map(roots, cache_folder, relevance_text=relevance_text)
    except Exception as e:
        console.warning(f"Could not build repo map (continuing without it): {e}")
        return ""


def read_text_tail(path: str, max_chars: int = 4_000) -> str:
    """Best-effort tail of a text file, used to mine failure output for relevance terms."""
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        return text[-max_chars:]
    except OSError:
        return ""


def _code_brief_path(build_folder: str) -> str:
    return os.path.join(build_folder, CODEPLAIN_SUBFOLDER, CODE_BRIEF_FILE_NAME)


def append_code_brief_entry(build_folder: str, frid: str, requirement_text: str, changed_files: list[str]) -> None:
    """Append one FRID line to the module's rolling code brief. Best-effort: never raises.

    Called just before the per-FRID commit, so the updated brief is included in the
    same commit and survives --render-from resumption.
    """
    try:
        first_line = (requirement_text or "").strip().splitlines()[0] if (requirement_text or "").strip() else ""
        first_line = first_line.lstrip("- ").strip()
        if len(first_line) > 120:
            first_line = first_line[:120] + "…"

        files = [f for f in (changed_files or []) if not f.startswith(CODEPLAIN_SUBFOLDER)]
        shown = ", ".join(f"`{f}`" for f in files[:8])
        if len(files) > 8:
            shown += f" (+{len(files) - 8} more)"
        files_part = f" → {len(files)} file(s): {shown}" if files else ""

        path = _code_brief_path(build_folder)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        is_new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            if is_new:
                f.write(CODE_BRIEF_HEADER)
            f.write(f"- FRID {frid}: {first_line}{files_part}\n")
    except Exception as e:
        console.warning(f"Could not update code brief (continuing without it): {e}")


def read_code_brief(build_folder: str, max_chars: int = MAX_BRIEF_CHARS) -> str:
    """Read the module's code brief for seeding; "" when absent. Tail-truncates when large."""
    try:
        with open(_code_brief_path(build_folder), "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return ""
    if len(text) <= max_chars:
        return text
    return CODE_BRIEF_HEADER + "… [older entries truncated]\n" + text[-max_chars:].split("\n", 1)[-1]
