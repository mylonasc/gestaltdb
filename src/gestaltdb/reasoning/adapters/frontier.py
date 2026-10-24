"""Opt-in temporal claim demand under one authenticated caller-owned read view."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...readview import GraphReadView, ReadViewProvenance
from ..extraction import ExtractionRequest
from ..frontier import FrontierAnswer, evaluate_frontier
from ..model import Atom, Program, ReasoningError, ReasoningLimitError, ReasoningLimits, _text, check_cancelled
from ..protocols import InputContext
from .batched import PinnedBatchedClaimSource
from .claims import ClaimMapping, ClaimSelection, ClaimSourceLimits, _context, _verify_graph


@dataclass(frozen=True)
class PinnedFrontierAnswer:
    """Goal answers plus source authentication and cumulative extraction work."""

    answer: FrontierAnswer
    context: InputContext
    provenance: ReadViewProvenance
    scanned_claims: int


def answer_pinned_claims(view: GraphReadView, program: Program, goal: Atom, *,
                         world: str, ontology_revision: str,
                         selection: ClaimSelection, mapping: ClaimMapping,
                         limits: ReasoningLimits,
                         source_limits: ClaimSourceLimits = ClaimSourceLimits(),
                         max_requests: int = 10_000,
                         cancel: Callable[[], bool] | None = None) -> PinnedFrontierAnswer:
    """Read assertions for discovered bindings, never repinning the source.

    Supports only the positive binary entity Program/Atom fragment. The returned
    evidence is the fetched input set, not an explanation or independent proof.
    Scanned counts are claim versions yielded by indexed queries, not internal KV
    visits/authentication/history reads. No publication or persistent reuse.
    """
    if type(view) is not GraphReadView:
        raise ReasoningError("a temporal GraphReadView is required")
    if not isinstance(program, Program) or not isinstance(goal, Atom):
        raise ReasoningError("positive binary Program and Atom goal required")
    if not isinstance(selection, ClaimSelection) or not isinstance(mapping, ClaimMapping):
        raise ReasoningError("explicit ClaimSelection and ClaimMapping required")
    if not isinstance(source_limits, ClaimSourceLimits) or not isinstance(limits, ReasoningLimits):
        raise ReasoningError("explicit claim source and reasoning limits required")
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests < 1:
        raise ReasoningError("max_requests must be a positive integer")
    if len(program.rules) > limits.max_rules:
        raise ReasoningLimitError("max_rules exceeded")
    _text(world, "world")
    _text(ontology_revision, "ontology_revision")
    check_cancelled(cancel)
    provenance = ReadViewProvenance.from_dict(view.provenance.to_dict())
    graph = view._graph
    scanned = 0

    def verify():
        check_cancelled(cancel)
        if (view._graph is not graph or view.provenance != provenance
                or view.valid_time.epoch_microseconds != provenance.valid_time_us):
            raise ReasoningError("frontier source view was rebound")
        _verify_graph(provenance, graph)

    def read(request):
        nonlocal scanned
        verify()
        predicates = mapping.storage_predicates((request.predicate,))
        if not predicates:
            return
        remaining = source_limits.max_scanned_claims - scanned
        if remaining <= 0:
            raise ReasoningLimitError("frontier exceeded max_scanned_claims")
        extraction = ExtractionRequest(world, predicates=predicates, subject=request.subject,
                                       object=request.object, batch_rows=256, max_rows=limits.max_input_facts)
        with PinnedBatchedClaimSource(view, request=extraction, ontology_revision=ontology_revision,
                                      selection=selection, mapping=mapping,
                                      limits=ClaimSourceLimits(remaining)) as source:
            for batch in source.iter_batches(cancel=cancel):
                check_cancelled(cancel)
                yield from batch.evidence
            scanned += source.profile.scanned
        verify()

    verify()
    answer = evaluate_frontier(program, goal, read, limits=limits, max_requests=max_requests, cancel=cancel)
    verify()
    # Full source identity stays distinct from the goal/program carried in result.
    context = _context(provenance, world, ontology_revision, selection, mapping)
    return PinnedFrontierAnswer(answer, context, provenance, scanned)
