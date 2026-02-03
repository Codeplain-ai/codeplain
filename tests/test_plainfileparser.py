import os
import re
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
from liquid2 import Environment, RenderContext, TemplateSource

import file_utils
import plain_file
import plain_spec
from plain2code_exceptions import PlainSyntaxError


def test_regular_plain_source(get_test_data_path):
    _, plain_sections, _ = plain_file.plain_file_parser(
        "regular_plain_source.plain",
        [get_test_data_path("data/plainfileparser")],
    )
    assert plain_sections == {
        "definitions": [],
        "implementation reqs": [
            {"markdown": "- First non-functional requirement."},
            {"markdown": "- Second non-functional requirement."},
        ],
        "functional specs": [{"markdown": '- Display "hello, world"'}],
    }


def test_unknown_section():
    plain_source = """
***definitions***

***Unknown Section:***
"""
    with pytest.raises(
        Exception,
        match=re.escape(
            "Syntax error at line 3: Invalid specification heading (`Unknown Section:`). Allowed headings: definitions, implementation reqs, test reqs, functional specs, acceptance tests"
        ),
    ):
        plain_file.parse_plain_source(plain_source, {}, [], [], [])


def test_duplicate_section():
    plain_source = """
***definitions***

***definitions***
"""
    with pytest.raises(
        Exception,
        match=re.escape("Syntax error at line 3: Duplicate specification heading (`definitions`)"),
    ):
        plain_file.parse_plain_source(plain_source, {}, [], [], [])


def test_invalid_top_level_element():
    plain_source = """
***definitions***
```
code block
```
"""
    with pytest.raises(
        Exception,
        match=re.escape("Syntax error at line 2: Invalid source structure  (`code block`)"),
    ):
        plain_file.parse_plain_source(plain_source, {}, [], [], [])


def test_plain_file_parser_with_comments(get_test_data_path):
    _, plain_sections, _ = plain_file.plain_file_parser(
        "plain_file_parser_with_comments.plain",
        [get_test_data_path("data/plainfileparser")],
    )
    assert plain_sections == {
        "definitions": [],
        "implementation reqs": [{"markdown": "- Second non-functional requirement."}],
        "functional specs": [{"markdown": '- Display "hello, world"'}],
    }


def test_plain_file_parser_with_comments_indented(get_test_data_path):
    _, plain_sections, _ = plain_file.plain_file_parser(
        "plain_file_with_comments_indented.plain",
        [get_test_data_path("data/plainfileparser")],
    )
    assert plain_sections == {
        "definitions": [],
        "implementation reqs": [
            {"markdown": "- First non-functional requirement."},
            {"markdown": "- Second non-functional requirement."},
        ],
        "functional specs": [{"markdown": '- Display "hello, world"'}],
    }


def test_invalid_url_link(get_test_data_path):
    with pytest.raises(Exception, match="Only relative links are allowed."):
        plain_file.plain_file_parser("plain_source_with_url_link.plain", [get_test_data_path("data/plainfile")])


def test_invalid_absolute_link(get_test_data_path):
    with pytest.raises(Exception, match="Only relative links are allowed."):
        plain_file.plain_file_parser(
            "plain_source_with_absolute_link.plain",
            [get_test_data_path("data/plainfile")],
        )


def test_reference_link_parsing(get_test_data_path):
    _, plain_sections, _ = plain_file.plain_file_parser(
        "task_manager_with_reference_links.plain",
        [get_test_data_path("data/plainfile")],
    )
    asserted_resources = [
        "task_list_ui_specification.yaml",
        "add_new_task_modal_specification.yaml",
    ]
    for functional_requirement in plain_sections[plain_spec.FUNCTIONAL_REQUIREMENTS]:
        if "linked_resources" not in functional_requirement:
            continue

        for resource in functional_requirement["linked_resources"]:
            assert resource["target"] in asserted_resources
            del asserted_resources[asserted_resources.index(resource["target"])]

            parsed_url = urlparse(resource["target"])
            assert parsed_url.scheme == ""

            assert not os.path.isabs(resource["target"])

    assert asserted_resources == []

    assert "`[resource]task_list_ui_specification.yaml`" in "\n".join(
        [item["markdown"] for item in plain_sections[plain_spec.FUNCTIONAL_REQUIREMENTS]]
    )
    assert "`[resource]add_new_task_modal_specification.yaml`" in "\n".join(
        [item["markdown"] for item in plain_sections[plain_spec.FUNCTIONAL_REQUIREMENTS]]
    )

    assert plain_sections[plain_spec.FUNCTIONAL_REQUIREMENTS][1]["linked_resources"] == [
        {
            "text": "task_list_ui_specification.yaml",
            "target": "task_list_ui_specification.yaml",
        }
    ]
    assert plain_sections[plain_spec.FUNCTIONAL_REQUIREMENTS][2]["linked_resources"] == [
        {
            "text": "add_new_task_modal_specification.yaml",
            "target": "add_new_task_modal_specification.yaml",
        }
    ]


