"""Authenticated temporal-claims binding for storage-neutral generation values.

This is an opt-in bridge, not a publisher or proof checker. It validates an
existing ClaimReasoningResult and verifies the pinned database prefix. Latest
checks are conservative; a publisher must perform them under its writer guard.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...graphdb import GraphDB
from ...ontology.compiler import CompiledOntology
from ...readview import ProvenanceMismatchError, ReadViewProvenance
from ..catalog import CatalogEntry, RuleCatalog
from ..extraction import ExtractionRequest
from ..generation import GenerationIdentity, TemporalSourceIdentity
from ..model import Program, ReasoningError
from .claims import ClaimMapping, ClaimReasoningResult, ClaimSelection, _context, _verify_graph


def _source(provenance):
    return TemporalSourceIdentity(provenance.database_id, provenance.commit_horizon,
                                  provenance.token, provenance.valid_time_us)


def catalog_for_ontology(compiled: CompiledOntology) -> RuleCatalog:
    """Catalog compiled rule content; version IDs name the immutable ontology revision."""
    if not isinstance(compiled, CompiledOntology):
        raise ReasoningError("CompiledOntology required")
    return RuleCatalog(tuple(CatalogEntry(rule.rule_id, compiled.revision_id, Program([rule]).digest, 1)
                             for rule in compiled.program.rules))


@dataclass(frozen=True)
class ClaimGenerationBinding:
    """Validated source-policy metadata; it is not evidence that computation is sound."""

    identity: GenerationIdentity
    provenance: ReadViewProvenance
    selection: ClaimSelection
    mapping: ClaimMapping
    extraction_request: ExtractionRequest | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.identity, GenerationIdentity) or not isinstance(self.provenance, ReadViewProvenance)
                or not isinstance(self.selection, ClaimSelection) or not isinstance(self.mapping, ClaimMapping)):
            raise ReasoningError("invalid generation binding types")
        provenance = ReadViewProvenance.from_dict(self.provenance.to_dict())
        context = self.identity.context
        if self.identity.source != _source(provenance) or context != _context(
                provenance, context.world, context.ontology_revision, self.selection, self.mapping,
                self.extraction_request):
            raise ProvenanceMismatchError("generation source/context policy binding mismatch")
        object.__setattr__(self, "provenance", provenance)

    def verify_source(self, graph: GraphDB, *, freshness: str = "latest") -> None:
        """Authenticate a source prefix; latest additionally rejects any newer commit.

        This read-only check alone is not an atomic compare-and-publish permit.
        Publication must hold the shared writer guard through its final commit.
        """
        if freshness not in ("latest", "historical"):
            raise ReasoningError("freshness must be latest or historical")
        _verify_graph(self.provenance, graph)
        if freshness == "latest":
            with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=self.provenance.valid_time_us) as view:
                if view.provenance.commit_horizon != self.provenance.commit_horizon:
                    raise ProvenanceMismatchError("generation source horizon moved; rebind explicitly")


def bind_claim_generation(graph: GraphDB, result: ClaimReasoningResult, catalog: RuleCatalog, *,
                          dictionary_id: str = "external-strings-v1") -> ClaimGenerationBinding:
    """Bind a verified binary-ontology result and a content-compatible rule catalog."""
    if not isinstance(result, ClaimReasoningResult) or not isinstance(catalog, RuleCatalog):
        raise ReasoningError("ClaimReasoningResult and RuleCatalog required")
    result.verify_source(graph)
    expected = {entry.rule_key: entry.content_digest for entry in catalog_for_ontology(result.compiled).entries}
    if {entry.rule_key: entry.content_digest for entry in catalog.entries} != expected:
        raise ReasoningError("catalog content does not match the compiled ontology")
    identity = GenerationIdentity(_source(result.provenance), result.context, result.result.program_digest,
                                  catalog.digest, "positive-binary-entity-v1", dictionary_id)
    return ClaimGenerationBinding(identity, result.provenance, result.selection, result.mapping,
                                   result.extraction_request)
