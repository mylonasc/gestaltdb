"""Generation-bound executor admission; no executors are selected here.

``select_executor`` (in ``executors.py``) chooses a physical engine from an
explicit request. ``admit`` answers the prior question: is the requested
(mode, executor, dictionary) combination compatible with one pinned
generation? Anything else fails closed instead of silently weakening the
requested semantics. Profiles travel as opaque generation metadata; this
module records but does not interpret them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .executors import EXECUTORS, INDEXED_PYTHON, INDEXED_SCC, POLARS_PROTOTYPE
from .generation import GenerationIdentity
from .model import ReasoningError, _text

MODES = ("closure", "proofs", "maintenance-supports", "temporal-closure")
INDEXED = (INDEXED_PYTHON, INDEXED_SCC)


@dataclass(frozen=True)
class AdmissionRequest:
    """One explicit execution request against a pinned generation."""

    mode: str = "closure"
    executor: str = INDEXED_PYTHON
    dictionary_id: str = "external-strings-v1"

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ReasoningError(f"mode must be one of {MODES}")
        if self.executor not in EXECUTORS:
            raise ReasoningError(f"executor must be one of {EXECUTORS}")
        _text(self.dictionary_id, "dictionary_id")


@dataclass(frozen=True)
class Admission:
    """Compatible triple: the generation digest it was checked against."""

    mode: str
    executor: str
    generation_digest: str
    rationale: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ReasoningError(f"mode must be one of {MODES}")
        if self.executor not in EXECUTORS:
            raise ReasoningError(f"executor must be one of {EXECUTORS}")
        _text(self.generation_digest, "generation_digest")
        if not isinstance(self.rationale, (tuple, list)) or not self.rationale:
            raise ReasoningError("rationale must be a non-empty tuple of codes")
        for code in self.rationale:
            _text(code, "rationale code")
        object.__setattr__(self, "rationale", tuple(self.rationale))


def admit(generation: GenerationIdentity, request: AdmissionRequest) -> Admission:
    """Check a request against a pinned generation; never downgrade silently.

    Dictionary identity must match exactly: a generation computed under one
    dictionary cannot be consumed through another without explicit recoding.
    Witness-bearing and stateful modes (proofs, maintenance supports,
    temporal closure) are indexed-engine only; the Polars prototype stays
    closure-only. Unknown combinations raise instead of falling back.
    """
    if not isinstance(generation, GenerationIdentity):
        raise ReasoningError("generation must be a GenerationIdentity")
    if not isinstance(request, AdmissionRequest):
        raise ReasoningError("request must be an AdmissionRequest")
    if request.dictionary_id != generation.dictionary_id:
        raise ReasoningError("dictionary mismatch; recode or recompile explicitly")
    if request.mode == "proofs":
        if request.executor not in INDEXED:
            raise ReasoningError("proofs require an indexed witness engine")
        return Admission(request.mode, request.executor, generation.digest,
                         ("witnesses-require-indexed-engines", "generation-bound"))
    if request.mode == "maintenance-supports":
        if request.executor not in INDEXED:
            raise ReasoningError("maintenance supports require an indexed engine")
        return Admission(request.mode, request.executor, generation.digest,
                         ("supports-live-in-maintenance-index", "generation-bound"))
    if request.mode == "temporal-closure":
        if request.executor not in INDEXED:
            raise ReasoningError("temporal closure requires the generation-bound segment path")
        return Admission(request.mode, request.executor, generation.digest,
                         ("change-segments-are-indexed-only", "generation-bound"))
    if request.executor == POLARS_PROTOTYPE:
        return Admission(request.mode, request.executor, generation.digest,
                         ("explicit-closure-only-opt-in", "generation-bound"))
    return Admission(request.mode, request.executor, generation.digest,
                     ("indexed-reference-default", "generation-bound"))