def test_invalid_specification_order(get_test_data_path):
    with pytest.raises(
        Exception,
        match="Syntax error at line 6: Definitions specification must be the first specification in the section.",
    ):
        plain_file.plain_file_parser("invalid_specification_order.plain", [get_test_data_path("data/plainfile")])


def test_duplicate_specification_heading(get_test_data_path):
    with pytest.raises(
        Exception,
        match=re.escape("Syntax error at line 6: Duplicate specification heading (`definitions`)"),
    ):
        plain_file.plain_file_parser(
            "duplicate_specification_heading.plain",
            [get_test_data_path("data/plainfile")],
        )


def test_missing_non_functional_requirements(get_test_data_path):
    with pytest.raises(
        Exception,
        match="Syntax error: Functional requirement with no non-functional requirements specified.",
    ):
        plain_file.plain_file_parser(
            "missing_non_functional_requirements.plain",
            [get_test_data_path("data/plainfile")],
        )


def test_without_non_functional_requirement(get_test_data_path):
    with pytest.raises(
        Exception,
        match="Syntax error: Functional requirement with no non-functional requirements specified.",
    ):
        plain_file.plain_file_parser(
            "without_non_functional_requirement.plain",
            [get_test_data_path("data/plainfile")],
        )


def test_indented_include_tags():
    plain_source = """# Main

***definitions***

- This is a definition.

***implementation reqs***
- First non-functional requirement.
- Second non-functional requirement.

> This is a comment
> This is a with an include tag {% include "template.plain" %}

***functional specs***
- Implement {% include "implement.plain" %}
{% include "template.plain" %}
- Display "hello, world"
    {% include "template.plain" %}
        {% include "template.plain" %}
    - Implement {% include "implement.plain" %}
"""
    loaded_templates = {
        "template.plain": "- This is a functional requirement inside a template.",
        "implement.plain": """something nice and useful
    - the nice thing should be really nice
    - the useful thing should be really useful""",
    }

    expected_rendered_plain_source = """# Main

***definitions***

- This is a definition.

***implementation reqs***
- First non-functional requirement.
- Second non-functional requirement.

> This is a comment
> This is a with an include tag {% include 'template.plain' %}

***functional specs***
- Implement something nice and useful
    - the nice thing should be really nice
    - the useful thing should be really useful
- This is a functional requirement inside a template.
- Display "hello, world"
    - This is a functional requirement inside a template.
        - This is a functional requirement inside a template.
    - Implement something nice and useful
        - the nice thing should be really nice
        - the useful thing should be really useful
"""
    rendered_plain_source = plain_file.render_plain_source(plain_source, loaded_templates, {})
    assert rendered_plain_source == expected_rendered_plain_source

    def mock_get_source(
        env: Environment,
        template_name: str,
        *,
        context: RenderContext | None = None,
        **kwargs: object,
    ):
        return TemplateSource(
            loaded_templates[template_name],
            template_name,
            lambda: True,
        )

    with patch(
        "liquid2.builtin.loaders.file_system_loader.FileSystemLoader.get_source",
        side_effect=mock_get_source,
    ):
        plain_source_result, loaded_templates_result = file_utils.get_loaded_templates(["."], plain_source)
        assert plain_source_result == expected_rendered_plain_source
        assert loaded_templates_result == loaded_templates


