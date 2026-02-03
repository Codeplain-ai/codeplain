import hashlib
import json
import uuid
from typing import Optional

from liquid2.filter import with_context

from plain2code_exceptions import InvalidLiquidVariableName

DEFINITIONS = "definitions"
NON_FUNCTIONAL_REQUIREMENTS = "implementation reqs"
TEST_REQUIREMENTS = "test reqs"
FUNCTIONAL_REQUIREMENTS = "functional specs"
ACCEPTANCE_TESTS = "acceptance_tests"
ACCEPTANCE_TEST_HEADING = "acceptance tests"

ALLOWED_SPECIFICATION_HEADINGS = [
    DEFINITIONS,
    NON_FUNCTIONAL_REQUIREMENTS,
    TEST_REQUIREMENTS,
    FUNCTIONAL_REQUIREMENTS,
    ACCEPTANCE_TEST_HEADING,
]

ALLOWED_IMPORT_SPECIFICATION_HEADINGS = [DEFINITIONS, NON_FUNCTIONAL_REQUIREMENTS, TEST_REQUIREMENTS]


def collect_specification_linked_resources(specification, specification_heading, linked_resources_list):
    linked_resources = []
    if "linked_resources" in specification:
        linked_resources.extend(specification["linked_resources"])

    for resource in linked_resources:
        resource_found = False
        for resource_map in linked_resources_list:
            if resource["text"] == resource_map["text"]:
                if resource["target"] != resource_map["target"]:
                    raise Exception(
                        f"The file {resource['target']} is linked to multiple linked resources with the same text: {resource['text']}"
                    )
            elif resource["target"] == resource_map["target"]:
                if resource["text"] != resource_map["text"]:
                    raise Exception(
                        f"The linked resource {resource['text']} is linked to multiple files: {resource_map['target']}"
                    )
            else:
                continue

            if resource_found:
                raise Exception(
                    "Duplicate linked resource found: " + resource["text"] + " (" + resource["target"] + ")"
                )

            resource_found = True
            resource_map["sections"].append(specification_heading)

        if not resource_found:
            linked_resources_list.append(
                {"text": resource["text"], "target": resource["target"], "sections": [specification_heading]}
            )


def collect_linked_resources_in_section(
    section, linked_resources_list, specifications_list, include_acceptance_tests, frid=Optional[str]
):
    # When should we collect specification headings in the current section. Either when one of the following holds true:
    # - frid wasn't specified
    # - section has no ID (and that's exactly when it's the root section)
    # - section has ID, frid was specified and the specified frid inside the current section tree
    should_collect_specification_headings_in_current_section = (
        frid is None or "ID" not in section or frid.startswith(section["ID"])
    )
    if should_collect_specification_headings_in_current_section:
        specifications_to_collect = list(set([DEFINITIONS, NON_FUNCTIONAL_REQUIREMENTS, TEST_REQUIREMENTS]))
        if specifications_list:
            specifications_to_collect = list(set(specifications_to_collect) & set(specifications_list))
        for specification_heading in specifications_to_collect:
            if specification_heading in section:
                for requirement in section[specification_heading]:
                    collect_specification_linked_resources(requirement, specification_heading, linked_resources_list)

    if FUNCTIONAL_REQUIREMENTS in section and (
        not specifications_list or FUNCTIONAL_REQUIREMENTS in specifications_list
    ):
        functional_requirement_count = 0
        for requirement in section[FUNCTIONAL_REQUIREMENTS]:
            collect_specification_linked_resources(requirement, FUNCTIONAL_REQUIREMENTS, linked_resources_list)

            functional_requirement_count += 1
            section_id = section.get("ID", None)
            current_frid = get_current_frid(section_id, functional_requirement_count)

            if ACCEPTANCE_TESTS in requirement and include_acceptance_tests and (frid is None or frid == current_frid):
                for acceptance_test in requirement[ACCEPTANCE_TESTS]:
                    collect_specification_linked_resources(
                        acceptance_test, FUNCTIONAL_REQUIREMENTS, linked_resources_list
                    )

            if frid is not None and current_frid == frid:
                # here, we rely on the fact that FRIDs are incrementing. And if we have reached the current FRID, we should
                # not collect any FRIDs after the current one.
                return True

    if "sections" in section:
        for subsection in section["sections"]:
            if collect_linked_resources_in_section(
                subsection, linked_resources_list, specifications_list, include_acceptance_tests, frid
            ):
                return True

    return False


