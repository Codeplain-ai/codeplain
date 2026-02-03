import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import frontmatter
import mistletoe
import mistletoe.block_token
from liquid2 import DictLoader, Environment
from mistletoe.block_token import List, Paragraph, Quote
from mistletoe.markdown_renderer import Fragment, MarkdownRenderer
from mistletoe.span_token import Emphasis, Link, RawText, Strong
from mistletoe.utils import traverse

import concept_utils
import file_utils
import plain_spec
from plain2code_exceptions import ModuleDoesNotExistError, PlainSyntaxError
from plain2code_nodes import Plain2CodeIncludeTag, Plain2CodeLoaderMixin

RESOURCE_MARKER = "[resource]"

IMPORT_DIRECTIVE = "import"
REQUIRES_DIRECTIVE = "requires"
REQUIRED_CONCEPTS_DIRECTIVE = "required_concepts"
EXPORTED_CONCEPTS_DIRECTIVE = "exported_concepts"

PLAIN_SOURCE_FILE_EXTENSION = ".plain"

PLAIN_SOURCE_TEMPLATE = {
    plain_spec.DEFINITIONS: None,
    plain_spec.NON_FUNCTIONAL_REQUIREMENTS: None,
    plain_spec.TEST_REQUIREMENTS: None,
}


@dataclass
class PlainFileParseResult:
    plain_source: dict
    plain_source_obj: frontmatter.Post
    required_modules: list[str]
    required_concepts: list[str]


class PlainRenderer(MarkdownRenderer):
    def render_link(self, token: Link) -> Iterable[Fragment]:
        yield from self.embed_span(
            Fragment(f"`{RESOURCE_MARKER}"),
            token.children,
            Fragment("`"),
        )


def remove_quotes(token):
    # If the token has no children, there's nothing to remove.
    if not hasattr(token, "children") or token.children is None:
        return

    # Convert children to a list for easy filtering.
    children_list = list(token.children)

    # Build a filtered list that excludes Quote tokens.
    new_children = []
    for child in children_list:
        if not isinstance(child, Quote):
            # Recursively remove quotes in any nested children.
            remove_quotes(child)
            new_children.append(child)

    # Convert the filtered list back to a tuple (or a list, if you prefer).
    # Mistletoe tokens often expect a tuple, so tuple is safest.
    token.children = tuple(new_children)


def check_section_for_linked_resources(section):
    linked_resources = []
    for link in traverse(section, klass=Link):
        parsed_url = urlparse(link.node.target)
        if parsed_url.scheme != "" or os.path.isabs(link.node.target):
            raise PlainSyntaxError(
                f"Only relative links are allowed (text: {link.node.children[0].content}, target: {link.node.target})."
            )

        if len(link.node.children) != 1:
            raise PlainSyntaxError(f"Link must have text specified (link: {link.node.target}).")

        linked_resources.append({"text": link.node.children[0].content, "target": link.node.target})

    if linked_resources:
        section.linked_resources = linked_resources


def check_for_linked_resources(plain_source):
    for specification_heading in plain_spec.ALLOWED_SPECIFICATION_HEADINGS:
        if specification_heading in plain_source and hasattr(plain_source[specification_heading], "children"):
            for requirement in plain_source[specification_heading].children:
                check_section_for_linked_resources(requirement)

                if hasattr(requirement, plain_spec.ACCEPTANCE_TESTS):
                    for acceptance_test in requirement.acceptance_tests:
                        check_section_for_linked_resources(acceptance_test)


def process_section_code_variables(section, code_variables):
    if "markdown" not in section:
        return

    code_variables_array = []
    for key, value in code_variables.items():
        if key not in section["markdown"]:
            continue

        variable_name = next(iter(value))
        section["markdown"] = section["markdown"].replace(key, f"{{{{ {variable_name} }}}}")

        code_variables_array.append({"name": variable_name, "value": value[variable_name]})

    if code_variables_array:
        section["code_variables"] = code_variables_array


def process_code_variables(plain_source, code_variables):
    for specification_heading in plain_spec.ALLOWED_SPECIFICATION_HEADINGS:
        if specification_heading in plain_source:
            for requirement in plain_source[specification_heading]:
                process_section_code_variables(requirement, code_variables)