def test_code_variables(load_test_data, get_test_data_path):
    plain_source = load_test_data("data/templates/code_variables.plain")
    loaded_templates = {
        "implement.plain": load_test_data("data/templates/implement.plain"),
    }

    code_variables = {}
    rendered_plain_source = plain_file.render_plain_source(plain_source, loaded_templates, code_variables)
    keys = list(code_variables.keys())

    expected_rendered_plain_source = f"""***definitions***

- :concept: is a concept.

***implementation reqs***
- First non-functional requirement.
- Second non-functional requirement.

***functional specs***
- Implement something nice and useful
    - the nice thing should be really {keys[0]}
    - the useful thing should be really useful
"""

    assert rendered_plain_source == expected_rendered_plain_source

    _, plain_source, _ = plain_file.plain_file_parser("code_variables.plain", [get_test_data_path("data/templates")])
    expected_plain_source = {
        "definitions": [{"markdown": "- :concept: is a concept."}],
        "implementation reqs": [
            {"markdown": "- First non-functional requirement."},
            {"markdown": "- Second non-functional requirement."},
        ],
        "functional specs": [
            {
                "markdown": "- Implement something nice and useful\n  - the nice thing should be really {{ variable_name }}\n  - the useful thing should be really useful",
                "code_variables": [{"name": "variable_name", "value": "nice"}],
            }
        ],
    }

    assert plain_source == expected_plain_source

    plain_source = load_test_data("data/templates/template_include.plain")
    loaded_templates = {
        "header.plain": load_test_data("data/templates/header.plain"),
        "implement_2.plain": load_test_data("data/templates/implement_2.plain"),
    }

    code_variables = {}
    rendered_plain_source = plain_file.render_plain_source(plain_source, loaded_templates, code_variables)
    keys = list(code_variables.keys())
    expected_rendered_plain_source = f"""***definitions***

- :concept: is a concept.

***implementation reqs***
- First non-functional requirement {keys[0]}.
- Second non-functional requirement {keys[1]}.

***functional specs***
- Implement something nice and useful
    - the nice thing should be really {keys[2]}
    - the useful thing should be really useful
"""

    assert rendered_plain_source == expected_rendered_plain_source

    _, plain_source, _ = plain_file.plain_file_parser("template_include.plain", [get_test_data_path("data/templates")])
    expected_plain_source = {
        "definitions": [{"markdown": "- :concept: is a concept."}],
        "implementation reqs": [
            {
                "markdown": "- First non-functional requirement {{ variable_name_1 }}.",
                "code_variables": [{"name": "variable_name_1", "value": "nice_1"}],
            },
            {
                "markdown": "- Second non-functional requirement {{ variable_name_1 }}.",
                "code_variables": [{"name": "variable_name_1", "value": "nice_2"}],
            },
        ],
        "functional specs": [
            {
                "markdown": "- Implement something nice and useful\n  - the nice thing should be really {{ variable_name_1 }}\n  - the useful thing should be really useful",
                "code_variables": [{"name": "variable_name_1", "value": "nice"}],
            }
        ],
    }

    assert plain_source == expected_plain_source


def test_acceptance_tests_block_include_with_trailing_newline_keeps_structure_and_ignores_quote(get_test_data_path):
    """
    Ensures that a block-level include inside ***acceptance tests*** whose template ends
    with a trailing newline does not terminate the list, and that a following '> ...' line
    is treated as a comment and ignored. The parser should not raise a syntax error.
    It's an error we encountered while implementing custom rendering and should break in case of regression.
    """
    plain_file.plain_file_parser("block_level_include.plain", [get_test_data_path("data/templates")])


def test_concept_validation_definitions(get_test_data_path):
    plain_file.plain_file_parser(
        "concept_validation_definition.plain",
        [get_test_data_path("data/plainfileparser")],
    )

    with pytest.raises(PlainSyntaxError):
        plain_file.plain_file_parser(
            "concept_validation_noconcepts.plain",
            [get_test_data_path("data/plainfileparser")],
        )


def test_concept_validation_usage(get_test_data_path):
    plain_file.plain_file_parser(
        "concept_validation_valid.plain",
        [get_test_data_path("data/plainfileparser")],
    )

    with pytest.raises(PlainSyntaxError):
        plain_file.plain_file_parser(
            "concept_validation_nondefined.plain",
            [get_test_data_path("data/plainfileparser")],
        )

    with pytest.raises(PlainSyntaxError):
        plain_file.plain_file_parser(
            "concept_validation_defined_nondefined.plain",
            [get_test_data_path("data/plainfileparser")],
        )

    with pytest.raises(PlainSyntaxError):
        plain_file.plain_file_parser(
            "concept_validation_defined_nondefined_2.plain",
            [get_test_data_path("data/plainfileparser")],
        )


