"""Storage-neutral generation compatibility values, never authentication proofs.

Adapters authenticate the source. These values compose its identity with the
existing InputContext, catalog and compilation contracts; constructing strings
or computing a digest does not authenticate data or prove a derivation.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import ReasoningError, _text
from .protocols import InputContext
from .relational import CompilationIdentity, _digest
from .executors import EXECUTORS, INDEXED_PYTHON, INDEXED_SCC, POLARS_PROTOTYPE


@dataclass(frozen=True)
class TemporalSourceIdentity:
    """One adapter-verified temporal prefix and valid-time point."""

    database_id: str
    commit_horizon: int
    provenance_token: str
    valid_time_us: int

    def __post_init__(self) -> None:
        _text(self.database_id, "database_id")
        _text(self.provenance_token, "provenance_token")
        if (isinstance(self.commit_horizon, bool) or not isinstance(self.commit_horizon, int)
                or self.commit_horizon < 0):
            raise ReasoningError("commit_horizon must be a non-negative integer")
        if isinstance(self.valid_time_us, bool) or not isinstance(self.valid_time_us, int):
            raise ReasoningError("valid_time_us must be an integer")


@dataclass(frozen=True)
class CurrentSourceIdentity:
    """One adapter-verified mutable snapshot, never a temporal commit horizon.

    The provenance token includes state, backend, serializer and snapshot kind.
    This value alone authenticates nothing and cannot reopen an expired view.
    """

    database_id: str
    snapshot_sequence: int
    provenance_token: str

    def __post_init__(self) -> None:
        _text(self.database_id, "database_id")
        _text(self.provenance_token, "provenance_token")
        if (isinstance(self.snapshot_sequence, bool) or not isinstance(self.snapshot_sequence, int)
                or self.snapshot_sequence < 0):
            raise ReasoningError("snapshot_sequence must be a non-negative integer")


@dataclass(frozen=True)
class GenerationIdentity:
    """Exact logical reuse identity; physical executor/batch settings stay separate."""

    source: TemporalSourceIdentity | CurrentSourceIdentity
    context: InputContext
    program_digest: str
    catalog_digest: str
    semantics_profile: str
    dictionary_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, (TemporalSourceIdentity, CurrentSourceIdentity)) or not isinstance(self.context, InputContext):
            raise ReasoningError("temporal/current source identity and InputContext required")
        for name in ("program_digest", "catalog_digest", "semantics_profile", "dictionary_id"):
            _text(getattr(self, name), name)

    @property
    def digest(self) -> str:
        context, source = self.context, self.source
        # Preserve every existing temporal digest byte-for-byte. Current-state
        # identities get a separate domain, not fake temporal sentinel fields.
        if isinstance(source, TemporalSourceIdentity):
            domain = "gestaltdb-generation-identity-v1"
            fields = [source.database_id, source.commit_horizon, source.provenance_token, source.valid_time_us]
        else:
            domain = "gestaltdb-current-generation-identity-v1"
            fields = [source.database_id, source.snapshot_sequence, source.provenance_token]
        return _digest([domain, fields,
                        [context.context_id, context.world, context.ontology_revision,
                         context.source_policy_id, context.mapping_id],
                        self.program_digest, self.catalog_digest, self.semantics_profile, self.dictionary_id])

    def require_compatible(self, other: GenerationIdentity) -> None:
        """Reject every logical identity change before reusing generation state."""
        if not isinstance(other, GenerationIdentity):
            raise ReasoningError("GenerationIdentity required")
        for name in self.__dataclass_fields__:
            if getattr(self, name) != getattr(other, name):
                raise ReasoningError(f"generation {name} drifted; rebind or rebuild explicitly")

    def require_compilation(self, compilation: CompilationIdentity) -> None:
        """Check logical plan compatibility, not executor admission or provenance.

        Executor-specific plan reuse still requires CompilationIdentity's full
        compatibility check. A logical generation does not select an executor.
        """
        if not isinstance(compilation, CompilationIdentity):
            raise ReasoningError("CompilationIdentity required")
        for name in ("program_digest", "context", "semantics_profile", "dictionary_id"):
            if getattr(self, name) != getattr(compilation, name):
                raise ReasoningError(f"compilation {name} does not match the generation")


ADMISSION_MODES = ("closure", "proofs", "maintenance-supports", "temporal-closure")
_ADMISSION_INDEXED = (INDEXED_PYTHON, INDEXED_SCC)


@dataclass(frozen=True)
class AdmissionRequest:
    """One explicit execution request against a pinned generation."""

    mode: str = "closure"
    executor: str = INDEXED_PYTHON
    dictionary_id: str = "external-strings-v1"

    def __post_init__(self) -> None:
        if self.mode not in ADMISSION_MODES:
            raise ReasoningError(f"mode must be one of {ADMISSION_MODES}")
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
        if self.mode not in ADMISSION_MODES:
            raise ReasoningError(f"mode must be one of {ADMISSION_MODES}")
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
        if request.executor not in _ADMISSION_INDEXED:
            raise ReasoningError("proofs require an indexed witness engine")
        return Admission(request.mode, request.executor, generation.digest,
                         ("witnesses-require-indexed-engines", "generation-bound"))
    if request.mode == "maintenance-supports":
        if request.executor not in _ADMISSION_INDEXED:
            raise ReasoningError("maintenance supports require an indexed engine")
        return Admission(request.mode, request.executor, generation.digest,
                         ("supports-live-in-maintenance-index", "generation-bound"))
    if request.mode == "temporal-closure":
        if request.executor not in _ADMISSION_INDEXED:
            raise ReasoningError("temporal closure requires the generation-bound segment path")
        return Admission(request.mode, request.executor, generation.digest,
                         ("change-segments-are-indexed-only", "generation-bound"))
    if request.executor == POLARS_PROTOTYPE:
        return Admission(request.mode, request.executor, generation.digest,
                         ("explicit-closure-only-opt-in", "generation-bound"))
    return Admission(request.mode, request.executor, generation.digest,
                     ("indexed-reference-default", "generation-bound"))