def check_if_functional_requirements_are_specified(plain_source, non_functional_requirements):
    if plain_source[plain_spec.NON_FUNCTIONAL_REQUIREMENTS] is not None and hasattr(
        plain_source[plain_spec.NON_FUNCTIONAL_REQUIREMENTS], "children"
    ):
        non_functional_requirements.extend(plain_source[plain_spec.NON_FUNCTIONAL_REQUIREMENTS].children)

    found_functional_requirements = plain_spec.FUNCTIONAL_REQUIREMENTS in plain_source
    if found_functional_requirements and len(non_functional_requirements) == 0:
        raise PlainSyntaxError("Syntax error: Functional requirement with no non-functional requirements specified.")

    return found_functional_requirements


def _is_acceptance_test_heading(token) -> tuple[bool, str | None]:
    """
    Check if token is an 'Acceptance test:' heading.

    This method is going to evaluate to True when:

    - The token is a Paragraph
    - The paragraph has 1 child
    - The child is an Emphasis (*** ... *** marker)
    - The Emphasis has 1 child (the raw text content)
    - The child of the Emphasis is a Strong
    - The Strong has 1 child
    - The child of the Strong is a RawText
    - The RawText content is equal to "Acceptance test:"

    This is expected structure for the acceptance test heading.
    """
    if not isinstance(token, Paragraph):
        return False, None

    # Check for the specific structure
    if (
        len(token.children) == 1
        and isinstance(token.children[0], Emphasis)
        and len(token.children[0].children) == 1
        and isinstance(token.children[0].children[0], Strong)
        and len(token.children[0].children[0].children) == 1
        and isinstance(token.children[0].children[0].children[0], RawText)
    ):

        # Check the actual text content
        content = token.children[0].children[0].children[0].content.strip()
        if content == plain_spec.ACCEPTANCE_TEST_HEADING:
            return True, None
        problem = f"Syntax error at line {token.line_number}: Invalid acceptance test heading (`{content}`). Expected: `{plain_spec.ACCEPTANCE_TEST_HEADING}.`"
        return False, problem

    return False, None


def _process_single_acceptance_test_requirement(functional_requirement: mistletoe.block_token.ListItem):
    """
    Process a single functional requirement to extract acceptance tests.

    Expected functional_requirement properties:
    - Is a list item
    - If acceptance tests are specified, it has 3 children:
        - List item element with functional requirement instructions/text
        - Paragraph with `***Acceptance test:***` heading
        - List of acceptance tests
    - If acceptance tests are not specified, it has 1 child:
        - List item element with functional requirement instructions/text
    """
    new_children = []
    functional_requirement_children = iter(functional_requirement.children)
    acceptance_tests_found_already = False

    for functional_requirement_child in functional_requirement_children:
        is_acceptance_test_heading, acceptance_test_heading_problem = _is_acceptance_test_heading(
            functional_requirement_child
        )
        if acceptance_test_heading_problem:
            # Handle the case when the heading is not valid. This case includes cases such as:
            # - Writing `acceptance test` instead of `acceptance tests` (or any other syntax diffs).
            # - Instead of specifying `acceptance tests` below the functional requirement, creator of the plain file
            #   might have specified some other building block (e.g. `implementation reqs`)
            raise PlainSyntaxError(acceptance_test_heading_problem)

        if is_acceptance_test_heading:
            if acceptance_tests_found_already:
                # Handle edge case of duplicated ***acceptance tests*** heading
                raise PlainSyntaxError(
                    f"Syntax error at line {functional_requirement_child.line_number}: Duplicate 'acceptance tests' heading found within the same functional requirement. Only one block of acceptance tests is allowed per functional requirement."
                )

            try:
                # If there is an acceptance test heading, the next token should be a list, with children being list items
                next_token = next(functional_requirement_children)
                if isinstance(next_token, List):
                    # Found valid acceptance test list -> Assign it property acceptance_tests and append all list items to it
                    if not hasattr(functional_requirement, plain_spec.ACCEPTANCE_TESTS):
                        functional_requirement.acceptance_tests = []
                    for list_item in next_token.children:
                        functional_requirement.acceptance_tests.append(list_item)
                    acceptance_tests_found_already = True
                else:
                    # Not followed by a list, keep both tokens
                    new_children.append(functional_requirement_child)
                    new_children.append(next_token)
            except StopIteration:
                # No next token, keep this one
                new_children.append(functional_requirement_child)
        else:
            # Regular token, keep it
            new_children.append(functional_requirement_child)

    # Assign the children property to all the children of the functional requirement from previous, with exception
    # of those we parsed as acceptance tests
    functional_requirement.children = type(functional_requirement.children)(new_children)