def test_concept_validation_redefinition(get_test_data_path):
    with pytest.raises(PlainSyntaxError):
        plain_file.plain_file_parser(
            "concept_validation_redefinition.plain",
            [get_test_data_path("data/plainfileparser")],
        )


def test_concept_validation_non_concept_usage(get_test_data_path):
    plain_file.plain_file_parser(
        "concept_validation_nonconcept.plain",
        [get_test_data_path("data/plainfileparser")],
    )


def test_concept_validation_several_concepts(get_test_data_path):
    plain_file.plain_file_parser(
        "concept_validation_several_concepts.plain",
        [get_test_data_path("data/plainfileparser")],
    )


def test_concept_validation_acceptance_tests(get_test_data_path):
    plain_file.plain_file_parser(
        "concept_validation_acceptance_tests.plain",
        [get_test_data_path("data/plainfileparser")],
    )

    with pytest.raises(PlainSyntaxError):
        plain_file.plain_file_parser(
            "concept_validation_acceptance_tests_nondefined.plain",
            [get_test_data_path("data/plainfileparser")],
        )


def test_required_concepts(get_test_data_path):
    plain_file.plain_file_parser(
        "required_concepts_example.plain",
        [get_test_data_path("data/plainfileparser")],
    )

    plain_file.plain_file_parser(
        "required_concepts_module.plain",
        [get_test_data_path("data/plainfileparser")],
    )

    plain_file.plain_file_parser(
        "required_concepts_partial.plain",
        [get_test_data_path("data/plainfileparser")],
    )

    with pytest.raises(PlainSyntaxError):
        plain_file.plain_file_parser(
            "required_concepts_missing.plain",
            [get_test_data_path("data/plainfileparser")],
        )

    with pytest.raises(PlainSyntaxError):
        plain_file.plain_file_parser(
            "required_concepts_partial_duplicate.plain",
            [get_test_data_path("data/plainfileparser")],
        )


def test_exported_concepts(get_test_data_path):
    plain_file.plain_file_parser(
        "exported_concepts_example.plain",
        [get_test_data_path("data/plainfileparser")],
    )

    plain_file.plain_file_parser(
        "exported_concepts_nested_example.plain",
        [get_test_data_path("data/plainfileparser")],
    )

    with pytest.raises(PlainSyntaxError):
        plain_file.plain_file_parser(
            "exported_concepts_missing_example.plain",
            [get_test_data_path("data/plainfileparser")],
        )

    with pytest.raises(PlainSyntaxError):
        plain_file.plain_file_parser(
            "exported_concepts_transitive_example.plain",
            [get_test_data_path("data/plainfileparser")],
        )


def test_topological_sort(get_test_data_path):
    _, plain_source, _ = plain_file.plain_file_parser(
        "topological_sort.plain", [get_test_data_path("data/plainfileparser")]
    )
    assert plain_spec.DEFINITIONS in plain_source
    assert plain_source[plain_spec.DEFINITIONS] == [
        {"markdown": "- :Concept1: is a concept."},
        {"markdown": "- :Concept2: is a concept that depends on the :Concept1: concept."},
        {"markdown": "- :Concept3: is a concept that depends on both the :Concept1: and :Concept2: concepts."},
    ]

    _, plain_source, _ = plain_file.plain_file_parser(
        "topological_sort_not_referenced.plain",
        [get_test_data_path("data/plainfileparser")],
    )
    assert plain_spec.DEFINITIONS in plain_source
    assert plain_source[plain_spec.DEFINITIONS] == [
        {"markdown": "- :Concept1: is a concept."},
        {"markdown": "- :NonReferencedConcept: is a concept."},
        {"markdown": "- :Concept7: is a concept."},
        {"markdown": "- :Concept2: is a concept that depends on the :Concept1: concept."},
        {"markdown": "- :Concept8: is a concept that depends on the :Concept1: concept."},
        {"markdown": "- :Concept5: is a concept that depends on the :Concept1: concept."},
        {"markdown": "- :Concept3: is a concept that depends on both the :Concept1: and :Concept2: concepts."},
        {"markdown": "- :Concept9: is a concept that depends on the :Concept8:."},
        {"markdown": "- :Concept4: is a concept that depends on the :Concept3: concept."},
        {"markdown": "- :Concept6: is a concept that depends on the :Concept1: and :Concept4: concepts."},
    ]
