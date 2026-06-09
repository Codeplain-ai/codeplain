#!/usr/bin/env python3
"""
Explore reachable states in the render state machine.

Usage:
    python explore_states.py <state> <n>
    python explore_states.py <state> <n> --mermaid

Examples:
    python explore_states.py renderInitialised 3
    python explore_states.py implementingFrid_refactoringCode 5 --mermaid
"""

import argparse
import sys
from collections import deque
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Minimal RenderContext stub — only condition methods matter for transitions
# ---------------------------------------------------------------------------
class _StubRenderContext:
    def should_run_unit_tests(self) -> bool:
        return True

    def should_run_conformance_tests(self) -> bool:
        return True

    def has_next_frid(self) -> bool:
        return True

    # Everything else referenced in state defs (on_enter / on_exit callbacks)
    def __getattr__(self, name: str):
        return lambda *a, **kw: None


# ---------------------------------------------------------------------------
# Load transitions without importing the full app stack
# ---------------------------------------------------------------------------
def _load_transitions() -> List[Dict[str, Any]]:
    import os

    root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, root)

    from render_machine.state_machine_config import StateMachineConfig

    config = StateMachineConfig()
    ctx = _StubRenderContext()
    return config.get_transitions(ctx)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Build adjacency map: source -> [(trigger, dest)]
# Wildcard source ("*") is expanded to every known state.
# ---------------------------------------------------------------------------
def _build_graph(transitions: List[Dict[str, Any]]):
    all_states = set()
    for t in transitions:
        src, dst = t["source"], t["dest"]
        if src != "*":
            all_states.add(src)
        all_states.add(dst)

    graph: Dict[str, List[tuple]] = {s: [] for s in all_states}

    for t in transitions:
        src = t["source"]
        trigger = t["trigger"]
        dst = t["dest"]
        cond = t.get("conditions")
        unless = t.get("unless")

        label = trigger
        if cond is not None:
            label += " [if condition]"
        if unless is not None:
            label += " [unless condition]"

        if src == "*":
            for state in all_states:
                graph.setdefault(state, []).append((label, dst))
        else:
            graph.setdefault(src, []).append((label, dst))

    return graph


# ---------------------------------------------------------------------------
# BFS from start_state up to depth n
# ---------------------------------------------------------------------------
def _bfs(graph, start_state: str, n: int):
    """Returns list of (depth, source, trigger, dest) edges within n hops."""
    if start_state not in graph:
        return None, []

    visited_states = {start_state}
    queue = deque([(start_state, 0)])
    edges = []

    while queue:
        state, depth = queue.popleft()
        if depth >= n:
            continue
        for trigger, dest in graph.get(state, []):
            edges.append((depth + 1, state, trigger, dest))
            if dest not in visited_states:
                visited_states.add(dest)
                queue.append((dest, depth + 1))

    return visited_states, edges


# ---------------------------------------------------------------------------
# Output: indented text tree
# ---------------------------------------------------------------------------
def _print_tree(start_state: str, edges, n: int):
    by_source: Dict[str, list] = {}
    for depth, src, trigger, dest in edges:
        by_source.setdefault(src, []).append((depth, trigger, dest))

    print(f"Reachable states from '{start_state}' in up to {n} transition(s):\n")

    def _recurse(state: str, indent: int, seen_paths: set):
        for depth, trigger, dest in by_source.get(state, []):
            prefix = "  " * indent
            print(f"{prefix}--[{trigger}]--> {dest}")
            path_key = (state, trigger, dest)
            if path_key not in seen_paths:
                seen_paths.add(path_key)
                _recurse(dest, indent + 1, seen_paths)

    _recurse(start_state, 0, set())


# ---------------------------------------------------------------------------
# Output: Mermaid diagram
# ---------------------------------------------------------------------------
def _print_mermaid(start_state: str, edges):
    def _safe(s: str) -> str:
        return s.replace(" ", "_").replace("[", "").replace("]", "")

    print("```mermaid")
    print("stateDiagram-v2")
    seen = set()
    for _, src, trigger, dest in edges:
        key = (src, trigger, dest)
        if key not in seen:
            seen.add(key)
            print(f"    {_safe(src)} --> {_safe(dest)} : {trigger}")
    print("```")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Explore reachable states from a given state in n transitions.")
    parser.add_argument("state", help="Starting state (e.g. renderInitialised)")
    parser.add_argument("n", type=int, help="Maximum number of transitions to follow")
    parser.add_argument("--mermaid", action="store_true", help="Emit a Mermaid stateDiagram instead of text tree")
    args = parser.parse_args()

    transitions = _load_transitions()
    graph = _build_graph(transitions)

    if args.state not in graph:
        available = sorted(graph.keys())
        print(f"Error: state '{args.state}' not found.\n", file=sys.stderr)
        print("Known states:", file=sys.stderr)
        for s in available:
            print(f"  {s}", file=sys.stderr)
        sys.exit(1)

    visited, edges = _bfs(graph, args.state, args.n)

    if args.mermaid:
        _print_mermaid(args.state, edges)
    else:
        _print_tree(args.state, edges, args.n)


if __name__ == "__main__":
    main()