def process_acceptance_tests(plain_source):
    # Early returns for cases without functional requirements
    if plain_spec.FUNCTIONAL_REQUIREMENTS not in plain_source:
        return
    frs = plain_source[plain_spec.FUNCTIONAL_REQUIREMENTS]
    if not hasattr(frs, "children"):
        return

    # Process each functional requirement
    for functional_requirement in frs.children:
        if not hasattr(functional_requirement, "children"):
            continue

        # Process each requirement to extract acceptance tests
        _process_single_acceptance_test_requirement(functional_requirement)


def get_raw_text(token):
    if isinstance(token, RawText):
        yield token.content
    elif hasattr(token, "children"):
        if token.children is None:
            return
        for child in token.children:
            yield from get_raw_text(child)
    elif hasattr(token, "content"):
        yield token.content
    else:
        raise Exception(f"Unknown token type: {type(token)}")


def marshall_plain_source(input_plain_source):
    plain_source = {}
    with PlainRenderer() as renderer:
        if "ID" in input_plain_source:
            plain_source["ID"] = input_plain_source["ID"]

        if "Heading" in input_plain_source:
            plain_source["Heading"] = input_plain_source["Heading"]

        for specification_heading in plain_spec.ALLOWED_SPECIFICATION_HEADINGS:
            if specification_heading in input_plain_source and hasattr(
                input_plain_source[specification_heading], "children"
            ):
                list_of_requirements = []
                for requirement in input_plain_source[specification_heading].children:
                    requirement_section = {"markdown": renderer.render(requirement).strip()}
                    if hasattr(requirement, "linked_resources"):
                        requirement_section["linked_resources"] = requirement.linked_resources

                    if hasattr(requirement, plain_spec.ACCEPTANCE_TESTS):
                        requirement_section[plain_spec.ACCEPTANCE_TESTS] = []
                        for acceptance_test in requirement.acceptance_tests:
                            acceptance_test_section = {"markdown": renderer.render(acceptance_test).strip()}
                            if hasattr(acceptance_test, "linked_resources"):
                                acceptance_test_section["linked_resources"] = acceptance_test.linked_resources
                            requirement_section[plain_spec.ACCEPTANCE_TESTS].append(acceptance_test_section)

                    list_of_requirements.append(requirement_section)

                plain_source[specification_heading] = list_of_requirements

    return plain_source


class Plain2CodeDictLoader(Plain2CodeLoaderMixin, DictLoader):
    pass


def render_plain_source(plain_source, loaded_templates, code_variables):
    env = Environment(loader=Plain2CodeDictLoader(loaded_templates))
    env.tags["include"] = Plain2CodeIncludeTag(env)
    env.filters["code_variable"] = plain_spec.code_variable_liquid_filter
    env.filters["prohibited_chars"] = plain_spec.prohibited_chars_liquid_filter

    template = env.from_string(plain_source)

    return template.render(code_variables=code_variables)


