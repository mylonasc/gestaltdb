import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from gestaltdb.ontology.compiler import CompiledOntology, RuleOrigin, compile_ontology
from gestaltdb.ontology.model import (
    Domain, EquivalentClasses, EquivalentProperties, InverseOf, Ontology,
    OntologyError, Range, SubClassOf, SubPropertyOf, Symmetric, Transitive,
    TYPE_PREDICATE,
)
from gestaltdb.reasoning.engine import DatalogEngine
from gestaltdb.reasoning.model import Fact, ReasoningLimits


def _types(entity, *classes):
    return {Fact(entity, TYPE_PREDICATE, class_id) for class_id in classes}


def _closure(axioms, facts):
    compiled = compile_ontology(Ontology("test", axioms))
    return set(DatalogEngine().evaluate(compiled.program, facts, limits=ReasoningLimits()).facts)


def test_subclass_multiple_inheritance_and_cycles_preserve_ids():
    axioms = [SubClassOf("Researcher", "Person"), SubClassOf("Researcher", "Employee"),
              SubClassOf("Employee", "Agent"), SubClassOf("Person", "Researcher")]
    assert _closure(axioms, _types("alice", "Researcher")) == _types(
        "alice", "Researcher", "Person", "Employee", "Agent")


def test_subproperty_chain_domain_range_and_multiple_domains():
    axioms = [SubPropertyOf("worksFor", "affiliatedWith"), SubPropertyOf("affiliatedWith", "relatedTo"),
              Domain("relatedTo", "Person"), Domain("relatedTo", "Agent"), Range("relatedTo", "Organization")]
    result = _closure(axioms, [Fact("alice", "worksFor", "acme")])
    assert result == {
        Fact("alice", "worksFor", "acme"), Fact("alice", "affiliatedWith", "acme"),
        Fact("alice", "relatedTo", "acme"), *_types("alice", "Person", "Agent"),
        *_types("acme", "Organization"),
    }


@pytest.mark.parametrize("axiom", [EquivalentClasses("A", "B"), EquivalentProperties("A", "B")])
def test_named_equivalence_works_in_both_directions_without_merging_entities(axiom):
    if isinstance(axiom, EquivalentClasses):
        inputs = _types("a", "A") | _types("b", "B")
        expected = _types("a", "A", "B") | _types("b", "A", "B")
    else:
        inputs = {Fact("a", "A", "x"), Fact("b", "B", "y")}
        expected = inputs | {Fact("a", "B", "x"), Fact("b", "A", "y")}
    assert _closure([axiom], inputs) == expected


def test_inverse_symmetric_transitive_composition():
    axioms = [InverseOf("parent", "child"), SubPropertyOf("parent", "ancestor"),
              Transitive("ancestor"), Symmetric("friend")]
    result = _closure(axioms, [Fact("a", "parent", "b"), Fact("c", "child", "b"), Fact("a", "friend", "c")])
    assert result == {
        Fact("a", "parent", "b"), Fact("b", "parent", "c"), Fact("b", "child", "a"), Fact("c", "child", "b"),
        Fact("a", "ancestor", "b"), Fact("b", "ancestor", "c"), Fact("a", "ancestor", "c"),
        Fact("a", "friend", "c"), Fact("c", "friend", "a"),
    }


def test_empty_and_reflexive_axioms_do_not_invent_facts():
    assert _closure([], []) == set()
    assert _closure([SubClassOf("A", "A"), EquivalentProperties("p", "p"), Transitive("p")], []) == set()
    inputs = {Fact("a", "p", "b")}
    assert _closure([EquivalentProperties("p", "p")], inputs) == inputs


def test_revision_identity_is_canonical_but_not_logical_equivalence():
    a, b = SubClassOf("A", "B"), Domain("p", "A")
    first = Ontology("o", [b, a, a])
    second = Ontology("o", [a, b])
    assert first == second
    assert first.revision_id == second.revision_id
    assert first.revision_id != Ontology("other", [a, b]).revision_id
    assert first.revision_id != Ontology("o", [a]).revision_id
    assert a.axiom_id == Ontology("other", [a]).axioms[0].axiom_id
    assert compile_ontology(first) == compile_ontology(second)


@pytest.mark.parametrize("factory", [EquivalentClasses, EquivalentProperties, InverseOf])
def test_symmetric_axioms_have_order_independent_identity(factory):
    assert factory("A", "B") == factory("B", "A")
    assert factory("A", "B").axiom_id == factory("B", "A").axiom_id


def test_axiom_and_ontology_collections_are_immutable():
    axiom = SubClassOf("?A", "?B")
    axioms = [axiom]
    ontology = Ontology("?ontology", axioms)
    axioms.clear()
    assert ontology.axioms == (axiom,)
    with pytest.raises(FrozenInstanceError):
        axiom.subclass = "other"
    assert hash(ontology)


