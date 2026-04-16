import re
from copy import deepcopy
from typing import Optional

import mistletoe
import mistletoe.block_token
import networkx as nx

import plain_spec
from plain2code_exceptions import PlainSyntaxError

# These are reserved concept names that are pre-defined and must not be redefined in the definitions section.
DEFAULT_CONCEPTS = {
    ":ConformanceTests:",
    ":UnitTests:",
    ":AcceptanceTests:",
    ":Implementation:",
}


def extract_concepts_from_definition(text: str) -> tuple[list[str], list[str]]:
    pattern = r"-\s(:[^\:]+:)(?:,\s*:[^\:]+:)*"
    match = re.search(pattern, text)

    if not match:
        return list(), [
            f"Syntax error: Invalid definition specification text: {text}. Should start with `- :<concept>:` (where <concept> is any string that does not contain `:`)."
        ]

    concept_candidates = re.findall(r":[^\:]+:", match.group())
    valid_concepts = list[str]()
    concept_re = r":[+\-\.0-9A-Z_a-z]+:"
    errors = list[str]()
    for candidate in concept_candidates:
        if re.match(concept_re, candidate):
            valid_concepts.append(candidate)
        else:
            errors.append(
                f"Syntax error: Invalid concept: {candidate}. Should contain only letters, numbers, hyphens and dots."
            )

    return valid_concepts, errors


def extract_concepts_from_spec_text(text: str) -> str:
    return re.findall(r":[+\-\.0-9A-Z_a-z]+:", text)


def collect_concepts(plain_source) -> tuple[set[str], list[str]]:
    if plain_spec.DEFINITIONS not in plain_source:
        return set(), list()

    concepts = list[str]()
    errors = list[str]()
    for definition in plain_source[plain_spec.DEFINITIONS]:
        spec_text = definition["markdown"]
        new_concepts, new_errors = extract_concepts_from_definition(spec_text)
        errors.extend(new_errors)
        concepts.extend(new_concepts)

    return concepts, errors


def validate_concepts_in_spec(spec: dict, concepts: set[str], spec_group: str) -> list[str]:
    spec_text = spec["markdown"]
    used_concepts = extract_concepts_from_spec_text(spec_text)
    errors = []
    for used_concept in used_concepts:
        if used_concept not in concepts and used_concept not in DEFAULT_CONCEPTS:
            errors.append(
                f"Syntax error: Concept {used_concept} is not defined in the definitions. {spec_group}: {spec_text}"
            )

    return errors


def validate_concepts_in_spec_group(spec_group, plain_source, concepts: set[str]) -> list[str]:
    if spec_group not in plain_source:
        return []

    errors = []
    for spec in plain_source[spec_group]:
        errors.extend(validate_concepts_in_spec(spec, concepts, spec_group))

    return errors


def validate_concepts(marshalled_plain_source) -> list[str]:
    errors = list[str]()
    concepts = deepcopy(DEFAULT_CONCEPTS)
    tree_based_concepts = deepcopy(DEFAULT_CONCEPTS)

    new_concepts, new_errors = collect_concepts(marshalled_plain_source)
    errors.extend(new_errors)

    unique_new_concepts = set(new_concepts)
    intersection = unique_new_concepts.intersection(concepts)

    if len(unique_new_concepts) < len(new_concepts) or (len(intersection) > 0):
        misused_concepts = {concept for concept in new_concepts if new_concepts.count(concept) > 1}
        misused_concepts.update(intersection)
        errors.append(f"Syntax error: Concepts were defined multiple times: {', '.join(misused_concepts)} .")

    concepts.update(unique_new_concepts)
    tmp_tree_based_concepts = tree_based_concepts.union(unique_new_concepts)

    for spec_group in [
        plain_spec.NON_FUNCTIONAL_REQUIREMENTS,
        plain_spec.TEST_REQUIREMENTS,
        plain_spec.FUNCTIONAL_REQUIREMENTS,
        plain_spec.DEFINITIONS,
    ]:
        errors.extend(validate_concepts_in_spec_group(spec_group, marshalled_plain_source, tmp_tree_based_concepts))

    if plain_spec.FUNCTIONAL_REQUIREMENTS in marshalled_plain_source:
        for func_spec in marshalled_plain_source[plain_spec.FUNCTIONAL_REQUIREMENTS]:
            if "acceptance_tests" not in func_spec:
                continue

            acceptance_tests = func_spec["acceptance_tests"]
            for spec in acceptance_tests:
                errors.extend(validate_concepts_in_spec(spec, tmp_tree_based_concepts, plain_spec.ACCEPTANCE_TESTS))

    return errors


