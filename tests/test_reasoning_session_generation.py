"""Independent exact-source admission for typed sessions, not closure proofs."""

from dataclasses import replace

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.adapters.session_generation import (
    SessionAdmissionLimits, bind_session_generation, catalog_for_typed_program,
)
from gestaltdb.reasoning.adapters.session_store import load_session, save_session
from gestaltdb.reasoning.adapters.sessions import SessionScope, advance_session, start_session
from gestaltdb.reasoning.catalog import RuleCatalog
from gestaltdb.reasoning.evidence_ledger import AssertionVersion, EvidenceLedger
from gestaltdb.reasoning.model import ReasoningCancelledError, ReasoningError, ReasoningLimitError, ReasoningLimits, Variable
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom, TypedFact


@pytest.fixture(params=[("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def bound_source(tmp_path, request):
    backend, module = request.param
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / "db", backend=backend, serializer="json")
    try:
        versions = [graph.epistemic.assert_claim(subject="a", predicate="p", object="b", agent=agent,
                                        source="registry", polarity="positive", valid=(0, 100))
                    for agent in ("one", "two")]
        x, y = Variable("x"), Variable("y")
        program = GenericProgram([
            GenericRule("r", [TypedAtom("p", [x, y])], [], TypedAtom("person", [x])),
            GenericRule("r2", [TypedAtom("person", [x])], [], TypedAtom("entity", [x])),
        ])
        scope = SessionScope("default", 50, ClaimSelection(), ClaimMapping())
        session = start_session(graph, program, catalog_for_typed_program(program),
                                scope=scope, limits=ReasoningLimits())
        yield graph, session, versions
    finally:
        graph.close()


def test_authenticates_alternative_assertions_and_persisted_state(bound_source, tmp_path):
    graph, session, versions = bound_source
    binding = bind_session_generation(graph, session)
    assert len(session.ledger.versions) == 2
    assert len(session.ledger.facts) == 1
    assert binding.identity.program_digest == session.program.digest
    assert binding.identity.catalog_digest == session.catalog.digest
    assert binding.identity.context.ontology_revision == session.program.digest
    assert binding.identity.source.commit_horizon == session.commit_horizon
    path = tmp_path / "session.json"
    assert save_session(session, path) == binding.session_digest
    binding.verify(graph, load_session(path))
    graph.epistemic.retract_claim(versions[0].logical_id, valid=(0, 100))
    with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
        bind_session_generation(graph, session)
    historical = bind_session_generation(graph, session, freshness="historical")
    binding.identity.require_compatible(historical.identity)
    binding.verify(graph, load_session(path), freshness="historical")
    advanced = advance_session(graph, session, limits=ReasoningLimits()).session
    assert advanced.facts == session.facts
    next_binding = bind_session_generation(graph, advanced)
    with pytest.raises(ReasoningError, match="source drifted"):
        binding.identity.require_compatible(next_binding.identity)
    with pytest.raises(ProvenanceMismatchError, match="session state drifted"):
        binding.verify(graph, advanced, freshness="historical")


def test_same_facts_do_not_authenticate_missing_independent_version(bound_source):
    graph, session, _ = bound_source
    kept = session.ledger.versions[0]
    forged = replace(session, ledger=EvidenceLedger(session.ledger.scope_id, (kept,)),
                     version_logical=tuple(pair for pair in session.version_logical if pair[0] == kept.version_id))
    assert forged.facts == session.facts
    with pytest.raises(ProvenanceMismatchError, match="evidence"):
        bind_session_generation(graph, forged)


@pytest.mark.parametrize("tamper", ["logical_id", "fact", "selection", "mapping"])
def test_source_version_mapping_and_policy_must_match(bound_source, tamper):
    graph, session, _ = bound_source
    if tamper == "logical_id":
        forged = replace(session, version_logical=tuple((vid, "wrong") for vid, _ in session.version_logical))
    elif tamper == "fact":
        forged = replace(session, ledger=EvidenceLedger(session.ledger.scope_id, tuple(
            AssertionVersion(v.version_id, TypedFact("person", ("a",))) for v in session.ledger.versions)))
    elif tamper == "selection":
        forged = replace(session, scope=replace(session.scope, selection=ClaimSelection(agents=("one",))))
    else:
        forged = replace(session, scope=replace(session.scope, mapping=ClaimMapping((("p", "other"),))))
    with pytest.raises(ProvenanceMismatchError, match="evidence"):
        bind_session_generation(graph, forged)


def test_catalog_content_is_not_just_rule_key_coverage(bound_source):
    graph, session, _ = bound_source
    entry = session.catalog.entries[0]
    wrong = replace(session, catalog=RuleCatalog((replace(entry, content_digest="sha256:wrong"),
                                                 *session.catalog.entries[1:])))
    with pytest.raises(ReasoningError, match="catalog content"):
        bind_session_generation(graph, wrong)
    versioned = replace(session, catalog=RuleCatalog((replace(entry, version_id="external-v2", ordinal=2),
                                                     *session.catalog.entries[1:])))
    rebound = bind_session_generation(graph, versioned)
    with pytest.raises(ReasoningError, match="catalog_digest drifted"):
        bind_session_generation(graph, session).identity.require_compatible(rebound.identity)


def test_foreign_database_and_unavailable_horizon_fail_closed(bound_source, tmp_path):
    graph, session, _ = bound_source
    other = GraphDB.create(tmp_path / "other", backend="lmdb", serializer="json")
    try:
        with pytest.raises((ReasoningError, ProvenanceMismatchError, ValueError)):
            bind_session_generation(other, session, freshness="historical")
    finally:
        other.close()
    with pytest.raises((ReasoningError, ProvenanceMismatchError, ValueError)):
        bind_session_generation(graph, replace(session, commit_horizon=999), freshness="historical")


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_resurrected_session_rebinds_to_authenticated_source(tmp_path, backend, module):
    pytest.importorskip(module)
    from gestaltdb.reasoning.adapters.sessions import advance_session
    graph = GraphDB.create(tmp_path / "db", backend=backend, serializer="json")
    try:
        from gestaltdb.reasoning.adapters.sessions import SessionScope, start_session
        from gestaltdb.reasoning.catalog import RuleCatalog
        from gestaltdb.reasoning.model import ReasoningLimits, Variable
        from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom
        x, y = Variable("x"), Variable("y")
        program = GenericProgram([GenericRule("r", [TypedAtom("p", [x, y])], [], TypedAtom("q", [x]))])
        scope = SessionScope("default", 50, ClaimSelection(), ClaimMapping())
        first = graph.epistemic.assert_claim(subject="a", predicate="p", object="b", agent="registry",
                                   polarity="positive", valid=(0, 100))
        session = start_session(graph, program, catalog_for_typed_program(program),
                                scope=scope, limits=ReasoningLimits())
        binding = bind_session_generation(graph, session)
        binding.verify(graph, session)
        graph.epistemic.retract_claim(first.logical_id, valid=(0, 100))
        emptied = advance_session(graph, session, limits=ReasoningLimits()).session
        assert emptied.facts == ()
        with pytest.raises(ProvenanceMismatchError, match="session state drifted"):
            binding.verify(graph, emptied)
        graph.epistemic.assert_claim(subject="a", predicate="p", object="b", agent="registry",
                           polarity="positive", valid=(0, 100))
        resurrected = advance_session(graph, emptied, limits=ReasoningLimits()).session
        rebound = bind_session_generation(graph, resurrected)
        rebound.verify(graph, resurrected)
        with pytest.raises(ReasoningError, match="source drifted"):
            binding.identity.require_compatible(rebound.identity)
    finally:
        graph.close()


@pytest.mark.parametrize("field,value", [
    ("max_versions", 1), ("max_facts", 1), ("max_supports", 1), ("max_commits", 1),
])
def test_limits_are_explicit(bound_source, field, value):
    graph, session, _ = bound_source
    with pytest.raises(ReasoningLimitError, match=field):
        bind_session_generation(graph, session, limits=SessionAdmissionLimits(**{field: value}))


def test_cancellation_and_unknown_modes(bound_source):
    graph, session, _ = bound_source
    with pytest.raises(ReasoningCancelledError):
        bind_session_generation(graph, session, cancel=lambda: True)
    with pytest.raises(ReasoningError, match="freshness"):
        bind_session_generation(graph, session, freshness="guess")
    with pytest.raises(ReasoningError, match="SessionAdmissionLimits"):
        bind_session_generation(graph, session, limits=None)
    with pytest.raises(ReasoningError, match="ReasoningSession"):
        bind_session_generation(graph, object())
    with pytest.raises(ReasoningError, match="closed scopes"):
        catalog_for_typed_program(GenericProgram(closed=("p",)))


@pytest.mark.parametrize("field,value", [
    ("semantics_profile", "range-profile"), ("dictionary_id", "compact-dictionary"),
])
def test_unsupported_profiles_and_dictionaries_fail_closed(bound_source, field, value):
    graph, session, _ = bound_source
    binding = bind_session_generation(graph, session)
    source = replace(binding.source_binding, identity=replace(binding.identity, **{field: value}))
    with pytest.raises(ProvenanceMismatchError, match="binding mismatch"):
        replace(binding, source_binding=source)


def test_candidate_bound_counts_before_policy_filtering(bound_source):
    graph, session, _ = bound_source
    graph.epistemic.assert_claim(subject="unrelated", predicate="p", object="c", agent="excluded",
                       polarity="positive", valid=(0, 100))
    selected = start_session(graph, session.program, session.catalog,
                             scope=replace(session.scope, selection=ClaimSelection(agents=("one",))),
                             limits=ReasoningLimits())
    assert len(selected.ledger.versions) == 1
    with pytest.raises(ReasoningLimitError, match="max_versions candidates"):
        bind_session_generation(graph, selected, limits=SessionAdmissionLimits(max_versions=2))


@pytest.mark.parametrize("include_derived", [False, True])
def test_effective_policy_aliases_and_world_are_authenticated(bound_source, include_derived):
    graph, session, versions = bound_source
    graph.epistemic.assert_claim(subject="foreign", predicate="p", object="b", agent="one", source="registry",
                       polarity="positive", world="foreign", valid=(0, 100))
    graph.epistemic.assert_claim(subject="excluded", predicate="p", object="b", agent="one", source="elsewhere",
                       polarity="positive", valid=(0, 100))
    graph.epistemic.assert_claim(subject="negative", predicate="p", object="b", agent="one", source="registry",
                       polarity="negative", valid=(0, 100))
    derived = graph.epistemic.assert_claim(subject="derived", predicate="p", object="b", agent="gestaltdb:ontology",
                                 source="registry", polarity="positive", valid=(0, 100),
                                 provenance={"derived_by": "gestaltdb.ontology"})
    scope = replace(session.scope, selection=ClaimSelection(agents=("one", "gestaltdb:ontology"),
                                                           sources=("registry",), include_derived=include_derived),
                    mapping=ClaimMapping((("p", "linked"),)))
    selected = start_session(graph, session.program, session.catalog, scope=scope, limits=ReasoningLimits())
    binding = bind_session_generation(graph, selected)
    binding.verify(graph, selected)
    expected_ids = {versions[0].version_id} | ({derived.version_id} if include_derived else set())
    assert {v.version_id for v in selected.ledger.versions} == expected_ids
    assert all(f.predicate == "linked" for f in selected.ledger.facts)
    assert binding.identity.context.mapping_id == scope.mapping.mapping_id
    assert binding.identity.context.source_policy_id == scope.selection.policy_id


def test_historical_correction_does_not_replace_the_admitted_prefix(bound_source):
    graph, session, versions = bound_source
    binding = bind_session_generation(graph, session)
    graph.epistemic.correct_claim(versions[0].logical_id, supersedes_version_id=versions[0].version_id, valid=(0, 100))
    binding.verify(graph, session, freshness="historical")
    advanced = advance_session(graph, session, limits=ReasoningLimits()).session
    bind_session_generation(graph, advanced).verify(graph, advanced)
    with pytest.raises(ProvenanceMismatchError, match="session state drifted"):
        binding.verify(graph, advanced)


def test_cancellation_during_independent_scan(bound_source):
    graph, session, _ = bound_source
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        return calls == 4

    with pytest.raises(ReasoningCancelledError):
        bind_session_generation(graph, session, cancel=cancel)
    assert calls == 4


@pytest.mark.parametrize("field", ["max_versions", "max_facts", "max_supports", "max_commits"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_admission_limits_reject_invalid_values(field, value):
    with pytest.raises(ReasoningError, match=field):
        SessionAdmissionLimits(**{field: value})