@pytest.mark.parametrize("bad", ["", None, 12, True, "\ud800"])
def test_axiom_identifiers_require_nonempty_utf8_text(bad):
    for factory in (lambda: SubClassOf(bad, "B"), lambda: Transitive(bad), lambda: Ontology(bad)):
        with pytest.raises(OntologyError):
            factory()


@pytest.mark.parametrize("factory", [
    lambda: SubPropertyOf(TYPE_PREDICATE, "p"), lambda: SubPropertyOf("p", TYPE_PREDICATE),
    lambda: EquivalentProperties(TYPE_PREDICATE, "p"), lambda: InverseOf("p", TYPE_PREDICATE),
    lambda: Domain(TYPE_PREDICATE, "C"), lambda: Range(TYPE_PREDICATE, "C"),
    lambda: Symmetric(TYPE_PREDICATE), lambda: Transitive(TYPE_PREDICATE),
])
def test_membership_predicate_is_reserved_only_in_property_positions(factory):
    with pytest.raises(OntologyError, match="reserved"):
        factory()
    assert SubClassOf(TYPE_PREDICATE, "Class")


def test_unsupported_shapes_profiles_and_subclasses_fail_closed():
    class Unimplemented(SubClassOf):
        pass

    for build in (lambda: Ontology("o", ["axiom"]), lambda: Ontology("o", [{"sameAs": "b"}]),
                  lambda: Ontology("o", [Unimplemented("A", "B")]), lambda: Ontology("o", profile="owl-rl"),
                  lambda: Ontology("o", iter([])), lambda: compile_ontology("o")):
        with pytest.raises(OntologyError):
            build()


def test_every_generated_rule_has_exact_axiom_origin_and_safe_head():
    ontology = Ontology("o", [SubClassOf("A", "B"), EquivalentClasses("B", "C"), Domain("p", "A"),
                              InverseOf("p", "q"), Symmetric("q"), Transitive("p")])
    compiled = compile_ontology(ontology)
    assert compiled.revision_id == ontology.revision_id
    assert tuple(origin.rule_id for origin in compiled.origins) == tuple(rule.rule_id for rule in compiled.program.rules)
    assert {origin.axiom_id for origin in compiled.origins} == {axiom.axiom_id for axiom in ontology.axioms}
    assert len(compiled.program.rules) == 8
    assert compile_ontology(ontology).program.digest == compiled.program.digest
    with pytest.raises(OntologyError, match="exactly once"):
        CompiledOntology(compiled.ontology, compiled.program, compiled.origins[:-1])
    with pytest.raises(OntologyError, match="source ontology"):
        CompiledOntology(compiled.ontology, compiled.program,
                         [RuleOrigin(origin.rule_id, "unknown") for origin in compiled.origins])
    with pytest.raises(OntologyError):
        RuleOrigin("", "axiom")


def test_ontology_implementation_imports_only_stdlib_and_inward_values():
    root = Path(__file__).parents[1] / "src" / "gestaltdb" / "ontology"
    allowed = {"__future__", "dataclasses", "hashlib", "json", "typing"}
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] in allowed for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert (node.level == 0 and node.module.split(".")[0] in allowed) or (
                    node.level == 1 and node.module == "model"
                ) or (node.level == 2 and node.module == "reasoning.model"), (path, ast.dump(node))


def test_ontology_compiles_and_runs_without_site_packages_or_graph_imports():
    root = Path(__file__).parents[1] / "src" / "gestaltdb"
    script = f"""
import importlib.util, sys, types
package = types.ModuleType('standalone')
package.__path__ = [{str(root)!r}]
sys.modules['standalone'] = package
from standalone.ontology.model import Ontology, SubClassOf, TYPE_PREDICATE
from standalone.ontology.compiler import compile_ontology
from standalone.reasoning.engine import DatalogEngine
from standalone.reasoning.model import Fact, ReasoningLimits
compiled = compile_ontology(Ontology('o', [SubClassOf('A', 'B')]))
result = DatalogEngine().evaluate(compiled.program, [Fact('a', TYPE_PREDICATE, 'A')], limits=ReasoningLimits())
assert Fact('a', TYPE_PREDICATE, 'B') in result.facts
assert compiled.ontology.axioms[0].axiom_id == compiled.origins[0].axiom_id
assert not any(name.startswith('gestaltdb') for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)


def test_reasoning_and_ontology_contract_examples_execute():
    document = Path(__file__).parents[1] / "docs" / "ontology-contract.rst"
    blocks = document.read_text().split(".. code-block:: python\n")[1:]
    assert len(blocks) == 2
    for block in blocks:
        code = []
        for line in block.splitlines():
            if line and not line.startswith("   "):
                break
            code.append(line)
        exec(compile(textwrap.dedent("\n".join(code)), str(document), "exec"), {})
