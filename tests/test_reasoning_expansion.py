"""RE-13 explicit relation expansion, fanout accounting and seed handoff."""

import uuid

import pytest

from gestaltdb.ontology import Ontology, SubPropertyOf, Transitive, compile_ontology
from gestaltdb.reasoning.adapters.graph import (
    ExpandedNeighborhood,
    GraphMapping,
    GraphReasoningResult,
    RelationContribution,
    expand_edge_types,
    inferred_seeds,
    sample_expanded_neighbors,
)
from gestaltdb.reasoning.model import Fact, ReasoningError, ReasoningLimits
from gestaltdb.reasoning.protocols import InputContext
from gestaltdb.readview import CurrentReadProvenance


def compiled():
    return compile_ontology(Ontology("hierarchy", [
        SubPropertyOf("narrow", "wide"),
        SubPropertyOf("narrower", "narrow"),
        Transitive("ancestor"),
        SubPropertyOf("parent", "ancestor"),
    ]))


def mapping():
    return GraphMapping(edge_types=[("narrowEdge", "narrow"), ("narrowerEdge", "narrower"),
                                    ("wideEdge", "wide"), ("parentEdge", "parent"),
                                    ("ancestorEdge", "ancestor")])


def test_expand_edge_types_follows_schema_closure():
    plan = compiled()
    assert expand_edge_types("wide", plan, mapping()) == ("narrowEdge", "narrowerEdge", "wideEdge")
    assert expand_edge_types("narrow", plan, mapping()) == ("narrowEdge", "narrowerEdge")
    assert expand_edge_types("ancestor", plan, mapping()) == ("ancestorEdge", "parentEdge")
    with pytest.raises(ReasoningError, match="unknown predicate"):
        expand_edge_types("missing", plan, mapping())
    with pytest.raises(ReasoningError):
        expand_edge_types("", plan, mapping())
    with pytest.raises(ReasoningError):
        expand_edge_types("wide", plan, "not-a-mapping")


def _closure():
    from gestaltdb.reasoning import DatalogEngine

    plan = compiled()
    facts = [Fact("a", "narrow", "b"), Fact("b", "narrow", "c"), Fact("c", "wide", "d"),
             Fact("x", "parent", "y"), Fact("y", "parent", "z")]
    return DatalogEngine().evaluate(plan.program, facts, limits=ReasoningLimits()), plan


def _scoped(result):
    return GraphReasoningResult(InputContext("c", "w", "o", "p", "m"),
                                CurrentReadProvenance(database_id=str(uuid.uuid4()), snapshot_sequence=1,
                                                      backend_name="lmdb", backend_layout_version=1,
                                                      serializer_name="json", serializer_format_version=1,
                                                      backend_snapshot_kind="read-transaction",
                                                      state_sha256="0" * 64),
                                result)


def test_samples_are_distinct_with_per_relation_accounting():
    result, plan = _closure()
    scoped = _scoped(result)
    expanded = sample_expanded_neighbors(scoped, "a", predicate="wide", direction="out",
                                         fanout=3, seed=7, mapping=mapping(), compiled=plan)
    assert isinstance(expanded, ExpandedNeighborhood)
    assert len(expanded.endpoints) <= 3 and len(expanded.endpoints) == len(set(expanded.endpoints))
    assert sum(len(item.sampled) for item in expanded.contributions) == len(expanded.endpoints)
    assert [item.relation for item in expanded.contributions] == ["narrowEdge", "narrowerEdge", "wideEdge"]
    assert all(isinstance(item, RelationContribution) for item in expanded.contributions)
    assert sample_expanded_neighbors(scoped, "a", predicate="wide", direction="out",
                                     fanout=3, seed=7, mapping=mapping(), compiled=plan) == expanded
    total = sum(item.candidates for item in expanded.contributions)
    assert total == 2
    assert set(expanded.endpoints) <= {fact.object for fact in result.match(predicate="wide", subject="a")}
    with pytest.raises(ReasoningError):
        sample_expanded_neighbors(result, "a", predicate="wide", direction="out",
                                  fanout=1, seed=0, mapping=mapping(), compiled=plan)
    with pytest.raises(ReasoningError, match="unknown predicate"):
        sample_expanded_neighbors(scoped, "a", predicate="nope", direction="out",
                                  fanout=1, seed=0, mapping=mapping(), compiled=plan)


def test_expanded_sampling_is_subset_of_full_expansion_with_accounting():
    result, plan = _closure()
    scoped = _scoped(result)
    wide = sample_expanded_neighbors(scoped, "a", predicate="wide", direction="out",
                                     fanout=100, seed=1, mapping=mapping(), compiled=plan)
    assert set(wide.endpoints) == {"b"}
    assert sum(item.candidates for item in wide.contributions) == 2
    assert inferred_seeds(wide.endpoints) == ("b",)
    ancestor = sample_expanded_neighbors(scoped, "x", predicate="ancestor", direction="out",
                                         fanout=100, seed=1, mapping=mapping(), compiled=plan)
    assert set(ancestor.endpoints) == {"y", "z"}
    assert inferred_seeds(iter(["b", "b", "c"])) == ("b", "c")
    with pytest.raises(ReasoningError):
        inferred_seeds([None])
    with pytest.raises(ReasoningError):
        inferred_seeds(42)
    with pytest.raises(ReasoningError):
        inferred_seeds("single-string")