def process_imports(
    plain_source: dict,
    imports: list[str],
    code_variables: dict,
    template_dirs: list[str],
    imported_modules: list[str],
    modules_trace: list[str],
) -> list[str]:
    required_concepts = list[str]()
    for module_name in imports:
        if module_name in modules_trace:
            raise PlainSyntaxError(f"Circular import detected: {module_name}.")

        if module_name in imported_modules:
            continue

        plain_file_parse_result = parse_plain_file(
            module_name, code_variables, template_dirs, imported_modules, modules_trace
        )

        if check_if_functional_requirements_are_specified(plain_file_parse_result.plain_source, []):
            raise PlainSyntaxError("Imported module must not contain functional requirements.")

        for specification_heading in plain_file_parse_result.plain_source:
            if specification_heading not in plain_spec.ALLOWED_IMPORT_SPECIFICATION_HEADINGS:
                raise PlainSyntaxError(
                    f"Syntax error: Invalid specification heading (`{specification_heading}`). Allowed headings: {', '.join(plain_spec.ALLOWED_IMPORT_SPECIFICATION_HEADINGS)}"
                )

            if plain_source[specification_heading] is None:
                plain_source[specification_heading] = plain_file_parse_result.plain_source[specification_heading]
            elif plain_file_parse_result.plain_source[specification_heading] is not None:
                plain_source[specification_heading].children.extend(
                    plain_file_parse_result.plain_source[specification_heading].children
                )

        if REQUIRED_CONCEPTS_DIRECTIVE in plain_file_parse_result.plain_source_obj.metadata:
            for item in plain_file_parse_result.plain_source_obj.metadata[REQUIRED_CONCEPTS_DIRECTIVE]:
                assert isinstance(
                    item, str
                ), f"Syntax error: Invalid {REQUIRED_CONCEPTS_DIRECTIVE} metadata. Expected a string."
                new_concepts, _ = concept_utils.extract_concepts_from_definition(item)
                required_concepts.extend(new_concepts)

            required_concepts.extend(plain_file_parse_result.required_concepts)

        imported_modules.append(module_name)

    return required_concepts


def read_plain_source_metadata(plain_source_text):
    try:
        plain_source_obj = frontmatter.loads(plain_source_text)
    except Exception as e:
        raise PlainSyntaxError(f"Syntax error: Invalid frontmatter: {e}")

    for directive in [EXPORTED_CONCEPTS_DIRECTIVE, REQUIRED_CONCEPTS_DIRECTIVE]:
        if directive in plain_source_obj.metadata:
            assert isinstance(
                plain_source_obj.metadata[directive], list
            ), f"Syntax error: Invalid {directive} metadata. Expected a list."
            prepared_metadata = []
            for item in plain_source_obj.metadata[directive]:
                if isinstance(item, dict):
                    for k, v in item.items():
                        prepared_metadata.append(f"- {k}: {v}")
                elif isinstance(item, str):
                    prepared_metadata.append(f"- {item}")
                else:
                    raise PlainSyntaxError(
                        f"Syntax error: Invalid {directive} metadata. Expected a dictionary or a string."
                    )

            plain_source_obj.metadata[directive] = prepared_metadata

    return plain_source_obj


