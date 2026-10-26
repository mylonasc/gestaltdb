"""Whole-range admission cannot be replaced by matching one point token."""

from dataclasses import replace
from pathlib import Path
import re

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.adapters.range_generation import RangeSourceLimits, evaluate_pinned_range, admit_range_result
from gestaltdb.reasoning.adapters.session_generation import catalog_for_typed_program
from gestaltdb.reasoning.catalog import RuleCatalog
from gestaltdb.reasoning.generation import TemporalSourceIdentity, CurrentSourceIdentity
from gestaltdb.reasoning.intervals import Interval, IntervalSet
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError, ReasoningCancelledError, ReasoningLimits, Variable
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact

X, Y, Z = Variable("x"), Variable("y"), Variable("z")
PROGRAM = GenericProgram([
    GenericRule("base", [TypedAtom("edge", [X, Y])], [], TypedAtom("reach", [X, Y])),
    GenericRule("step", [TypedAtom("reach", [X, Y]), TypedAtom("edge", [Y, Z])], [], TypedAtom("reach", [X, Z])),
])
MAPPING = ClaimMapping((("p", "edge"), ("q", "edge")))


@pytest.fixture(params=[("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def source(tmp_path, request):
    backend, module = request.param
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / "db", backend=backend, serializer="json")
    def assertion(s, p, o, agent, valid):
        return graph.epistemic.assert_claim(subject=s, predicate=p, object=o, agent=agent, source="registry",
                                  polarity="positive", valid=valid)
    first = assertion("a", "p", "b", "one", (0, 100))
    second = assertion("a", "p", "b", "two", (10, 90))
    bc = assertion("b", "q", "c", "one", (20, 80))
    graph.epistemic.retract_claim(first.logical_id, valid=(40, 60))
    corrected = graph.epistemic.correct_claim(second.logical_id, supersedes_version_id=second.version_id, valid=(30, 70))
    graph.epistemic.retract_claim(bc.logical_id, valid=(50, 55))
    tail = assertion("a", "p", "b", "three", (110, 120))
    yield graph, (first, second, bc, corrected, tail)
    graph.close()


def run(view, **kwargs):
    return evaluate_pinned_range(view, PROGRAM, kwargs.pop("catalog", catalog_for_typed_program(PROGRAM)),
        valid_start_us=kwargs.pop("valid_start_us", 0), valid_end_us=kwargs.pop("valid_end_us", 130),
        world="default", selection=kwargs.pop("selection", ClaimSelection()), mapping=MAPPING,
        limits=ReasoningLimits(), **kwargs)


def admit(view, result, **kwargs):
    return admit_range_result(view, result, PROGRAM, catalog_for_typed_program(PROGRAM),
        expected_generation=kwargs.pop("expected_generation", result.binding.identity), limits=ReasoningLimits(), **kwargs)


def oracle(point):
    # Independent fixture semantics: one of the two a->b assertions survives
    # every point in [0,100); b->c has a retraction hole. No engine or adapter.
    edges = set()
    if 0 <= point < 100 or 110 <= point < 120:
        edges.add(("a", "b"))
    if 20 <= point < 50 or 55 <= point < 80:
        edges.add(("b", "c"))
    reach = set(edges)
    while True:
        added = {(a, c) for a, b in reach for u, c in edges if b == u}
        if added <= reach:
            break
        reach.update(added)
    return {TypedFact("edge", pair) for pair in edges} | {TypedFact("reach", pair) for pair in reach}


def test_independent_point_oracle_every_boundary_and_gap(source):
    graph, versions = source
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        result = run(view)
        for point in range(130):
            assert set(result.closure.at(point)) == oracle(point)
        assert result.closure.answers(TypedFact("reach", ("a", "c"))) == IntervalSet([Interval(20, 50), Interval(55, 80)])
        evidence = {e.version_id: e for e in result.evidence}
        assert evidence[versions[0].version_id].valid == IntervalSet([Interval(0, 40), Interval(60, 100)])
        assert evidence[versions[3].version_id].valid == IntervalSet([Interval(30, 70)])
        assert len(evidence) == 5
        assert admit(view, result).points_checked == len(result.closure.segments)
        result.binding.verify_source(graph)


def test_independent_versions_cannot_be_dropped_with_same_logical_facts(source):
    graph, _ = source
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        result = run(view)
        changed = replace(result, evidence=result.evidence[1:])
        with pytest.raises(ProvenanceMismatchError, match="complete authenticated source"):
            admit(view, changed)
        e = result.evidence[0]
        changed = replace(result, evidence=(replace(e, valid=IntervalSet([Interval(0, 130)])), *result.evidence[1:]))
        with pytest.raises(ProvenanceMismatchError, match="evidence"):
            admit(view, changed)


def test_closure_and_derivation_tampering_rejected(source):
    graph, _ = source
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        result = run(view)
        assert result.closure.derivations
        for closure in (replace(result.closure, derivations=()),
                        replace(result.closure, base=result.closure.base[1:]),
                        replace(result.closure, segments=result.closure.segments[1:]),
                        replace(result.closure, program_digest="other")):
            with pytest.raises(ReasoningError, match="fresh evaluation"):
                admit(view, replace(result, closure=closure))
        derivation = result.closure.derivations[0]
        # Same fact sets are not enough: a forged support must also fail.
        bad = replace(derivation, rule_id="nonexistent")
        with pytest.raises(ReasoningError, match="fresh evaluation"):
            admit(view, replace(result, closure=replace(result.closure,
                derivations=(bad, *result.closure.derivations[1:]))))


def test_historical_prefix_and_latest_source_movement(source):
    graph, _ = source
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        result = run(view)
        graph.epistemic.assert_claim(subject="c", predicate="q", object="d", agent="new", polarity="positive", valid=(0, 130))
        assert run(view).closure == result.closure
        admit(view, result)
        result.binding.verify_source(graph, freshness="historical")
        with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
            result.binding.verify_source(graph)
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0, through_commit=result.binding.provenance.commit_horizon) as historical:
        admit(historical, result)
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as latest:
        with pytest.raises(ReasoningError, match="source drifted"):
            admit(latest, result)


def test_empty_historical_prefix_is_not_latest_or_missing_history(source):
    graph, _ = source
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0, through_commit=0) as view:
        result = run(view)
        assert result.evidence == () and result.history_versions == result.scanned_claims == 0
        assert result.extraction_segments == (Interval(0, 130),)
        assert admit(view, result).points_checked == 0
        result.binding.verify_source(graph, freshness="historical")
        with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
            result.binding.verify_source(graph)


