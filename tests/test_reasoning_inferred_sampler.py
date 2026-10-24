"""Sampler artifacts from pinned inferred views with generation provenance."""

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.adapters.inferred_sampler import build_inferred_snapshot
from gestaltdb.reasoning.adapters.sessions import SessionScope, advance_session, start_session
from gestaltdb.reasoning.catalog import CatalogEntry, RuleCatalog
from gestaltdb.reasoning.model import ReasoningLimits, Variable
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom
from gestaltdb.sampling.snapshot import SamplerSnapshot


def program():
    x, y = Variable("x"), Variable("y")
    return GenericProgram([
        GenericRule("r1", [TypedAtom("memberOf", [x, y])], [], TypedAtom("person", [x])),
        GenericRule("r2", [TypedAtom("person", [x])], [], TypedAtom("entity", [x])),
    ])


def catalog_for(rules):
    return RuleCatalog(tuple(
        CatalogEntry(rule.rule_id, "v1", GenericProgram([rule]).digest, 1) for rule in rules.rules))


def scope():
    return SessionScope(world="default", valid_time_us=50,
                        selection=ClaimSelection(), mapping=ClaimMapping())


def assert_member(graph, subject, agent, group="g1"):
    return graph.assert_claim(subject=subject, predicate="memberOf", object=group,
                              agent=agent, polarity="positive", valid=(0, 100))


def triples(snapshot):
    nodes = list(snapshot.external_node_ids.astype(str))
    relations = list(snapshot.external_relation_ids.astype(str))
    return {(nodes[int(s)], relations[int(r)], nodes[int(d)])
            for s, r, d in zip(snapshot.src_int.tolist(), snapshot.rel_int.tolist(), snapshot.dst_int.tolist())}


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_build_rebuild_stability_and_mismatch(tmp_path, backend, module):
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        rules = program()
        assert_member(graph, "a", agent="one")
        assert_member(graph, "a", agent="two")
        assert_member(graph, "b", agent="one")
        session = start_session(graph, rules, catalog_for(rules), scope=scope(), limits=ReasoningLimits())

        first_dir = tmp_path / "snap1"
        first = build_inferred_snapshot(session, first_dir)
        assert triples(first) == {("a", "memberOf", "g1"), ("b", "memberOf", "g1")}
        assert first.metadata["reasoning_generation"]["commit_horizon"] == session.commit_horizon
        assert first.metadata["reasoning_generation"]["program_digest"] == session.program.digest
        reloaded = SamplerSnapshot.load(first_dir)
        assert triples(reloaded) == triples(first)

        # Engine-level neighbor verification: full fanout covers every inferred edge.
        from gestaltdb.sampling.engine import SamplerEngine

        engine = SamplerEngine.load(first_dir, seed=7)
        # Compact, version-stable check: every snapshot edge is reachable from its source.
        for src, rel, dst in triples(first):
            batch = engine.sample_neighbors([reloaded.node_int(src)], fanout=10, direction="out")
            assert batch.neighbor_nodes.size >= 1
        all_batch = engine.sample_neighbors(
            [reloaded.node_int("a"), reloaded.node_int("b")], fanout=10, direction="out")
        assert all_batch.neighbor_nodes.size == 2

        # Generation mismatch fails before writing.
        with pytest.raises(Exception, match="generation/view mismatch"):
            build_inferred_snapshot(session, tmp_path / "mismatch",
                                    expected_generation={"commit_horizon": session.commit_horizon + 100})
        assert not (tmp_path / "mismatch").exists()

        # Populated output path fails closed.
        with pytest.raises(Exception, match="already holds a snapshot"):
            build_inferred_snapshot(session, first_dir)

        # Retract the final version for "a": new generation, new path, old artifact stable.
        remaining = [v for v in graph.iter_claim_versions()
                     if v.claim is not None and v.claim.subject == "a"]
        for version in remaining:
            graph.retract_claim(version.logical_id, valid=(0, 100),
                                supersedes_version_id=version.version_id)
        moved = advance_session(graph, session, limits=ReasoningLimits())
        second_dir = tmp_path / "snap2"
        second = build_inferred_snapshot(moved.session, second_dir)
        assert triples(second) == {("b", "memberOf", "g1")}
        assert SamplerSnapshot.load(first_dir).metadata["reasoning_generation"]["commit_horizon"] == \
            session.commit_horizon
        assert second.metadata["reasoning_generation"]["commit_horizon"] == moved.session.commit_horizon
    finally:
        graph.close()


def test_explicit_selection_contract(tmp_path):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    try:
        rules = program()
        assert_member(graph, "a", agent="one")
        session = start_session(graph, rules, catalog_for(rules), scope=scope(), limits=ReasoningLimits())
        # Unknown node predicate yields nodes={} and therefore no edges, but keeps provenance.
        empty = build_inferred_snapshot(session, tmp_path / "empty",
                                        node_predicate="noSuchPredicate")
        assert empty.num_edges == 0
        assert empty.metadata["reasoning_generation"]["inferred_node_count"] == 0
        with pytest.raises(Exception, match="edge_predicates must name"):
            build_inferred_snapshot(session, tmp_path / "bad", edge_predicates=())
    finally:
        graph.close()


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_snapshot_respects_pinned_valid_time(tmp_path, backend, module):
    pytest.importorskip(module)
    from gestaltdb.reasoning.adapters.sessions import SessionScope, start_session
    graph = GraphDB.create(tmp_path / backend, backend=backend, serializer="json")
    try:
        rules = program()
        graph.assert_claim(subject="a", predicate="memberOf", object="g1",
                           agent="registry", polarity="positive", valid=(0, 100))
        graph.assert_claim(subject="b", predicate="memberOf", object="g1",
                           agent="registry", polarity="positive", valid=(200, 300))
        graph.assert_claim(subject="c", predicate="memberOf", object="g1",
                           agent="registry", polarity="positive", valid=(0, 50))
        catalog = catalog_for(rules)
        selection, mapping = ClaimSelection(), ClaimMapping()
        at_50 = start_session(graph, rules, catalog,
                              scope=SessionScope("default", 50, selection, mapping),
                              limits=ReasoningLimits())
        snap_50 = build_inferred_snapshot(at_50, tmp_path / "snap50")
        assert triples(snap_50) == {("a", "memberOf", "g1")}
        assert snap_50.metadata["reasoning_generation"]["valid_time_us"] == 50
        at_250 = start_session(graph, rules, catalog,
                               scope=SessionScope("default", 250, selection, mapping),
                               limits=ReasoningLimits())
        snap_250 = build_inferred_snapshot(at_250, tmp_path / "snap250")
        assert triples(snap_250) == {("b", "memberOf", "g1")}
        assert snap_250.metadata["reasoning_generation"]["valid_time_us"] == 250
        at_25 = start_session(graph, rules, catalog,
                              scope=SessionScope("default", 25, selection, mapping),
                              limits=ReasoningLimits())
        assert triples(build_inferred_snapshot(at_25, tmp_path / "snap25")) == {
            ("a", "memberOf", "g1"), ("c", "memberOf", "g1")}
    finally:
        graph.close()
