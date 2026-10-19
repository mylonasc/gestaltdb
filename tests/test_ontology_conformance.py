"""Selected named-entity consequences, not full OWL conformance certification.

The grounding oracle enumerates finite substitutions independently of the kernel.
The optional OWL-RL oracle independently implements ontology semantics. Project
only supported individual/class/property consequences: OWL vocabulary axioms,
sameAs reflexivity and other consequences outside gestalt-core-v1 are excluded.
"""

import ast
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import random
import subprocess
import sys

import pytest

from gestaltdb.ontology.compiler import compile_ontology
from gestaltdb.ontology.model import (
    Domain, EquivalentClasses, EquivalentProperties, InverseOf, Ontology,
    Range, SubClassOf, SubPropertyOf, Symmetric, Transitive, TYPE_PREDICATE,
)
from gestaltdb.reasoning.engine import DatalogEngine
from gestaltdb.reasoning.model import Fact, ReasoningLimits
from tests.test_reasoning_engine import _reference


def _iri(name):
    return "urn:gestaltdb:conformance:" + name


def _fact(subject, predicate, object):
    return Fact(_iri(subject), _iri(predicate), _iri(object))


def _type(subject, class_id):
    return Fact(_iri(subject), TYPE_PREDICATE, _iri(class_id))


@dataclass(frozen=True)
class _Case:
    name: str
    axioms: tuple
    facts: tuple[Fact, ...]


def _cases():
    a, b, c = [_iri("c" + str(i)) for i in range(3)]
    p, q, r = [_iri("p" + str(i)) for i in range(3)]
    cases = [
        _Case("subclass-cycle", (SubClassOf(a, b), SubClassOf(b, c), SubClassOf(c, a)), (_type("n0", "c0"),)),
        _Case("class-equivalence", (EquivalentClasses(a, b), SubClassOf(b, c)), (_type("n0", "c0"), _type("n1", "c1"))),
        _Case("subproperty-cycle", (SubPropertyOf(p, q), SubPropertyOf(q, p), EquivalentProperties(q, r)), (_fact("n0", "p0", "n1"),)),
        _Case("domain-range", (Domain(p, a), Domain(p, b), Range(p, c)), (_fact("n0", "p0", "n1"),)),
        _Case("inverse-symmetric", (InverseOf(p, q), Symmetric(q)), (_fact("n0", "p0", "n1"),)),
        _Case("transitive-cycle", (Transitive(p),), (_fact("n0", "p0", "n1"), _fact("n1", "p0", "n0"))),
        _Case("composition", (SubPropertyOf(p, q), Transitive(q), InverseOf(q, r), Domain(r, a), SubClassOf(a, b)),
              (_fact("n0", "p0", "n1"), _fact("n1", "p0", "n2"))),
        _Case("empty", (), ()),
    ]
    rng = random.Random(20261020)
    classes, properties = [a, b, c], [p, q, r]
    for number in range(12):
        axioms = []
        for _ in range(8):
            left, right = rng.choices(classes, k=2)
            first, second = rng.choices(properties, k=2)
            choices = [SubClassOf(left, right), EquivalentClasses(left, right), SubPropertyOf(first, second),
                       EquivalentProperties(first, second), Domain(first, left), Range(first, left),
                       InverseOf(first, second), Symmetric(first), Transitive(first)]
            axioms.append(rng.choice(choices))
        facts = [_fact(rng.choice(["n0", "n1", "n2", "n3"]), rng.choice(["p0", "p1", "p2"]),
                       rng.choice(["n0", "n1", "n2", "n3"])) for _ in range(8)]
        facts.extend(_type("n" + str(i), "c" + str(i % 3)) for i in range(3))
        cases.append(_Case(f"generated-{number}", tuple(axioms), tuple(facts)))
    return cases


_CASES = _cases()


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.name)
def test_profile_closure_and_witnesses_agree_with_independent_grounding(case):
    compiled = compile_ontology(Ontology(case.name, case.axioms))
    engine = DatalogEngine()
    result = engine.evaluate(compiled.program, case.facts, limits=ReasoningLimits(), collect_proofs=True)
    assert set(result.facts) == _reference(compiled.program, case.facts)
    assert not result.proofs.truncated
    assert result == engine.evaluate(compiled.program, reversed(case.facts), limits=ReasoningLimits(), collect_proofs=True)
    origins = {origin.rule_id for origin in compiled.origins}
    assert all(node.rule is None or node.rule.rule_id in origins for node in result.proofs.nodes)
    additional = engine.evaluate(compiled.program, [*case.facts, _fact("extra", "unreferenced", "other")],
                                 limits=ReasoningLimits())
    assert set(result.facts) <= set(additional.facts)