def find_concept_definitions_in_plain_source(
    concept: str,
    plain_source: dict,
    renderer,
) -> list[mistletoe.block_token.token]:
    definitions = list[mistletoe.block_token.token]()
    if plain_source[plain_spec.DEFINITIONS] is None:
        return definitions

    for definition in plain_source[plain_spec.DEFINITIONS].children:
        rendered_definition = renderer.render(definition)
        defined_concepts, _ = extract_concepts_from_definition(rendered_definition)
        if concept in defined_concepts:
            definitions.append(definition)
            used_concepts = [
                used_concept
                for used_concept in extract_concepts_from_spec_text(rendered_definition)
                if used_concept != concept and used_concept not in DEFAULT_CONCEPTS
            ]
            for used_concept in used_concepts:
                definitions.extend(find_concept_definitions_in_plain_source(used_concept, plain_source, renderer))

    return definitions


def build_adjacency_list(definitions: Optional[list[dict]]) -> tuple[dict, dict]:
    adjacency_list = dict[str, list[str]]()
    concept_definitions = dict[str, dict]()
    if definitions is None or len(definitions) == 0:
        return adjacency_list, concept_definitions

    for definition in definitions:
        def_text = definition["markdown"]
        new_concepts, _ = extract_concepts_from_definition(def_text)
        used_concepts = [
            used_concept
            for used_concept in extract_concepts_from_spec_text(def_text)
            if used_concept not in new_concepts and used_concept not in DEFAULT_CONCEPTS
        ]
        used_concepts = set(used_concepts)

        for new_concept in new_concepts:
            if new_concept not in adjacency_list:
                adjacency_list[new_concept] = []

        for used_concept in used_concepts:
            if used_concept not in adjacency_list:
                adjacency_list[used_concept] = []

            adjacency_list[used_concept].extend(new_concepts)

        for new_concept in new_concepts:
            concept_definitions[new_concept] = definition

    return adjacency_list, concept_definitions


def sort_definitions(definitions: list[dict]) -> list[dict]:
    if len(definitions) <= 1:
        return

    adjacency_list, concept_definitions = build_adjacency_list(definitions)

    concept_graph = nx.DiGraph()
    for node, neighbors in adjacency_list.items():
        for neighbour in neighbors:
            concept_graph.add_edge(node, neighbour)
        if not neighbors:
            concept_graph.add_node(node)

    if not nx.is_directed_acyclic_graph(concept_graph):
        msg = "Found cycles in the concept graph. Cycles are not allowed."
        all_cycles = list(nx.simple_cycles(concept_graph))

        for cycle in all_cycles:
            cyclic_definitions = []
            cyclic_definitions.append(concept_definitions[cycle[0]]["markdown"])
            cyclic_definitions.append(concept_definitions[cycle[1]]["markdown"])
            msg += "Cyclic definitons:\n"
            msg += "\n".join(cyclic_definitions)
            msg += "\n"

        raise PlainSyntaxError(f"Plain syntax error: {msg}")

    order = list(nx.topological_sort(concept_graph))
    if len(order) > 0:
        definition_to_order_idx = {
            plain_spec.hash_text(str(concept_definitions[concept])): idx for idx, concept in enumerate(order)
        }
        definitions.sort(key=lambda definition: definition_to_order_idx[plain_spec.hash_text(str(definition))])
