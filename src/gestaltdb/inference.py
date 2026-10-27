"""Bounded audit export for persisted logical and model-output claims.

This explicit adapter consumes ``GraphDB.explain_claim``; it is not an
independent proof checker, a probability calculus, or a model explainer.
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, cast

from .rules import (
    ClaimExplanation,
    RuleAbsence,
    RuleEvaluationLimitError,
    RuleError,
    RuleJustification,
    RuleVersion,
    absence_node_id,
)
from .versioning import ClaimVersion, canonical_json_bytes


@dataclass(frozen=True)
class InferenceExplanation:
    """Detached canonical JSON audit artifact, not a trusted proof certificate.

    ``to_dict`` returns a fresh copy; mutating it cannot change the report.
    Model metadata is caller-asserted and confidence never means entailment.
    """

    _json: str

    def to_json(self) -> str:
        return self._json

    def to_dict(self) -> dict:
        return json.loads(self._json)

    def as_html(self) -> str:
        """Render an offline, script-free audit view with escaped content."""
        value = self.to_dict()
        status = "TRUNCATED — incomplete evidence" if value["truncated"] else "Recorded evidence"
        return (
            '<!doctype html><html lang="en"><meta charset="utf-8">'
            '<title>Inference audit</title><body><h1>Inference audit</h1>'
            f'<h2>{status}</h2><p>Model scores are asserted metadata, not '
            'logical probabilities. This export is not independent proof verification.</p>'
            f'<pre>{html.escape(json.dumps(value, indent=2, ensure_ascii=False))}</pre></body></html>'
        )


def _limit(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuleError(f"{name} must be a positive integer")


def export_inference(explanation: ClaimExplanation, *, max_edges: int = 100_000,
                     max_bytes: int = 10_000_000) -> InferenceExplanation:
    """Serialize exact historical versions and grouped alternative supports.

    Model-output claims opt in via ``provenance['model_output']`` containing
    non-empty ``model_id`` and ``model_version`` strings. No model association
    is guessed from a claim's confidence or agent. Incomplete supports are
    omitted and the report marked truncated; they are never represented as
    valid proofs. Edge/serialized-byte budget overflow raises, with no export.
    Shared rule nodes do not merge unrelated justification premise groups.
    """
    if not isinstance(explanation, ClaimExplanation):
        raise RuleError("ClaimExplanation required")
    _limit(max_edges, "max_edges")
    _limit(max_bytes, "max_bytes")
    nodes: list[dict[str, Any]] = []
    identities = {node.node_id for node in explanation.nodes}
    if len(identities) != len(explanation.nodes) or explanation.root_version_id not in identities:
        raise RuleError("explanation has duplicate nodes or a missing root")
    supports: list[dict[str, Any]] = []
    edge_count = 0
    truncated = explanation.truncated
    for node in sorted(explanation.nodes, key=lambda item: item.node_id):
        if node.kind == "rule":
            if not isinstance(node.value, RuleVersion) or node.value.version_id != node.node_id:
                raise RuleError("rule version identity mismatch")
            nodes.append({"id": node.node_id, "kind": "rule", "value": node.value.to_dict()})
            continue
        if node.kind == "absence":
            if not isinstance(node.value, RuleAbsence):
                raise RuleError("absence node requires recorded absence evidence")
            nodes.append({
                "id": node.node_id, "kind": "absence", "value": node.value.to_dict(),
            })
            continue
        if node.kind != "claim" or not isinstance(node.value, ClaimVersion) or node.value.claim is None:
            raise RuleError("explanation requires exact claim and rule versions")
        version = node.value
        if version.version_id != node.node_id:
            raise RuleError("claim version identity mismatch")
        claim = version.claim
        if claim is None:
            raise RuleError("explanation requires exact claim and rule versions")
        model = claim.provenance.get("model_output")
        if model is not None:
            if not isinstance(model, Mapping) or any(
                    not isinstance(model.get(key), str) or not model[key]
                    for key in ("model_id", "model_version")):
                raise RuleError("model_output requires explicit model_id and model_version")
        nodes.append({
            "id": node.node_id, "kind": "model_output" if model is not None else "claim",
            "claim": claim.to_dict(), "logical_id": version.logical_id,
            "commit_id": version.commit_id, "commit_ordinal": version.commit_ordinal,
            "system_time_us": version.system_time.epoch_microseconds,
            "valid_start_us": version.valid.start.epoch_microseconds,
            "valid_end_us": None if version.valid.end is None else version.valid.end.epoch_microseconds,
            "operation": version.operation.value, "payload_hash": version.payload_hash,
            "supersedes_version_id": version.supersedes_version_id, "reason": version.reason,
        })
        # Use grouped persisted justifications, not the flattened rule-node
        # edges: a rule version can support several conclusions independently.
        if claim.provenance.get("derived_by") != "gestaltdb.rules":
            continue
        for support in cast(Iterable[object], claim.provenance.get("justifications", ())):
            try:
                normalized = RuleJustification.from_dict(cast("Mapping[str, object]", support))
            except RuleError as exc:
                raise RuleError("invalid recorded inference justification") from exc
            rule_id = normalized.rule_version_id
            premises = list(normalized.premise_version_ids)
            absent = [item.to_dict() for item in normalized.absences]
            absence_ids = [absence_node_id(rule_id, item) for item in normalized.absences]
            if (
                rule_id not in identities
                or any(item not in identities for item in premises)
                or any(item not in identities for item in absence_ids)
            ):
                truncated = True
                continue
            edge_count += 1 + len(premises) + len(absent)
            if edge_count > max_edges:
                raise RuleEvaluationLimitError("inference export exceeded max_edges")
            value: dict[str, Any] = {
                "conclusion": node.node_id, "rule": rule_id, "premises": premises,
            }
            if absent:
                value["absences"] = absent
            supports.append(value)
    payload = {
        "format_version": 1, "kind": "gestaltdb.inference-audit",
        "claim_id": explanation.claim_id, "root_version_id": explanation.root_version_id,
        "valid_time_us": explanation.valid_time.epoch_microseconds,
        "system_time_us": explanation.system_time.epoch_microseconds,
        "truncated": truncated, "nodes": nodes,
        "justifications": sorted(supports, key=lambda item: (
            item["conclusion"], item["rule"], item["premises"],
            json.dumps(item.get("absences", ()), sort_keys=True))),
        "semantics": (
            "Recorded historical evidence; absence nodes are closed-world checks at the "
            "evaluation horizon, not negative claims. Model metadata is not logical "
            "probability or independent proof verification."
        ),
    }
    encoded = canonical_json_bytes(payload)
    if len(encoded) > max_bytes:
        raise RuleEvaluationLimitError("inference export exceeded max_bytes")
    return InferenceExplanation(encoded.decode("utf-8"))


def explain_inference(graph, claim_id, *, valid_time, system_time=None,
                      max_depth=100, max_nodes=10_000, max_edges=100_000,
                      max_bytes=10_000_000) -> InferenceExplanation | None:
    """Read-only convenience adapter; absent/retracted roots return ``None``.

    Depth/node bounds use the existing historical explanation traversal.
    Serialization edge/byte bounds are additional, not extraction budgets.
    No graph mutation, model execution, or external network request occurs.
    """
    _limit(max_edges, "max_edges")
    _limit(max_bytes, "max_bytes")
    explanation = graph.rules.explain(claim_id, valid_time=valid_time,
                                     system_time=system_time, max_depth=max_depth,
                                     max_nodes=max_nodes)
    return None if explanation is None else export_inference(
        explanation, max_edges=max_edges, max_bytes=max_bytes)
