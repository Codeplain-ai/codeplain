"""Structured trace records for codeplain.log.

Emits one-line, grep-able DEBUG records of the form:

    [tag] key=value key="quoted value" ...

so a full render leaves a trace from which the whole run can be reconstructed:
state-machine transitions, agent turns, individual tool calls, API requests,
script executions, and fix-loop lifecycle events. Values are whitespace-collapsed
and length-capped so pathological content (huge diffs, file bodies) cannot bloat
the log; sizes are logged alongside so nothing is lost silently.

Conventions:
  - tags are kebab-case nouns for the layer: state-machine, agent, tool, api,
    script, fix-loop, review
  - every record for one layer shares the tag, so `grep '\\[tool\\]' codeplain.log`
    yields that layer's full history
  - durations are seconds with one decimal, sizes are character counts
"""

import json
import logging

import plain2code_logger

logger = logging.getLogger(plain2code_logger.LOGGER_NAME)

# Cap for any single traced value. Long values are cut with an explicit marker;
# callers log sizes separately when the full magnitude matters.
PREVIEW_CHARS = 240


def preview(value, limit: int = PREVIEW_CHARS) -> str:
    """One-line, length-capped rendering of an arbitrary value."""
    text = value if isinstance(value, str) else repr(value)
    collapsed = " ".join(text.split())
    if len(collapsed) > limit:
        return collapsed[:limit] + f"…(+{len(collapsed) - limit} chars)"
    return collapsed


def _format_value(value) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.1f}"
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item) for item in value) or "-"
    text = preview(value)
    if text == "" or any(ch in text for ch in ' "='):
        return json.dumps(text, ensure_ascii=False)
    return text


def trace(tag: str, **fields) -> None:
    """Emit one structured DEBUG record. Fields with value None are omitted."""
    parts = [f"{key}={_format_value(value)}" for key, value in fields.items() if value is not None]
    logger.debug("[%s] %s", tag, " ".join(parts))


def summarize_args(args: dict, inline_limit: int = 80) -> str:
    """Compact one-line summary of a tool-call args dict.

    Short values are shown verbatim; long ones (file contents, search/replace
    blocks) are replaced by their length so the record stays one line while the
    call remains identifiable.
    """
    parts = []
    for key, value in args.items():
        text = value if isinstance(value, str) else repr(value)
        if len(text) > inline_limit:
            parts.append(f"{key}=<{len(text)} chars>")
        else:
            parts.append(f"{key}={preview(text, inline_limit)}")
    return " ".join(parts) if parts else "-"
