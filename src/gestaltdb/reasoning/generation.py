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
class GenerationIdentity:
    """Exact logical reuse identity; physical executor/batch settings stay separate."""

    source: TemporalSourceIdentity
    context: InputContext
    program_digest: str
    catalog_digest: str
    semantics_profile: str
    dictionary_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, TemporalSourceIdentity) or not isinstance(self.context, InputContext):
            raise ReasoningError("TemporalSourceIdentity and InputContext required")
        for name in ("program_digest", "catalog_digest", "semantics_profile", "dictionary_id"):
            _text(getattr(self, name), name)

    @property
    def digest(self) -> str:
        context, source = self.context, self.source
        return _digest(["gestaltdb-generation-identity-v1",
                        [source.database_id, source.commit_horizon, source.provenance_token, source.valid_time_us],
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