@pytest.mark.parametrize("dimension", ["start", "end", "horizon", "token", "program_digest", "catalog_digest",
                                        "semantics_profile", "dictionary_id", "world", "mapping_id", "point", "current"])
def test_expected_identity_rejected_before_source_iteration(source, monkeypatch, dimension):
    graph, _ = source
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        identity = run(view).binding.identity
        if dimension in ("start", "end", "horizon", "token"):
            key, value = {"start": ("valid_start_us", -1), "end": ("valid_end_us", 131),
                          "horizon": ("commit_horizon", 99), "token": ("provenance_token", "other")}[dimension]
            wrong = replace(identity, source=replace(identity.source, **{key: value}))
        elif dimension in ("world", "mapping_id"):
            wrong = replace(identity, context=replace(identity.context, **{dimension: "other"}))
        elif dimension == "point":
            wrong = replace(identity, source=TemporalSourceIdentity("db", 1, "token", 0))
        elif dimension == "current":
            wrong = replace(identity, source=CurrentSourceIdentity("db", 1, "token"))
        else:
            wrong = replace(identity, **{dimension: "other"})
        assert wrong.digest != identity.digest
        def forbidden(*args, **kwargs):
            pytest.fail("source extracted before exact pin check")
        monkeypatch.setattr(view, "iter_claim_versions", forbidden)
        with pytest.raises(ReasoningError, match="drifted"):
            run(view, expected_generation=wrong)


@pytest.mark.parametrize("field", ["max_history_versions", "max_scanned_claims", "max_segments", "max_evidence", "max_commits"])
def test_cumulative_source_bounds(source, field):
    graph, _ = source
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        with pytest.raises(ReasoningLimitError, match=field):
            run(view, source_limits=replace(RangeSourceLimits(), **{field: 1}))


def test_policy_empty_inputs_catalog_and_invalid_ranges(source):
    graph, _ = source
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        result = run(view, selection=ClaimSelection(agents=()))
        assert result.evidence == () and result.closure.base == () and result.closure.derivations == ()
        assert result.extraction_segments[0].start == 0 and result.extraction_segments[-1].end == 130
        admit(view, result)
        with pytest.raises(ReasoningError, match="catalog content"):
            run(view, catalog=RuleCatalog([]))
        with pytest.raises(ReasoningError, match="start < end"):
            run(view, valid_end_us=0)
        with pytest.raises(ReasoningError, match="anchored"):
            run(view, valid_start_us=1)


