"""RE-05 contracts, capability probing and batched claim parity."""

import pytest

from gestaltdb.reasoning.adapters.batched import ExtractionProfile, PinnedBatchedClaimSource
from gestaltdb.reasoning.adapters.claims import (
    ClaimMapping,
    ClaimSelection,
    ClaimSourceLimits,
    PinnedClaimSource,
)
from gestaltdb.reasoning.extraction import (
    ExtractionCapability,
    ExtractionRequest,
    FactBatch,
    batch_evidence,
)
from gestaltdb.reasoning.model import Fact, ReasoningError, ReasoningLimitError, ReasoningLimits
from gestaltdb.reasoning.protocols import Evidence


def test_request_and_batch_contracts_reject_invalid_inputs():
    with pytest.raises(ReasoningError):
        ExtractionRequest(world="")
    with pytest.raises(ReasoningError):
        ExtractionRequest(world="w", predicates="p")
    with pytest.raises(ReasoningError):
        ExtractionRequest(world="w", batch_rows=0)
    with pytest.raises(ReasoningError):
        ExtractionRequest(world="w", max_rows=True)
    with pytest.raises(ReasoningError):
        ExtractionRequest(world="w", sources="s")
    request = ExtractionRequest(world="w", predicates=["b", "a"], sources=["s", None],
                                batch_rows=2, max_rows=3)
    assert request.predicates == ("a", "b")
    assert request.sources == (None, "s")
    with pytest.raises(ReasoningError):
        FactBatch((), 0, True)
    with pytest.raises(ReasoningError):
        FactBatch(["not-evidence"], 0, False)
    with pytest.raises(ReasoningError):
        FactBatch([Evidence(Fact("a", "p", "b"), "v1")], -1, False)
    with pytest.raises(ReasoningError):
        list(batch_evidence([], "not-a-request"))
    with pytest.raises(ReasoningError):
        list(batch_evidence(["not-evidence"], ExtractionRequest(world="w")))


def test_batch_evidence_chunks_bounds_and_propagates_source_failures():
    def source(count):
        for index in range(count):
            yield Evidence(Fact(f"n{index}", "p", "o"), f"v{index}")

    request = ExtractionRequest(world="w", batch_rows=2, max_rows=10)
    batches = list(batch_evidence(source(5), request))
    assert [len(b.evidence) for b in batches] == [2, 2, 1]
    assert [b.batch_index for b in batches] == [0, 1, 2]
    assert [b.complete for b in batches] == [False, False, True]
    assert batches[0].evidence[0].reference_id == "v0"
    assert list(batch_evidence(source(0), request)) == []
    with pytest.raises(ReasoningLimitError, match="max_rows"):
        list(batch_evidence(source(5), ExtractionRequest(world="w", batch_rows=2, max_rows=4)))

    def broken():
        yield Evidence(Fact("a", "p", "b"), "v1")
        raise RuntimeError("upstream failed")

    with pytest.raises(RuntimeError, match="upstream failed"):
        list(batch_evidence(broken(), request))


def test_capability_probe_is_storage_free_and_fails_closed_on_unknown():
    capability = ExtractionCapability.probe(object())
    assert capability.backend == "object" and not capability.pinned_snapshot
    assert not capability.native_arrow_scan and not capability.multiget
    assert capability.snapshot_kind == "unknown-none"
    with pytest.raises(ReasoningError, match="no pinned read snapshot"):
        capability.require_pinned_snapshot()
    assert ExtractionCapability.probe(dict).backend == "dict"
    assert ExtractionCapability.probe(dict).snapshot_kind == "unknown-none"


def test_native_hook_prefers_runtime_report_over_class_name():
    class FakePyRexStore:
        supports_read_snapshots = False
        supports_verifiable_read_snapshots = False

        def has_native_arrow_scan(self):
            return True

        def iter_index_prefix(self, *args):
            raise AssertionError("probe must not touch storage")

        def get_nodes_bulk(self, *args):
            raise AssertionError("probe must not touch storage")

        def get_edges_bulk(self, *args):
            raise AssertionError("probe must not touch storage")

    capability = ExtractionCapability.probe(FakePyRexStore())
    assert capability.snapshot_kind == "pyrex-native-snapshot"
    assert capability.pinned_snapshot and capability.native_arrow_scan
    capability.require_pinned_snapshot()

    class BrokenHook:
        def has_native_arrow_scan(self):
            raise RuntimeError("runtime exploded")

    assert not ExtractionCapability.probe(BrokenHook()).native_arrow_scan
    with pytest.raises(ReasoningError):
        ExtractionCapability(backend="", snapshot_kind="x", pinned_snapshot=True,
                             verifiable_snapshot=False, predicate_index=False, multiget=False)


