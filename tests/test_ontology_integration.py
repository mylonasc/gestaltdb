"""Optional adapters compose explicitly without mixing current and claim stores."""
import pytest

from gestaltdb.graphdb import GraphDB, Node, Edge
from gestaltdb.ontology.adapters.rdf import RDFDocument, import_rdf
from gestaltdb.reasoning import Fact, ReasoningLimits
from gestaltdb.reasoning.adapters.claims import ClaimSelection, ClaimMapping
from gestaltdb.reasoning.adapters.graph import GraphMapping, PinnedGraphSource
from gestaltdb.reasoning.adapters.publication import reconcile_point


def test_import_project_query_sample_publish_and_reconcile(tmp_path):
    pytest.importorskip("rdflib")
    pytest.importorskip("lmdb")
    plan = import_rdf(RDFDocument(
        '<urn:p> <http://www.w3.org/2000/01/rdf-schema#subPropertyOf> <urn:q> .',
        "nt",
    ), ontology_id="urn:integration").compile()
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    try:
        graph.put_nodes([Node("a"), Node("b")])
        graph.put_edge(Edge("ab", "a", "b", {"type": "P"}))
        base = graph.epistemic.assert_claim(subject="a", predicate="urn:p", object="b", agent="input",
                                  polarity="positive", valid=(0, 100))
        with graph.current_read_view(require_verifiable=True) as view:
            with PinnedGraphSource(view, mapping=GraphMapping(edge_types=[("P", "urn:p")])) as source:
                current = source.evaluate(plan, limits=ReasoningLimits())
        assert current.match(predicate="urn:q") == (Fact("a", "urn:q", "b"),)
        assert current.sample_neighbors("a", predicate="urn:q", direction="out", fanout=1, seed=0) == ("b",)
        params = dict(valid_time=50, source_world="default", target_world="published",
                      publication_key="integration", selection=ClaimSelection(), mapping=ClaimMapping(),
                      limits=ReasoningLimits())
        receipt = reconcile_point(graph, plan, **params)
        assert receipt.asserted == 2
        assert reconcile_point(graph, plan, **params).commit_id is None
        assert not graph.query("MATCH ()-[r:related]->() RETURN r").records
        graph.epistemic.retract_claim(base.logical_id, supersedes_version_id=base.version_id)
        assert reconcile_point(graph, plan, **params).retracted == 2
        assert not list(graph.epistemic.iter_claims_as_of(world="published", valid_time=50))
        # Claims reconciliation leaves mutable graph and prior result untouched.
        assert graph.get_edge(graph.edge_key_to_bytes("ab")) is not None
        assert current.match(predicate="urn:q")
        graph.epistemic.assert_claim(subject="a", predicate="urn:p", object="b", agent="input",
                           polarity="positive", valid=(0, 100))
        assert reconcile_point(graph, plan, **params).asserted == 2
        assert reconcile_point(graph, plan, **params).commit_id is None
    finally:
        graph.close()
