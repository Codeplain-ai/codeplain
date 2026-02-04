from typing import Optional

import plain_spec
from plain2code_console import console

AMBIGUITY_CAUSES = {
    "reference_resource_ambiguity": "Ambiguity is in the reference resources",
    "definition_ambiguity": "Ambiguity is in the definitions",
    "non_functional_requirement_ambiguity": "Ambiguity is in the non-functional requirements",
    "functional_requirement_ambiguity": "Ambiguity is in the functional requirements",
    "other": "Ambiguity in the other parts of the specification",
}


def print_dry_run_output(plain_source_tree: dict, render_range: Optional[list[str]]):
    frid = plain_spec.get_first_frid(plain_source_tree)

    while frid is not None:
        is_inside_range = render_range is None or frid in render_range

        if is_inside_range:
            specifications, _ = plain_spec.get_specifications_for_frid(plain_source_tree, frid)
            functional_requirement_text = specifications[plain_spec.FUNCTIONAL_REQUIREMENTS][-1]
            console.info(
                "-------------------------------------\n"
                f"Rendering functional requirement {frid}\n"
                f"{functional_requirement_text}\n"
                "-------------------------------------\n"
            )
            if plain_spec.ACCEPTANCE_TESTS in specifications:
                for i, acceptance_test in enumerate(specifications[plain_spec.ACCEPTANCE_TESTS], 1):
                    console.info(f"Generating acceptance test #{i}:\n\n{acceptance_test}\n")
        else:
            console.info(
                "-------------------------------------\n"
                f"Skipping rendering iteration: {frid}\n"
                "-------------------------------------"
            )

        frid = plain_spec.get_next_frid(plain_source_tree, frid)