def parse_plain_source(  # noqa: C901
    plain_source_text: str,
    code_variables: dict,
    template_dirs: list[str],
    imported_modules: list[str],
    modules_trace: list[str],
) -> PlainFileParseResult:
    plain_source_obj = read_plain_source_metadata(plain_source_text)

    plain_source = PLAIN_SOURCE_TEMPLATE.copy()

    if IMPORT_DIRECTIVE in plain_source_obj.metadata:
        required_concepts = process_imports(
            plain_source,
            plain_source_obj.metadata[IMPORT_DIRECTIVE],
            code_variables,
            template_dirs,
            imported_modules,
            modules_trace,
        )
    else:
        required_concepts = list[str]()

    [_, loaded_templates] = file_utils.get_loaded_templates(template_dirs, plain_source_text)

    plain_source_full_text = render_plain_source(plain_source_obj.content, loaded_templates, code_variables)

    plain_file = mistletoe.Document(io.StringIO(plain_source_full_text))

    remove_quotes(plain_file)

    current_specification_heading = None
    processed_specification_headings = set[str]()
    for token in plain_file.children:
        token_text = "".join(get_raw_text(token)).strip()
        if isinstance(token, Paragraph):
            if not (
                len(token.children) == 1
                and isinstance(token.children[0], Emphasis)
                and isinstance(token.children[0], Emphasis)
                and len(token.children[0].children) == 1
                and isinstance(token.children[0].children[0], Strong)
            ):

                raise PlainSyntaxError(
                    f"Syntax error at line {token.line_number}: Invalid specification (`{token_text}`)"
                )

            specification_heading = token.children[0].children[0].children[0].content

            if specification_heading not in plain_spec.ALLOWED_SPECIFICATION_HEADINGS:
                raise PlainSyntaxError(
                    f"Syntax error at line {token.line_number}: Invalid specification heading (`{specification_heading}`). Allowed headings: {', '.join(plain_spec.ALLOWED_SPECIFICATION_HEADINGS)}"
                )

            if (
                specification_heading == plain_spec.FUNCTIONAL_REQUIREMENTS
                and specification_heading not in plain_source
            ):
                plain_source[specification_heading] = None

            if specification_heading in processed_specification_headings:
                raise PlainSyntaxError(
                    f"Syntax error at line {token.line_number}: Duplicate specification heading (`{specification_heading}`)"
                )

            if specification_heading == plain_spec.DEFINITIONS and current_specification_heading is not None:
                raise PlainSyntaxError(
                    f"Syntax error at line {token.line_number}: Definitions specification must be the first specification in the section  (`{token_text}`)"
                )

            current_specification_heading = specification_heading
            if plain_source[current_specification_heading] is None:
                plain_source[current_specification_heading] = mistletoe.Document("")

            processed_specification_headings.add(current_specification_heading)
        elif isinstance(token, List):
            if current_specification_heading is None:
                raise PlainSyntaxError(
                    f"Syntax error at line {token.line_number}: Missing specification heading (`{token_text}`)"
                )
            plain_source[current_specification_heading].children.extend(token.children)

        else:
            raise PlainSyntaxError(
                f"Syntax error at line {token.line_number}: Invalid source structure  (`{token_text}`)"
            )

    if plain_source[plain_spec.DEFINITIONS] is not None:
        with PlainRenderer() as renderer:
            for token in plain_source[plain_spec.DEFINITIONS].children:
                rendered_token = renderer.render(token)
                new_concepts, _ = concept_utils.extract_concepts_from_definition(rendered_token)
                for concept in new_concepts:
                    if concept in required_concepts:
                        required_concepts.remove(concept)

    required_modules = []
    if REQUIRES_DIRECTIVE in plain_source_obj.metadata:
        required_modules = plain_source_obj.metadata[REQUIRES_DIRECTIVE]

    return PlainFileParseResult(
        plain_source=plain_source,
        plain_source_obj=plain_source_obj,
        required_modules=required_modules,
        required_concepts=required_concepts,
    )


def read_module_plain_source(module_name: str, template_dirs: list[str]) -> str:
    plain_source_text = file_utils.open_from(template_dirs, module_name + PLAIN_SOURCE_FILE_EXTENSION)
    if plain_source_text is None:
        raise ModuleDoesNotExistError(f"Module does not exist ({module_name}).")
    return plain_source_text


def parse_plain_file(
    module_name: str,
    code_variables: dict,
    template_dirs: list[str],
    imported_modules: list[str],
    modules_trace: list[str],
) -> PlainFileParseResult:  # noqa: C901
    plain_source_text = read_module_plain_source(module_name, template_dirs)

    return parse_plain_source(
        plain_source_text,
        code_variables,
        template_dirs,
        imported_modules,
        modules_trace + [module_name],
    )


def process_required_modules(
    required_modules: list[str],
    code_variables: dict,
    template_dirs: list[str],
    all_required_modules: list[str],
    modules_trace: list[str],
) -> list[mistletoe.block_token.token]:
    exported_definitions = list[mistletoe.block_token.token]()
    for module_name in required_modules:
        if module_name in modules_trace:
            raise PlainSyntaxError(f"Circular required module detected: {module_name}.")

        if len(all_required_modules) > 0 and module_name == all_required_modules[-1]:
            continue

        plain_file_parse_result = parse_plain_file(
            module_name, code_variables, template_dirs, imported_modules=[], modules_trace=[]
        )

        if len(plain_file_parse_result.required_modules) == 0:
            if len(all_required_modules) > 0:
                # For now we require that there is fixed order how required modules are dependent.
                # In the future we will support the cases where required modules can be rendered independently
                # and then merged (somehow).
                raise PlainSyntaxError(
                    f"There must be a fixed order how required modules are dependent ({module_name})."
                )
        else:
            process_required_modules(
                plain_file_parse_result.required_modules,
                code_variables,
                template_dirs,
                all_required_modules,
                modules_trace + [module_name],
            )

        if EXPORTED_CONCEPTS_DIRECTIVE in plain_file_parse_result.plain_source_obj.metadata:
            exported_concepts = list[str]()
            for concept in plain_file_parse_result.plain_source_obj.metadata[EXPORTED_CONCEPTS_DIRECTIVE]:
                if concept in concept_utils.DEFAULT_CONCEPTS:
                    raise PlainSyntaxError(
                        f"Syntax error: Default concept cannot be exported: {concept}. Only user-defined concepts can be exported."
                    )

                if isinstance(concept, str):
                    exported_concepts.extend(concept_utils.extract_concepts_from_definition(concept)[0])
                else:
                    raise PlainSyntaxError(f"Syntax error: Invalid exported concept: {concept}.")

            with PlainRenderer() as renderer:
                for exported_concept in exported_concepts:
                    exported_definitions.extend(
                        concept_utils.find_concept_definitions_in_plain_source(
                            exported_concept, plain_file_parse_result.plain_source, renderer
                        )
                    )

        all_required_modules.append(module_name)

    return exported_definitions


