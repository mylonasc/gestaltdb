"""Authenticated finite-range extraction, evaluation and explicit re-admission.

Canonical point reads resolve winners at every historical validity boundary.
No point token is treated as a range identity, and no publication is performed.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...epistemic import ClaimObjectKind, ClaimPolarity
from ...readview import GraphReadView, ReadViewProvenance, ProvenanceMismatchError
from ...temporal import TemporalInstant
from ...versioning import ClaimVersion
from ..catalog import RuleCatalog
from ..generation import GenerationIdentity, TemporalRangeSourceIdentity
from ..intervals import Interval, IntervalSet
from ..model import ReasoningError, ReasoningLimitError, ReasoningLimits, _text, check_cancelled
from ..protocols import InputContext
from ..temporal_closure import TemporalClosure, TemporalFact, ClosureVerification, evaluate_range, verify_closure
from ..typed import GenericProgram, TypedFact
from .claims import ClaimMapping, ClaimSelection, _digest, _verify_graph
from .session_generation import catalog_for_typed_program
from .sessions import SessionScope, _check_version, _filters, _SkipVersion, _typed_fact


PROFILE = "positive-typed-range-v1"
DICTIONARY = "typed-values-v1"


@dataclass(frozen=True)
class RangeSourceLimits:
    """Cumulative yielded-source bounds, not backend-internal work or RSS."""

    max_commits: int = 10_000
    max_history_versions: int = 100_000
    max_scanned_claims: int = 100_000
    max_segments: int = 10_000
    max_evidence: int = 100_000
    max_supports: int = 100_000
    max_sweep_memberships: int = 1_000_000

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ReasoningError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class RangeEvidence:
    """One exact active assertion version and its clipped validity pieces."""

    version_id: str
    logical_id: str
    fact: TypedFact
    valid: IntervalSet

    def __post_init__(self):
        _text(self.version_id, "version_id")
        _text(self.logical_id, "logical_id")
        if not isinstance(self.fact, TypedFact) or not isinstance(self.valid, IntervalSet) or not self.valid.intervals:
            raise ReasoningError("range evidence requires a typed fact and nonempty validity")


def _source(provenance, start, end):
    return TemporalRangeSourceIdentity(provenance.database_id, provenance.commit_horizon,
                                       provenance.token, start, end)


def _context(provenance, world, program, selection, mapping, start, end):
    return InputContext(_digest(["range-context-v1", provenance.token, start, end, world,
                                 program, selection.policy_id, mapping.mapping_id]),
                        world, program, selection.policy_id, mapping.mapping_id)


@dataclass(frozen=True)
class RangeGenerationBinding:
    """Source-policy metadata, never a computation or publication certificate."""

    identity: GenerationIdentity
    provenance: ReadViewProvenance
    selection: ClaimSelection
    mapping: ClaimMapping

    def __post_init__(self):
        if (not isinstance(self.identity, GenerationIdentity)
                or not isinstance(self.identity.source, TemporalRangeSourceIdentity)
                or not isinstance(self.provenance, ReadViewProvenance)
                or not isinstance(self.selection, ClaimSelection) or not isinstance(self.mapping, ClaimMapping)):
            raise ReasoningError("range identity, temporal provenance and explicit policy required")
        p = ReadViewProvenance.from_dict(self.provenance.to_dict())
        source, identity = self.identity.source, self.identity
        if (p.valid_time_us != source.valid_start_us or source != _source(p, source.valid_start_us, source.valid_end_us)
                or identity.context != _context(p, identity.context.world, identity.program_digest,
                                                self.selection, self.mapping, source.valid_start_us, source.valid_end_us)
                or identity.semantics_profile != PROFILE or identity.dictionary_id != DICTIONARY):
            raise ProvenanceMismatchError("range generation source/context binding mismatch")
        object.__setattr__(self, "provenance", p)

    def verify_source(self, graph, *, freshness="latest"):
        """Authenticate the prefix; latest rejects movement, neither permits publication."""
        if freshness not in ("latest", "historical"):
            raise ReasoningError("freshness must be latest or historical")
        _verify_graph(self.provenance, graph)
        if freshness == "latest":
            with graph.read_view(valid_time=self.provenance.valid_time_us) as view:
                if view.commit_horizon != self.provenance.commit_horizon:
                    raise ProvenanceMismatchError("range source horizon moved; re-evaluate explicitly")


@dataclass(frozen=True)
class RangeReasoningResult:
    binding: RangeGenerationBinding
    closure: TemporalClosure
    evidence: tuple[RangeEvidence, ...]
    history_versions: int
    scanned_claims: int
    extraction_segments: tuple[Interval, ...]

    def __post_init__(self):
        if not isinstance(self.binding, RangeGenerationBinding) or not isinstance(self.closure, TemporalClosure):
            raise ReasoningError("range binding and TemporalClosure required")
        if not isinstance(self.evidence, (tuple, list)) or not all(isinstance(e, RangeEvidence) for e in self.evidence):
            raise ReasoningError("evidence must contain RangeEvidence values")
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if not isinstance(self.extraction_segments, (tuple, list)) or not all(
                isinstance(s, Interval) for s in self.extraction_segments):
            raise ReasoningError("extraction_segments must contain Interval values")
        object.__setattr__(self, "extraction_segments", tuple(self.extraction_segments))
        for name in ("history_versions", "scanned_claims"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ReasoningError(f"{name} must be a non-negative integer")


class _RangeSource:
    def __init__(self, view, program, catalog, world, selection, mapping, start, end, bounds, cancel):
        if type(view) is not GraphReadView or not isinstance(view.provenance, ReadViewProvenance):
            raise ReasoningError("temporal GraphReadView required")
        if not isinstance(bounds, RangeSourceLimits):
            raise ReasoningError("source_limits must be RangeSourceLimits")
        if not isinstance(selection, ClaimSelection) or not isinstance(mapping, ClaimMapping):
            raise ReasoningError("explicit ClaimSelection and ClaimMapping required")
        _text(world, "world")
        if not isinstance(program, GenericProgram) or not isinstance(catalog, RuleCatalog):
            raise ReasoningError("GenericProgram and RuleCatalog required")
        expected = catalog_for_typed_program(program)
        if {e.rule_key: e.content_digest for e in catalog.entries} != {e.rule_key: e.content_digest for e in expected.entries}:
            raise ReasoningError("catalog content does not match typed program")
        provenance = ReadViewProvenance.from_dict(view.provenance.to_dict())
        source = _source(provenance, start, end)  # strict finite-range validation
        if provenance.valid_time_us != start:
            raise ReasoningError("read view must be anchored at range start")
        if provenance.commit_horizon > bounds.max_commits:
            raise ReasoningLimitError("range extraction exceeded max_commits")
        identity = GenerationIdentity(source, _context(provenance, world, program.digest, selection, mapping, start, end),
                                      program.digest, catalog.digest, PROFILE, DICTIONARY)
        self.binding = RangeGenerationBinding(identity, provenance, selection, mapping)
        self.view, self.graph, self.store = view, view._graph, view._graph.store
        self.serializer = self.graph.serializer
        self.bounds, self.cancel = bounds, cancel
        self.check()

    def check(self, authenticate=False):
        check_cancelled(self.cancel)
        if (self.view._graph is not self.graph or self.graph.store is not self.store
                or self.graph.serializer is not self.serializer or self.view.provenance != self.binding.provenance
                or self.view.valid_time != TemporalInstant(self.binding.provenance.valid_time_us)):
            raise ProvenanceMismatchError("range source view/store/decoder was rebound")
        if authenticate:
            _verify_graph(self.binding.provenance, self.graph)
            self.check()

    def extract(self):
        self.check(authenticate=True)
        p, identity = self.binding.provenance, self.binding.identity
        source, bounds = identity.source, self.bounds
        cuts = {source.valid_start_us, source.valid_end_us}
        history = 0
        for version in self.view.iter_claim_versions():
            self.check()
            history += 1
            if history > bounds.max_history_versions:
                raise ReasoningLimitError("range extraction exceeded max_history_versions")
            if not isinstance(version, ClaimVersion) or not 0 < version.commit_id <= p.commit_horizon:
                raise ProvenanceMismatchError("invalid pinned historical claim version")
            for instant in (version.valid.start, version.valid.end):
                if instant is not None and source.valid_start_us < instant.epoch_microseconds < source.valid_end_us:
                    cuts.add(instant.epoch_microseconds)
                    if len(cuts) - 1 > bounds.max_segments:
                        raise ReasoningLimitError("range extraction exceeded max_segments")
        ordered = sorted(cuts)
        segments = tuple(Interval(a, b) for a, b in zip(ordered, ordered[1:]))
        found, scanned = {}, 0
        for segment in segments:
            self.check()
            scope = SessionScope(identity.context.world, segment.start, self.binding.selection, self.binding.mapping)
            agents, sources = _filters(scope)
            instant = TemporalInstant(segment.start)
            for version in self.view.iter_claims_as_of(valid_time=instant, world=scope.world,
                    polarity=ClaimPolarity.POSITIVE, object_kind=ClaimObjectKind.ENTITY):
                self.check()
                scanned += 1
                if scanned > bounds.max_scanned_claims:
                    raise ReasoningLimitError("range extraction exceeded max_scanned_claims")
                try:
                    claim = _check_version(version, p.commit_horizon, instant, scope, agents, sources)
                except _SkipVersion:
                    continue
                fact = _typed_fact(claim, scope.mapping)
                key = version.version_id
                old = found.get(key)
                if old is not None and (old.logical_id != version.logical_id or old.fact != fact):
                    raise ProvenanceMismatchError("conflicting range evidence version")
                valid = IntervalSet([segment])
                found[key] = RangeEvidence(key, version.logical_id, fact, valid if old is None else old.valid.union(valid))
                if len(found) > bounds.max_evidence:
                    raise ReasoningLimitError("range extraction exceeded max_evidence")
            self.check()
        self.check(authenticate=True)
        return tuple(found[key] for key in sorted(found)), history, scanned, segments


def _evaluate(program, evidence, limits, bounds, cancel):
    return evaluate_range(program, [TemporalFact(e.fact, e.valid) for e in evidence], limits=limits,
                          max_segments=bounds.max_segments, max_supports=bounds.max_supports,
                          max_sweep_rows=bounds.max_evidence, max_sweep_events=2 * bounds.max_scanned_claims,
                          max_sweep_memberships=bounds.max_sweep_memberships, cancel=cancel)


def evaluate_pinned_range(view: GraphReadView, program: GenericProgram, catalog: RuleCatalog, *,
                          valid_start_us: int, valid_end_us: int, world: str,
                          selection: ClaimSelection, mapping: ClaimMapping, limits: ReasoningLimits,
                          source_limits: RangeSourceLimits = RangeSourceLimits(),
                          expected_generation: GenerationIdentity | None = None, cancel=None) -> RangeReasoningResult:
    """Extract complete interval inputs and evaluate; no caller closure is trusted."""
    if not isinstance(limits, ReasoningLimits):
        raise ReasoningError("ReasoningLimits required")
    check_cancelled(cancel)
    if isinstance(program, GenericProgram) and len(program.rules) > limits.max_rules:
        raise ReasoningLimitError("max_rules exceeded")
    source = _RangeSource(view, program, catalog, world, selection, mapping,
                          valid_start_us, valid_end_us, source_limits, cancel)
    if expected_generation is not None:
        source.binding.identity.require_compatible(expected_generation)
    evidence, history, scanned, segments = source.extract()
    closure = _evaluate(program, evidence, limits, source_limits, cancel)
    source.check(authenticate=True)
    return RangeReasoningResult(source.binding, closure, evidence, history, scanned, segments)


def admit_range_result(view: GraphReadView, result: RangeReasoningResult, program: GenericProgram, catalog: RuleCatalog, *,
                        expected_generation: GenerationIdentity, limits: ReasoningLimits,
                        source_limits: RangeSourceLimits = RangeSourceLimits(), cancel=None) -> ClosureVerification:
    """Re-extract exact source evidence and recompute closure before reuse.

    This deliberately expensive opt-in checks source identity, input completeness
    and engine-conformance separately. It is not a cache-speedup or publication
    permit, and does not independently verify the implementation of the engine.
    """
    if not isinstance(result, RangeReasoningResult) or not isinstance(expected_generation, GenerationIdentity):
        raise ReasoningError("RangeReasoningResult and exact expected_generation required")
    if not isinstance(source_limits, RangeSourceLimits) or not isinstance(limits, ReasoningLimits):
        raise ReasoningError("RangeSourceLimits and ReasoningLimits required")
    check_cancelled(cancel)
    if isinstance(program, GenericProgram) and len(program.rules) > limits.max_rules:
        raise ReasoningLimitError("max_rules exceeded")
    expected_generation.require_compatible(result.binding.identity)
    for size, maximum, name in ((len(result.evidence), source_limits.max_evidence, "max_evidence"),
            (len(result.closure.base), source_limits.max_evidence, "max_evidence"),
            (len(result.closure.segments), source_limits.max_segments, "max_segments"),
            (len(result.extraction_segments), source_limits.max_segments, "max_segments"),
            (len(result.closure.derivations), source_limits.max_supports, "max_supports")):
        if size > maximum:
            raise ReasoningLimitError(f"range admission exceeded {name}")
    binding, interval = result.binding, result.binding.identity.source
    for rows in (result.evidence, result.closure.base):
        pieces = 0
        for row in rows:
            check_cancelled(cancel)
            pieces += len(row.valid.intervals)
            if pieces > source_limits.max_scanned_claims:
                raise ReasoningLimitError("range admission exceeded interval-piece bound")
    source = _RangeSource(view, program, catalog, binding.identity.context.world, binding.selection, binding.mapping,
                          interval.valid_start_us, interval.valid_end_us, source_limits, cancel)
    source.binding.identity.require_compatible(expected_generation)
    evidence, history, scanned, segments = source.extract()
    if result.evidence != evidence:
        raise ProvenanceMismatchError("range evidence does not match complete authenticated source")
    fresh_closure = _evaluate(program, evidence, limits, source_limits, cancel)
    if (result.closure != fresh_closure or result.extraction_segments != segments
            or result.history_versions != history or result.scanned_claims != scanned):
        raise ReasoningError("range closure differs from fresh evaluation; rematerialize explicitly")
    verification = verify_closure(program, result.closure, limits=limits, cancel=cancel)
    source.check(authenticate=True)
    return verification