def _rdf_fixture(case, rdflib):
    graph = rdflib.Graph()
    uri, rdf, rdfs, owl = rdflib.URIRef, rdflib.RDF, rdflib.RDFS, rdflib.OWL
    individuals, classes, properties = set(), set(), set()
    for fact in case.facts:
        graph.add((uri(fact.subject), uri(fact.predicate), uri(fact.object)))
        individuals.add(uri(fact.subject))
        if fact.predicate == TYPE_PREDICATE:
            classes.add(uri(fact.object))
        else:
            individuals.add(uri(fact.object))
            properties.add(uri(fact.predicate))
    for axiom in case.axioms:
        if isinstance(axiom, SubClassOf):
            left, right = uri(axiom.subclass), uri(axiom.superclass)
            classes.update((left, right))
            graph.add((left, rdfs.subClassOf, right))
        elif isinstance(axiom, EquivalentClasses):
            left, right = uri(axiom.left), uri(axiom.right)
            classes.update((left, right))
            graph.add((left, owl.equivalentClass, right))
        elif isinstance(axiom, SubPropertyOf):
            left, right = uri(axiom.subproperty), uri(axiom.superproperty)
            properties.update((left, right))
            graph.add((left, rdfs.subPropertyOf, right))
        elif isinstance(axiom, EquivalentProperties):
            left, right = uri(axiom.left), uri(axiom.right)
            properties.update((left, right))
            graph.add((left, owl.equivalentProperty, right))
        elif isinstance(axiom, (Domain, Range)):
            prop, class_id = uri(axiom.property), uri(axiom.class_id)
            properties.add(prop)
            classes.add(class_id)
            graph.add((prop, rdfs.domain if isinstance(axiom, Domain) else rdfs.range, class_id))
        elif isinstance(axiom, InverseOf):
            left, right = uri(axiom.left), uri(axiom.right)
            properties.update((left, right))
            graph.add((left, owl.inverseOf, right))
        elif isinstance(axiom, (Symmetric, Transitive)):
            prop = uri(axiom.property)
            properties.add(prop)
            graph.add((prop, rdf.type, owl.SymmetricProperty if isinstance(axiom, Symmetric) else owl.TransitiveProperty))
        else:
            raise AssertionError("oracle fixture lacks a supported axiom encoding")
    for class_id in classes:
        graph.add((class_id, rdf.type, owl.Class))
    for prop in properties:
        graph.add((prop, rdf.type, owl.ObjectProperty))
    return graph, individuals, classes, properties


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.name)
def test_selected_consequences_match_optional_owlrl_oracle(case):
    rdflib = pytest.importorskip("rdflib", reason="optional ontology conformance oracle")
    owlrl = pytest.importorskip("owlrl", reason="optional ontology conformance oracle")
    graph, individuals, classes, properties = _rdf_fixture(case, rdflib)
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics, axiomatic_triples=False, datatype_axioms=False).expand(graph)
    expected = {
        Fact(str(subject), str(predicate), str(object)) for subject, predicate, object in graph
        if subject in individuals and (
            (predicate == rdflib.RDF.type and object in classes)
            or (predicate in properties and object in individuals)
        )
    }
    compiled = compile_ontology(Ontology(case.name, case.axioms))
    result = DatalogEngine().evaluate(compiled.program, case.facts, limits=ReasoningLimits())
    assert set(result.facts) == expected


def test_core_and_ontology_import_graph_stays_inward_without_dynamic_loading():
    root = Path(__file__).parents[1] / "src"
    paths = [*root.joinpath("gestaltdb", "reasoning").rglob("*.py"),
             *root.joinpath("gestaltdb", "ontology").rglob("*.py")]
    for path in paths:
        if "adapters" in path.parts:
            continue
        relative = path.relative_to(root)
        package = ".".join(relative.parent.parts)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] in sys.stdlib_module_names for alias in node.names), path
                assert all(alias.name.split(".")[0] != "importlib" for alias in node.names), path
            elif isinstance(node, ast.ImportFrom):
                if not node.level:
                    assert node.module.split(".")[0] in sys.stdlib_module_names, path
                    assert node.module.split(".")[0] != "importlib", path
                    continue
                resolved = importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                targets = [resolved] if node.module else [resolved + "." + alias.name for alias in node.names]
                for target in targets:
                    core = target.startswith("gestaltdb.reasoning.") and not target.startswith("gestaltdb.reasoning.adapters")
                    ontology = "ontology" in relative.parts and target.startswith("gestaltdb.ontology.")
                    assert core or ontology, (path, target)
            elif isinstance(node, ast.Call):
                assert not (isinstance(node.func, ast.Name) and node.func.id in {"__import__", "import_module"}), path
                assert not (isinstance(node.func, ast.Attribute) and node.func.attr in {"__import__", "import_module"}), path


def test_whole_pipeline_runs_without_site_packages_storage_or_package_initializer():
    root = Path(__file__).parents[1] / "src" / "gestaltdb"
    script = f"""
import sys, types
package = types.ModuleType('isolated')
package.__path__ = [{str(root)!r}]
sys.modules['isolated'] = package
from isolated.ontology.model import Domain, Ontology, SubClassOf, TYPE_PREDICATE
from isolated.ontology.compiler import compile_ontology
from isolated.reasoning.model import Fact, ReasoningLimits
from isolated.reasoning.protocols import Evidence
from isolated.reasoning.engine import DatalogEngine
compiled = compile_ontology(Ontology('o', [Domain('p', 'A'), SubClassOf('A', 'B')]))
result = DatalogEngine().evaluate(compiled.program, [Evidence(Fact('a', 'p', 'b'), 'reference')],
                                 limits=ReasoningLimits(), collect_proofs=True)
explanation = result.explain(Fact('a', TYPE_PREDICATE, 'B'))
assert not explanation.truncated and len(explanation.nodes) == 3
assert not any(name.startswith(('gestaltdb', 'numpy', 'lark')) for name in sys.modules)
assert not any('.adapters' in name for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)


def test_recomputation_does_not_reuse_unsupported_cyclic_conclusions():
    compiled = compile_ontology(Ontology("cycle", [EquivalentClasses(_iri("A"), _iri("B"))]))
    engine = DatalogEngine()
    first = engine.evaluate(compiled.program, [_type("n", "A")], limits=ReasoningLimits(), collect_proofs=True)
    assert _type("n", "B") in first
    second = engine.evaluate(compiled.program, [], limits=ReasoningLimits(), collect_proofs=True)
    assert second.facts == () and second.proofs.nodes == ()
