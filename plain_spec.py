"""Compatibility shim: the parser now lives in the ``plain-parser`` package.

The render-range helpers below stay here because they read codeplain's CLI
arguments (``--render-range`` / ``--render-from``); the FRID slicing they wrap
is ``plain_parser.plain_spec.get_frids_range``.
"""

from plain_parser.plain_spec import *  # noqa: F401,F403
from plain_parser.plain_spec import get_frids_range


def get_render_range(render_range, plain_source):
    render_range = render_range.split(",")
    range_end = render_range[1] if len(render_range) == 2 else render_range[0]

    return get_frids_range(plain_source, render_range[0], range_end)


def get_render_range_from(start, plain_source):
    return get_frids_range(plain_source, start)


def compute_render_range(args, plain_source_tree):
    """Compute render range from --render-range or --render-from arguments.

    Args:
        args: Parsed command line arguments
        plain_source_tree: Parsed plain source tree

    Returns:
        List of FRIDs to render, or None to render all
    """
    if args.render_range:
        return get_render_range(args.render_range, plain_source_tree)
    elif args.render_from:
        return get_render_range_from(args.render_from, plain_source_tree)
    return None