def process_exported_definitions(plain_source: dict, exported_definitions: list[mistletoe.block_token.token]) -> None:
    if len(exported_definitions) == 0:
        return

    with PlainRenderer() as renderer:
        for exported_definition in exported_definitions:
            add_defintion = True

            exported_rendered_definition = renderer.render(exported_definition).strip()
            for definition in plain_source[plain_spec.DEFINITIONS].children:
                rendered_definition = renderer.render(definition).strip()
                if exported_rendered_definition == rendered_definition:
                    add_defintion = False
                    break

            if add_defintion:
                plain_source[plain_spec.DEFINITIONS].children.append(exported_definition)


def plain_file_parser(  # noqa: C901
    plain_source_file_name: str,
    template_dirs: list[str],
) -> tuple[str, dict, list[str]]:
    # code_variables are used to pass code variables to the plain source
    # they need to be passed as an argument to the function because they populated when liquid templating is applied
    # and we need to pass them to the marshalled_plain_source_tree after it's rendered
    plain_source_file_path = Path(plain_source_file_name)
    if plain_source_file_path.suffix != PLAIN_SOURCE_FILE_EXTENSION:
        raise PlainSyntaxError(
            f"Invalid plain file extension: {plain_source_file_path.suffix}. Expected: {PLAIN_SOURCE_FILE_EXTENSION}."
        )

    module_name = plain_source_file_path.stem

    code_variables = {}

    plain_file_parse_result = parse_plain_file(
        module_name,
        code_variables,
        template_dirs,
        imported_modules=[],
        modules_trace=[],
    )

    if len(plain_file_parse_result.required_concepts) > 0:
        missing_required_concepts_msg = "Missing required concepts: "
        missing_required_concepts_msg += ", ".join(plain_file_parse_result.required_concepts)
        raise PlainSyntaxError(
            f"Syntax error: Not all required concepts were defined. {missing_required_concepts_msg}."
        )

    if not check_if_functional_requirements_are_specified(plain_file_parse_result.plain_source, []):
        raise PlainSyntaxError("Syntax error: No functional requirements specified.")

    exported_definitions = process_required_modules(
        plain_file_parse_result.required_modules,
        code_variables={},
        template_dirs=template_dirs,
        all_required_modules=[],
        modules_trace=[],
    )

    process_exported_definitions(plain_file_parse_result.plain_source, exported_definitions)

    process_acceptance_tests(plain_file_parse_result.plain_source)

    check_for_linked_resources(plain_file_parse_result.plain_source)

    marshalled_plain_source = marshall_plain_source(plain_file_parse_result.plain_source)

    process_code_variables(marshalled_plain_source, code_variables)

    validation_errors = concept_utils.validate_concepts(marshalled_plain_source)
    if len(validation_errors) > 0:
        errors_msg = "\n".join(validation_errors)
        msg = f"Found {len(validation_errors)} errors in the plain file:\n{errors_msg}"
        raise PlainSyntaxError(msg)

    if plain_spec.DEFINITIONS in marshalled_plain_source:
        concept_utils.sort_definitions(marshalled_plain_source[plain_spec.DEFINITIONS])

    return module_name, marshalled_plain_source, plain_file_parse_result.required_modules