def test_cancellation_and_view_rebinding(source, monkeypatch):
    graph, _ = source
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        reads = []
        original = view.iter_claim_versions
        def counted():
            for v in original():
                reads.append(v)
                yield v
        monkeypatch.setattr(view, "iter_claim_versions", counted)
        with pytest.raises(ReasoningCancelledError):
            run(view, cancel=lambda: len(reads) >= 2)
        assert len(reads) == 2
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        original = view.iter_claims_as_of
        def rebound(**kwargs):
            for v in original(**kwargs):
                view.provenance = replace(view.provenance, valid_time_us=1)
                yield v
        monkeypatch.setattr(view, "iter_claims_as_of", rebound)
        with pytest.raises(ProvenanceMismatchError, match="rebound"):
            run(view)


def test_final_conformance_cannot_bypass_source_checks(source, monkeypatch):
    import gestaltdb.reasoning.adapters.range_generation as adapter
    graph, _ = source
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        result = run(view)
        original = adapter.verify_closure
        def rebound(*args, **kwargs):
            verification = original(*args, **kwargs)
            view.provenance = replace(view.provenance, valid_time_us=1)
            return verification
        monkeypatch.setattr(adapter, "verify_closure", rebound)
        with pytest.raises(ProvenanceMismatchError, match="rebound"):
            admit(view, result)


def test_catalog_version_and_policy_are_bound_independently(source):
    graph, _ = source
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        first = run(view)
        catalog = RuleCatalog([replace(e, version_id="changed", ordinal=2)
                               for e in catalog_for_typed_program(PROGRAM).entries])
        changed = run(view, catalog=catalog)
        assert first.closure == changed.closure
        assert first.binding.identity.catalog_digest != changed.binding.identity.catalog_digest
        with pytest.raises(ReasoningError, match="catalog_digest"):
            run(view, catalog=catalog, expected_generation=first.binding.identity)
        selected = run(view, selection=ClaimSelection(agents=("three",), sources=("registry",)))
        assert len(selected.evidence) == 1
        assert selected.closure.answers(TypedFact("edge", ("a", "b"))) == IntervalSet([Interval(110, 120)])
        with pytest.raises(ProvenanceMismatchError, match="binding mismatch"):
            replace(first.binding, selection=ClaimSelection(agents=()))


def test_full_validity_disjoint_policy_and_nonentity_inputs(source):
    graph, _ = source
    graph.epistemic.assert_claim(subject="ignored", predicate="p", object="literal", object_kind="literal",
                       agent="one", polarity="positive", valid=(1, 2))
    graph.epistemic.assert_claim(subject="ignored", predicate="p", object="negative", agent="one",
                       polarity="negative", valid=(3, 4))
    graph.epistemic.assert_claim(subject="ignored", predicate="p", object="other-world", agent="one",
                       world="elsewhere", polarity="positive", valid=(5, 6))
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        result = run(view)
        assert all(e.fact.values[0] != "ignored" for e in result.evidence)
        for point in range(130):
            assert set(result.closure.at(point)) == oracle(point)


def test_asserted_rule_heads_remain_complete_inputs(source):
    graph, _ = source
    graph.epistemic.assert_claim(subject="a", predicate="reach", object="explicit", agent="one",
                       polarity="positive", valid=(1, 9))
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        result = run(view)
        fact = TypedFact("reach", ("a", "explicit"))
        assert result.closure.answers(fact) == IntervalSet([Interval(1, 9)])
        assert any(e.fact == fact for e in result.evidence)


def test_retained_result_bounds_and_cancel_precede_readmission(source):
    graph, _ = source
    with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=0) as view:
        result = run(view)
        with pytest.raises(ReasoningLimitError, match="max_evidence"):
            admit(view, result, source_limits=RangeSourceLimits(max_evidence=1))
        with pytest.raises(ReasoningCancelledError):
            admit(view, result, cancel=lambda: True)


@pytest.mark.parametrize("start,end", [(True, 10), (0, False), (0, None), (10, 0), (0, 0)])
def test_finite_range_identity_validation(start, end):
    from gestaltdb.reasoning.generation import TemporalRangeSourceIdentity
    with pytest.raises(ReasoningError):
        TemporalRangeSourceIdentity("db", 1, "token", start, end)


def test_range_admission_documentation_example():
    pytest.importorskip("lmdb")
    text = (Path(__file__).resolve().parents[1] / "docs/reasoning-range-admission.rst").read_text()
    blocks = re.findall(r"\.\. code-block:: python\n\n((?:   .*\n|\n)+)", text)
    assert len(blocks) == 1
    exec("\n".join(line[3:] if line.startswith("   ") else line for line in blocks[0].splitlines()), {})