# TODO: check if this function can be refactored to return the list of linked resources instead of modifying it in place
def collect_linked_resources(
    plain_source_tree, linked_resources_list, specifications_list, include_acceptance_tests, frid=None
):

    if not isinstance(plain_source_tree, dict):
        raise ValueError("[plain_source_tree must be a dictionary.")

    if frid is not None:
        functional_requirements = get_frids(plain_source_tree)
        if frid not in functional_requirements:
            raise ValueError(f"frid {frid} does not exist.")

    result = collect_linked_resources_in_section(
        plain_source_tree, linked_resources_list, specifications_list, include_acceptance_tests, frid
    )

    # Sort linked_resources_list by the "text" field
    linked_resources_list.sort(key=lambda x: x["text"])

    return result


def get_frids(plain_source_tree):
    if FUNCTIONAL_REQUIREMENTS in plain_source_tree:
        for functional_requirement_id in range(1, len(plain_source_tree[FUNCTIONAL_REQUIREMENTS]) + 1):
            if "ID" in plain_source_tree:
                yield plain_source_tree["ID"] + "." + str(functional_requirement_id)
            else:
                yield str(functional_requirement_id)

    if "sections" in plain_source_tree:
        for section in plain_source_tree["sections"]:
            yield from get_frids(section)


def get_first_frid(plain_source_tree):
    return next(get_frids(plain_source_tree), None)


def get_current_frid(section_id: Optional[str], functional_requirement_count: int) -> str:
    if section_id is None:
        return str(functional_requirement_count)
    else:
        return section_id + "." + str(functional_requirement_count)


def get_next_frid(plain_source_tree, frid):
    functional_requirements = get_frids(plain_source_tree)
    for temp_frid in functional_requirements:
        if temp_frid == frid:
            return next(functional_requirements, None)

    raise Exception(f"Functional requirement {frid} does not exist.")


def get_previous_frid(plain_source_tree, frid):
    previous_frid = None
    for temp_frid in get_frids(plain_source_tree):
        if temp_frid == frid:
            return previous_frid

        previous_frid = temp_frid

    raise Exception(f"Functional requirement {frid} does not exist.")


def get_frids_before(plain_source_tree, target_frid: str) -> list[str]:
    """
    Get all FRIDs that appear before the target FRID in the specification.

    Args:
        plain_source_tree: The plain source tree
        target_frid: The FRID to find predecessors for

    Returns:
        List of FRIDs that appear before target_frid, in order
    """
    previous_frids = []
    for frid in get_frids(plain_source_tree):
        if frid == target_frid:
            break
        previous_frids.append(frid)

    return previous_frids


def get_specification_item_markdown(specification_item, code_variables, replace_code_variables):
    markdown = specification_item["markdown"]
    if "code_variables" in specification_item:
        for code_variable in specification_item["code_variables"]:
            if code_variable["name"] in code_variables:
                if code_variables[code_variable["name"]] != code_variable["value"]:
                    raise Exception(
                        f"Code variable {code_variable['name']} has multiple values: {code_variables[code_variable['name']]} and {code_variable['value']}"
                    )
            else:
                code_variables[code_variable["name"]] = code_variable["value"]

            if replace_code_variables:
                markdown = markdown.replace(f"{{{{ {code_variable['name']} }}}}", code_variable["value"])

    return markdown