def test_core_runs_without_site_packages_or_storage_imports():
    from pathlib import Path
    import subprocess
    import sys

    path = Path(__file__).parents[1] / "src/gestaltdb/reasoning/__init__.py"
    script = f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location('standalone', {str(path)!r})
module = importlib.util.module_from_spec(spec)
sys.modules['standalone'] = module
spec.loader.exec_module(module)
from standalone.extraction import ExtractionCapability, ExtractionRequest, batch_evidence
from standalone.model import Fact
from standalone.protocols import Evidence, InputContext
request = ExtractionRequest(world='w', predicates=['p'], batch_rows=2, max_rows=10)
batches = list(batch_evidence([Evidence(Fact('a','p','b'),'v1')], request))
assert len(batches) == 1 and batches[0].complete
assert not ExtractionCapability.probe(object()).pinned_snapshot
assert not any(name.startswith(('gestaltdb','numpy','pyarrow','polars')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)


def _seed(graph):
    graph.assert_claim(subject="alice", predicate="knows", object="bob",
                       agent="registry", polarity="positive", valid=(0, 100))
    graph.assert_claim(subject="bob", predicate="knows", object="carol",
                       agent="registry", polarity="positive", valid=(0, 100))
    graph.assert_claim(subject="alice", predicate="likes", object="carol",
                       agent="other", polarity="positive", valid=(0, 100))


def _batched(graph, view, request, **kwargs):
    return PinnedBatchedClaimSource(view, request=request, ontology_revision="rev-1",
                                    selection=kwargs.get("selection", ClaimSelection()),
                                    mapping=kwargs.get("mapping", ClaimMapping()))


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel")])
def test_batched_matches_single_pass_evidence_and_reports_profile(tmp_path, backend, module):
    pytest.importorskip(module)
    from gestaltdb.graphdb import GraphDB

    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        _seed(graph)
        with graph.read_view(valid_time=50) as view:
            expected = PinnedClaimSource(view, world="default", ontology_revision="rev-1",
                                         selection=ClaimSelection(), mapping=ClaimMapping())
            with expected:
                single = list(expected.iter_evidence())
            request = ExtractionRequest(world="default", batch_rows=2, max_rows=100)
            with _batched(graph, view, request) as source:
                assert source.capability.backend.lower().startswith(backend)
                assert source.capability.predicate_index and source.capability.multiget
                assert not source.capability.native_arrow_scan
                if backend == "lmdb":
                    source.require_pinned_snapshot()
                else:
                    with pytest.raises(ReasoningError, match="no pinned read snapshot"):
                        source.require_pinned_snapshot()
                batches = list(source.iter_batches())
                assert sum(len(b.evidence) for b in batches) == len(single) == 3
                assert [b.batch_index for b in batches] == [0, 1]
                assert [b.complete for b in batches] == [False, True]
                flattened = [e for b in batches for e in b.evidence]
                assert sorted(flattened, key=lambda e: e.fact) == sorted(single, key=lambda e: e.fact)
                repeat = [e for b in source.iter_batches() for e in b.evidence]
                assert repeat == flattened
                profile = source.profile
                assert isinstance(profile, ExtractionProfile)
                assert (profile.batches, profile.rows, profile.scanned) == (2, 3, 3)
                assert profile.pushdown == "none" and profile.verifications >= 3
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel")])
def test_predicate_and_binding_pushdown_narrows_scans(tmp_path, backend, module):
    pytest.importorskip(module)
    from gestaltdb.graphdb import GraphDB

    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        _seed(graph)
        with graph.read_view(valid_time=50) as view:
            request = ExtractionRequest(world="default", predicates=["knows"],
                                        subject="alice", batch_rows=100, max_rows=100)
            with _batched(graph, view, request) as source:
                batches = list(source.iter_batches())
                assert sum(len(b.evidence) for b in batches) == 1
                assert source.profile.pushdown == "predicate+subject"
                evidence = batches[0].evidence[0]
                assert (evidence.fact.subject, evidence.fact.predicate) == ("alice", "knows")
            single = ExtractionRequest(world="default", predicates=["knows"], batch_rows=100, max_rows=100)
            with _batched(graph, view, single) as source:
                assert sum(len(b.evidence) for b in source.iter_batches()) == 2
                assert source.profile.pushdown == "predicate"
            empty = ExtractionRequest(world="default", predicates=[], batch_rows=10, max_rows=10)
            with _batched(graph, view, empty) as source:
                assert list(source.iter_batches()) == []
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_multi_predicate_pushdown_matches_full_scan_and_narrows_scans(tmp_path, backend, module):
    pytest.importorskip(module)
    from gestaltdb.graphdb import GraphDB

    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        graph.assert_claim(subject="a", predicate="edge", object="b",
                           agent="registry", polarity="positive", valid=(0, 100))
        graph.assert_claim(subject="b", predicate="edge", object="c",
                           agent="registry", polarity="positive", valid=(0, 100))
        graph.assert_claim(subject="a", predicate="reach", object="seeded",
                           agent="registry", polarity="positive", valid=(0, 100))
        graph.assert_claim(subject="n", predicate="noise", object="unused",
                           agent="registry", polarity="positive", valid=(0, 100))
        with graph.read_view(valid_time=50) as view:
            full_request = ExtractionRequest(world="default", batch_rows=100, max_rows=100)
            with _batched(graph, view, full_request) as source:
                full = [e for b in source.iter_batches() for e in b.evidence]
                full_profile = source.profile
            assert full_profile.pushdown == "none"
            selective_request = ExtractionRequest(world="default", predicates=["edge", "reach"],
                                                  batch_rows=100, max_rows=100)
            with _batched(graph, view, selective_request) as source:
                batches = list(source.iter_batches())
                selective = [e for b in batches for e in b.evidence]
                selective_profile = source.profile
            assert selective_profile.pushdown == "predicates"
            wanted = {e.reference_id for e in full if e.fact.predicate in ("edge", "reach")}
            assert {e.reference_id for e in selective} == wanted
            assert len(selective) == 3
            assert selective_profile.scanned == 3 < full_profile.scanned == 4
            # Repeat passes are deterministic.
            with _batched(graph, view, selective_request) as source:
                repeat = [e for b in source.iter_batches() for e in b.evidence]
            assert repeat == selective
            # Subject binding composes with the multi-predicate union.
            bound_request = ExtractionRequest(world="default", predicates=["edge", "reach"],
                                              subject="a", batch_rows=100, max_rows=100)
            with _batched(graph, view, bound_request) as source:
                bound = [e for b in source.iter_batches() for e in b.evidence]
                assert source.profile.pushdown == "predicates+subject"
            assert {(e.fact.subject, e.fact.predicate) for e in bound} == {("a", "edge"), ("a", "reach")}
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_selective_extraction_preserves_demand_closure(tmp_path, backend, module):
    pytest.importorskip(module)
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.reasoning.demand import BoundGoal, answer, required_predicates
    from gestaltdb.reasoning.typed import GenericEngine, GenericProgram, GenericRule, TypedAtom, TypedFact
    from gestaltdb.reasoning.model import Variable

    x, y, z = Variable("x"), Variable("y"), Variable("z")
    program = GenericProgram([
        GenericRule("base", [TypedAtom("edge", [x, y])], [], TypedAtom("reach", [x, y])),
        GenericRule("step", [TypedAtom("reach", [x, y]), TypedAtom("edge", [y, z])], [],
                    TypedAtom("reach", [x, z])),
        GenericRule("unrelated", [TypedAtom("noise", [x])], [], TypedAtom("ignored", [x])),
    ])
    goal = BoundGoal("reach", ["a", y])
    plan = required_predicates(program, goal)
    assert plan == ("edge", "reach")
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        graph.assert_claim(subject="a", predicate="edge", object="b",
                           agent="registry", polarity="positive", valid=(0, 100))
        graph.assert_claim(subject="b", predicate="edge", object="c",
                           agent="registry", polarity="positive", valid=(0, 100))
        graph.assert_claim(subject="a", predicate="reach", object="asserted",
                           agent="registry", polarity="positive", valid=(0, 100))
        graph.assert_claim(subject="n", predicate="noise", object="unused",
                           agent="registry", polarity="positive", valid=(0, 100))
        with graph.read_view(valid_time=50) as view:
            full_request = ExtractionRequest(world="default", batch_rows=100, max_rows=100)
            with _batched(graph, view, full_request) as source:
                full_facts = [TypedFact(e.fact.predicate, (e.fact.subject, e.fact.object))
                              for b in source.iter_batches() for e in b.evidence]
            selective_request = ExtractionRequest(world="default", predicates=list(plan),
                                                  batch_rows=100, max_rows=100)
            with _batched(graph, view, selective_request) as source:
                selective_facts = [TypedFact(e.fact.predicate, (e.fact.subject, e.fact.object))
                                   for b in source.iter_batches() for e in b.evidence]
                assert source.profile.pushdown == "predicates"
        expected = answer(program, full_facts, goal, limits=ReasoningLimits()).facts
        assert answer(program, selective_facts, goal, limits=ReasoningLimits()).facts == expected
        # Recursive frontier beyond the seed and asserted headed facts survive.
        assert TypedFact("reach", ["a", "c"]) in expected
        assert TypedFact("reach", ["a", "asserted"]) in expected
        assert not any(f.predicate in ("noise", "ignored") for f in expected)
        assert GenericEngine().evaluate(program, selective_facts, limits=ReasoningLimits()) == \
            GenericEngine().evaluate(program, [f for f in full_facts if f.predicate in plan],
                                     limits=ReasoningLimits())
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel")])
def test_batched_limits_rebinding_and_evaluation_parity(tmp_path, backend, module):
    pytest.importorskip(module)
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.ontology import Domain, Ontology, SubClassOf, compile_ontology

    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        _seed(graph)
        with graph.read_view(valid_time=50) as view:
            request = ExtractionRequest(world="default", batch_rows=1, max_rows=2)
            with _batched(graph, view, request) as source:
                with pytest.raises(ReasoningLimitError, match="max_rows"):
                    list(source.iter_batches())
            with pytest.raises(ReasoningError, match="no completed"):
                source.profile
            request = ExtractionRequest(world="default", batch_rows=2, max_rows=100)
            source = _batched(graph, view, request)
            source.close()
            with pytest.raises(ReasoningError, match="closed"):
                list(source.iter_batches())
            plan = compile_ontology(Ontology("o", [Domain("knows", "Person")]))
            request = ExtractionRequest(world="default", batch_rows=2, max_rows=100)
            with PinnedBatchedClaimSource(view, request=request, ontology_revision=plan.revision_id,
                                          selection=ClaimSelection(), mapping=ClaimMapping()) as source:
                scoped = source.evaluate(plan, limits=ReasoningLimits())
                assert scoped.context.ontology_revision == plan.revision_id
                assert any(f.predicate == "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
                           for f in scoped.facts)
    finally:
        graph.close()


def test_pyrex_backend_native_scan_or_missing_extra_fails_closed(tmp_path):
    import importlib.util

    from gestaltdb.graphdb import GraphDB

    if importlib.util.find_spec("pyrex") is None:
        with pytest.raises(ImportError, match=r"gestaltdb\[rocksdb\]"):
            GraphDB.create(tmp_path / "pyrex", backend="pyrex", serializer="json")
    else:
        from gestaltdb.graphdb import Node
        from gestaltdb.kvstores import PyRexStore
        from gestaltdb.reasoning.adapters.native import NativeKeyScan
        from gestaltdb.serializers import JSONSerializer

        graph = GraphDB(PyRexStore(path=str(tmp_path / "pyrex")), JSONSerializer())
        try:
            capability = ExtractionCapability.probe(graph.store)
            assert capability.native_arrow_scan
            assert capability.snapshot_kind == "pyrex-native-snapshot"
            assert capability.pinned_snapshot and capability.verifiable_snapshot
            capability.require_pinned_snapshot()
            graph.put_node(Node("n1", labels=["P"]))
            rows = [row for batch in NativeKeyScan(graph.store) for row in batch.rows]
            assert rows and all(type(k) is bytes and type(v) is bytes for k, v in rows)
        finally:
            graph.close()
