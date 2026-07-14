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
    "Every file line below is a complete path relative to the project root — pass it "
    "VERBATIM to read_file/grep, exactly as written. The map reflects session start and "
    "may be slightly stale; always read a file before editing it."
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


def _render_root(label: str, root: str, entries: list[_FileEntry], boosted: set[str], level: int) -> list[str]:
    """Render one root at a detail level: 0 = all outlines, 1 = boosted outlines only,
    2 = paths only, 3 = collapsed directories.

    Every file line carries the COMPLETE path (root joined with the file's relative
    path), ready to pass to read_file/grep verbatim. Requiring the model to join a
    heading prefix with a bare file name is exactly how wrong-file reads happen, so
    no line here needs mental path arithmetic. Outlines stay indented beneath their
    file line; paths start at column 0.
    """
    lines = [f"{label} — {root} ({len(entries)} files):"]

    if level >= 3:
        # Even fully collapsed, the files implicated by the task (spec terms, failing
        # test output) keep their complete path and outline — they are what the agent
        # needs to find first.
        for entry in sorted(entries, key=lambda e: e.rel_path):
            if entry.abs_path not in boosted:
                continue
            line_count = f"  ({entry.lines} lines)" if entry.lines else ""
            lines.append(f"{os.path.join(root, entry.rel_path)}{line_count}")
            lines.extend(f"    {outline_line}" for outline_line in entry.outline)
        by_dir: dict[str, list[_FileEntry]] = {}
        for entry in entries:
            by_dir.setdefault(os.path.dirname(entry.rel_path), []).append(entry)
        for dir_path in sorted(by_dir):
            dir_entries = by_dir[dir_path]
            full_dir = os.path.join(root, dir_path) if dir_path else root
            names = ", ".join(os.path.basename(e.rel_path) for e in dir_entries[:6])
            more = f", … +{len(dir_entries) - 6} more" if len(dir_entries) > 6 else ""
            lines.append(f"{full_dir}/ ({len(dir_entries)} files: {names}{more})")
        return lines

    for entry in sorted(entries, key=lambda e: e.rel_path):
        line_count = f"  ({entry.lines} lines)" if entry.lines else ""
        lines.append(f"{os.path.join(root, entry.rel_path)}{line_count}")
        include_outline = level == 0 or (level == 1 and entry.abs_path in boosted)
        if include_outline:
            lines.extend(f"    {outline_line}" for outline_line in entry.outline)

    return lines


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
    # Walk the normalized absolute path but display the path as the caller gave it —
    # the rest of the agent's prompt refers to folders by project-root-relative paths.
    existing_roots = [(label, path, os.path.normpath(os.path.abspath(path))) for label, path in roots if path]
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

    text = ""
    for level in range(4):
        parts = [MAP_HEADER]
        for label, path, entries in collected:
            parts.extend(_render_root(label, path, entries, boosted, level))
        text = "\n".join(parts)
        if len(text) <= max_chars:
            return text

    return text[:max_chars] + "\n… [map truncated to budget]"


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