def get_specifications_from_plain_source_tree(
    frid,
    plain_source_tree,
    definitions,
    non_functional_requirements,
    test_requirements,
    functional_requirements,
    acceptance_tests,
    code_variables,
    replace_code_variables,
    section_id=None,
):
    return_frid = None
    if FUNCTIONAL_REQUIREMENTS in plain_source_tree and len(plain_source_tree[FUNCTIONAL_REQUIREMENTS]) > 0:
        functional_requirement_count = 0
        for functional_requirement in plain_source_tree[FUNCTIONAL_REQUIREMENTS]:
            functional_requirement_count += 1
            if section_id is None:
                current_frid = str(functional_requirement_count)
            else:
                current_frid = section_id + "." + str(functional_requirement_count)

            functional_requirements.append(
                get_specification_item_markdown(functional_requirement, code_variables, replace_code_variables)
            )

            if current_frid == frid:
                if ACCEPTANCE_TESTS in functional_requirement:
                    for acceptance_test in functional_requirement[ACCEPTANCE_TESTS]:
                        acceptance_tests.append(
                            get_specification_item_markdown(acceptance_test, code_variables, replace_code_variables)
                        )

                return_frid = current_frid
                break

    if "sections" in plain_source_tree:
        for section in plain_source_tree["sections"]:
            sub_frid = get_specifications_from_plain_source_tree(
                frid,
                section,
                definitions,
                non_functional_requirements,
                test_requirements,
                functional_requirements,
                acceptance_tests,
                code_variables,
                replace_code_variables,
                section["ID"],
            )
            if sub_frid is not None:
                return_frid = sub_frid
                break

    if return_frid is not None:
        if DEFINITIONS in plain_source_tree and plain_source_tree[DEFINITIONS] is not None:
            definitions[0:0] = [
                get_specification_item_markdown(specification, code_variables, replace_code_variables)
                for specification in plain_source_tree[DEFINITIONS]
            ]
        if (
            NON_FUNCTIONAL_REQUIREMENTS in plain_source_tree
            and plain_source_tree[NON_FUNCTIONAL_REQUIREMENTS] is not None
        ):
            non_functional_requirements[0:0] = [
                get_specification_item_markdown(specification, code_variables, replace_code_variables)
                for specification in plain_source_tree[NON_FUNCTIONAL_REQUIREMENTS]
            ]
        if TEST_REQUIREMENTS in plain_source_tree and plain_source_tree[TEST_REQUIREMENTS] is not None:
            test_requirements[0:0] = [
                get_specification_item_markdown(specification, code_variables, replace_code_variables)
                for specification in plain_source_tree[TEST_REQUIREMENTS]
            ]

    return return_frid


def get_specifications_for_frid(plain_source_tree, frid, replace_code_variables=True):
    definitions = []
    non_functional_requirements = []
    test_requirements = []
    functional_requirements = []
    acceptance_tests = []

    code_variables = {}

    result = get_specifications_from_plain_source_tree(
        frid,
        plain_source_tree,
        definitions,
        non_functional_requirements,
        test_requirements,
        functional_requirements,
        acceptance_tests,
        code_variables,
        replace_code_variables,
    )
    if result is None:
        raise Exception(f"Functional requirement {frid} does not exist.")

    specifications = {
        DEFINITIONS: definitions,
        NON_FUNCTIONAL_REQUIREMENTS: non_functional_requirements,
        TEST_REQUIREMENTS: test_requirements,
        FUNCTIONAL_REQUIREMENTS: functional_requirements,
    }

    if acceptance_tests:
        specifications[ACCEPTANCE_TESTS] = acceptance_tests

    if code_variables:
        return specifications, code_variables
    else:
        return specifications, None


@with_context
def code_variable_liquid_filter(value, *, context):
    if len(context.scope) == 0:
        raise Exception("Invalid use of code_variable filter!")

    if "code_variables" in context.globals:
        code_variables = context.globals["code_variables"]

        variable = next(iter(context.scope.items()))

        unique_str = uuid.uuid4().hex

        code_variables[unique_str] = {variable[0]: value}

        return unique_str
    else:

        return value


@with_context
def prohibited_chars_liquid_filter(value, prohibited_chars, *, context):
    if not isinstance(value, str):
        value = str(value)

    if len(context.scope) == 0:
        raise Exception("Invalid use of prohibited_chars filter!")

    variable = next(iter(context.scope.items()))
    variable_name = variable[0]

    for char in prohibited_chars:
        if char in value:
            raise InvalidLiquidVariableName(
                f"'{char}' is not a valid character for variable '{variable_name}' (value: '{value}')."
            )

    return value


def hash_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def get_hash_value(specifications):
    return hash_text(json.dumps(specifications, indent=4))
