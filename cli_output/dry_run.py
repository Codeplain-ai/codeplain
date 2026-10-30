"""Dry run output display for --dry-run flag."""

from typing import Optional

import plain_spec
from plain2code_console import console


def print_dry_run_output(plain_source_tree: dict, render_range: Optional[list[str]]):
    """Print dry run output showing what would be rendered."""
    frid = plain_spec.get_first_frid(plain_source_tree)

    while frid is not None:
        is_inside_range = render_range is None or frid in render_range

        if is_inside_range:
            specifications, _ = plain_spec.get_specifications_for_frid(plain_source_tree, frid)
            functional_requirement_text = specifications[plain_spec.FUNCTIONAL_REQUIREMENTS][-1]
            console.info(
                "-------------------------------------\n"
                f"Rendering functionality {frid}:\n"
                f"{functional_requirement_text}\n"
                "-------------------------------------\n"
            )
            for section, label in (
                (plain_spec.CONFORMANCE_TESTS, "Conformance scenarios"),
                (plain_spec.ACCEPTANCE_TESTS, "Acceptance scenarios"),
            ):
                scenario_ids = [scenario["id"] for scenario in specifications.get(section) or []]
                if scenario_ids:
                    console.info(f"{label}: {', '.join(scenario_ids)}\n")
        else:
            console.info(
                "-------------------------------------\n"
                f"Skipping rendering iteration: {frid}\n"
                "-------------------------------------"
            )

        frid = plain_spec.get_next_frid(plain_source_tree, frid)
