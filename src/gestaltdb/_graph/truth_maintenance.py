"""Incremental truth maintenance and derivation explanations (Phase 6, part 2).

``maintain_truth`` revalidates derived claims against the current fixpoint
after base evidence changes, retracting a conclusion only when its final
independent support disappears. ``explain_claim`` builds the bounded
derivation graph. Both consume one temporal engine, so selection, commits
and state stay scope-consistent by construction.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ..temporal_engine import TemporalEngine
from ..epistemic import Claim, ClaimObjectKind, ClaimPolarity
from ..ingestion import IndexMaintenanceMode
from ..rules import (
    ClaimExplanation,
    ExplanationEdge,
    ExplanationNode,
    RuleEvaluationLimitError,
    RuleJustification,
    RuleAbsence,
    RuleVersion,
    TruthMaintenanceResult,
    absence_node_id,
)
from ..temporal import TemporalInstant, TemporalInterval
from ..versioning import (
    ClaimVersion,
    ClaimVersionWrite,
    TemporalCorruptionError,
    canonical_json_bytes,
    normalize_temporal_instant,
    normalize_version_id,
)
from .stored_rules import (
    RULE_ENGINE_AGENT,
    absence_checks,
    iter_rule_versions,
    justification_order,
    rule_justifications,
    rule_program,
    rule_scope,
)
from .temporal_selection import _UNSET
from .temporal_types import PointInput
from .stored_rule_types import ConclusionKey, EncodedJustification, JoinRow, Justification, MaintenanceFact, MaintenanceOutput, SupportReference

TRUTH_MAINTENANCE_STATE_KEY = b"rules:truth-maintenance:v1"


def last_truth_maintenance_instant(temporal: TemporalEngine, state_key: bytes) -> TemporalInstant | None:
    """Return the valid time of the most recent rule run or maintenance pass."""
    store, reader = temporal.store, temporal.reader()
    payload = store.get_metadata(state_key)
    state_instant = None
    state_horizon = -1
    if payload is not None:
        try:
            state = json.loads(payload.decode("utf-8"))
            if state.get("format_version") == 1:
                state_instant = TemporalInstant(state["valid_time_us"])
                state_horizon = state["through_commit"]
                if isinstance(state_horizon, bool) or not isinstance(state_horizon, int):
                    raise TypeError
        except (AttributeError, KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            state_instant = None
            state_horizon = -1
    last_commit_id, _ = temporal.sequence()
    for commit_id in range(last_commit_id, 0, -1):
        commit = temporal.reader().get_commit(commit_id)
        if commit is None:
            continue
        for name in ("truth_maintenance", "rule_run"):
            value = commit.metadata.get(name)
            if isinstance(value, Mapping) and isinstance(value.get("as_of_us"), int):
                if commit_id > state_horizon:
                    return TemporalInstant(value["as_of_us"])
                return state_instant
    return state_instant


def maintain_truth(temporal: TemporalEngine, truth_state_key: bytes, *,
                 as_of: PointInput | None = None, system_time: PointInput | None = None, world: str | None = None,
                 max_iterations: int = 100, max_derivations: int = 10_000, max_justifications: int = 100_000,
                 index_mode: str | IndexMaintenanceMode = IndexMaintenanceMode.MAINTAIN) -> TruthMaintenanceResult:
    """Incrementally reconcile derived claims with the current rule fixpoint.

    The authoritative base excludes claims emitted by the rule engine, so a
    cycle cannot preserve itself after its final independent support is
    removed. Selection, guarded commits and state go through one temporal
    engine, so maintenance cannot mix scopes.
    """
    limits = {
        "max_iterations": max_iterations,
        "max_derivations": max_derivations,
        "max_justifications": max_justifications,
    }
    for name, value in limits.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if world is not None and (not isinstance(world, str) or not world):
        raise ValueError("world must be a non-empty string or None")
    mode = index_mode.value if isinstance(index_mode, IndexMaintenanceMode) else index_mode
    if mode not in {item.value for item in IndexMaintenanceMode}:
        raise ValueError("index_mode must be a valid IndexMaintenanceMode")
    instant = last_truth_maintenance_instant(temporal, truth_state_key) if as_of is None else normalize_temporal_instant(as_of)
    if instant is None:
        raise ValueError("as_of is required for the first truth-maintenance pass")

    with temporal.write_guard():
        input_horizon = temporal.system_horizon(system_time=system_time)
        rules, strata = rule_program(temporal.store, system_time=system_time)
        scope = rule_scope(rules)

        facts: list[MaintenanceFact] = []
        facts_by_predicate: dict[str, list[MaintenanceFact]] = {}
        existing: dict[ConclusionKey, ClaimVersion] = {}

        def conclusion_key(subject: str, predicate: str, object_value: object, claim_world: str) -> ConclusionKey:
            return subject, predicate, cast(str, object_value), claim_world

        def add_fact(reference: SupportReference, subject: str, predicate: str, object_value: object, claim_world: str, valid: TemporalInterval) -> MaintenanceFact:
            fact: MaintenanceFact = {
                "ref": reference,
                "subject": subject,
                "predicate": predicate,
                "object": cast(str, object_value),
                "world": claim_world,
                "valid": valid,
            }
            facts.append(fact)
            facts_by_predicate.setdefault(predicate, []).append(fact)
            return fact

        for version in temporal.history().iter_claims_as_of(
            valid_time=instant,
            polarity=ClaimPolarity.POSITIVE,
            world=world,
            through_commit=input_horizon,
        ):
            claim = version.claim
            if claim is None or claim.object_kind is not ClaimObjectKind.ENTITY:
                continue
            key = conclusion_key(claim.subject, claim.predicate, claim.object, claim.world)
            if claim.agent == RULE_ENGINE_AGENT and claim.provenance.get("derived_by") == "gestaltdb.rules":
                existing[key] = version
                continue
            add_fact(
                ("version", version.version_id),
                claim.subject,
                claim.predicate,
                claim.object,
                claim.world,
                version.valid,
            )

        def union_validity(left: TemporalInterval, right: TemporalInterval) -> TemporalInterval:
            start = min(left.start, right.start)
            end = None if left.end is None or right.end is None else max(left.end, right.end)
            return TemporalInterval(start, end)

        def unify(term: str, value: str, bindings: dict[str, str]) -> dict[str, str] | None:
            if not term.startswith("?"):
                return bindings if term == value else None
            current = bindings.get(term, _UNSET)
            if current is not _UNSET:
                return bindings if current == value else None
            updated = dict(bindings)
            updated[term] = value
            return updated

        outputs: dict[ConclusionKey, MaintenanceOutput] = {}
        iterations = 0
        support_count = 0
        for stratum in strata:
            stratum_rules = tuple(
                rule for rule in rules if rule.then.predicate in stratum
            )
            if not stratum_rules:
                continue
            delta = {fact["ref"] for fact in facts}
            while delta:
                if iterations >= max_iterations:
                    raise RuleEvaluationLimitError(
                        f"truth maintenance exceeded max_iterations={max_iterations}"
                    )
                next_delta: dict[ConclusionKey, MaintenanceOutput] = {}
                for rule in stratum_rules:
                    rows: list[JoinRow[SupportReference]] = [({}, (), None, None, False)]
                    for atom in rule.when:
                        joined: list[JoinRow[SupportReference]] = []
                        for bindings, premises, valid, row_world, used_delta in rows:
                            for fact in facts_by_predicate.get(atom.predicate, ()):
                                if row_world is not None and fact["world"] != row_world:
                                    continue
                                subject_bindings = unify(atom.subject, fact["subject"], bindings)
                                if subject_bindings is None:
                                    continue
                                bound = unify(atom.object, fact["object"], subject_bindings)
                                if bound is None:
                                    continue
                                intersection = fact["valid"] if valid is None else valid.intersection(fact["valid"])
                                if intersection is None:
                                    continue
                                joined.append((
                                    bound,
                                    (*premises, fact["ref"]),
                                    intersection,
                                    fact["world"],
                                    used_delta or fact["ref"] in delta,
                                ))
                        rows = joined
                        if not rows:
                            break
                    for bindings, premises, valid, row_world, used_delta in rows:
                        if not used_delta:
                            continue
                        valid, row_world = cast(TemporalInterval, valid), cast(str, row_world)
                        absences = absence_checks(
                            rule, bindings, valid, row_world, facts_by_predicate, scope
                        )
                        if absences is None:
                            continue
                        subject = bindings.get(rule.then.subject, rule.then.subject)
                        object_value = bindings.get(rule.then.object, rule.then.object)
                        key = conclusion_key(subject, rule.then.predicate, object_value, row_world)
                        output = outputs.get(key)
                        if output is None:
                            if len(outputs) >= max_derivations:
                                raise RuleEvaluationLimitError(
                                    f"truth maintenance exceeded max_derivations={max_derivations}"
                                )
                            output = {
                                "subject": subject,
                                "predicate": rule.then.predicate,
                                "object": object_value,
                                "world": row_world,
                                "valid": valid,
                                "supports": set(),
                                "fact": None,
                            }
                            outputs[key] = output
                            next_delta[key] = output
                        merged = union_validity(output["valid"], valid)
                        if merged != output["valid"]:
                            output["valid"] = merged
                            next_delta[key] = output
                        support = (rule.version_id, premises, absences)
                        if support not in output["supports"]:
                            support_count += 1
                            if support_count > max_justifications:
                                raise RuleEvaluationLimitError(
                                    f"truth maintenance exceeded max_justifications={max_justifications}"
                                )
                            output["supports"].add(support)
                iterations += 1
                added = []
                for key, output in next_delta.items():
                    output_fact = output["fact"]
                    if output_fact is None:
                        output_fact = add_fact(
                            ("derived", key),
                            output["subject"],
                            output["predicate"],
                            output["object"],
                            output["world"],
                            output["valid"],
                        )
                        output["fact"] = output_fact
                    else:
                        output_fact["valid"] = output["valid"]
                    added.append(output_fact)
                delta = {fact["ref"] for fact in added}

        def semantic_premise(version_id: str) -> SupportReference:
            premise = temporal.history().get_claim_version(version_id, through_commit=input_horizon)
            if (
                premise is not None
                and premise.claim is not None
                and premise.claim.agent == RULE_ENGINE_AGENT
                and premise.claim.provenance.get("derived_by") == "gestaltdb.rules"
            ):
                claim = premise.claim
                return ("derived", conclusion_key(
                    claim.subject, claim.predicate, claim.object, claim.world
                ))
            return ("version", version_id)

        def existing_semantic_supports(version: ClaimVersion) -> set[Justification[SupportReference]]:
            return {
                (
                    rule_id,
                    tuple(semantic_premise(premise) for premise in premises),
                    absences,
                )
                for rule_id, premises, absences in rule_justifications(
                    cast(Claim, version.claim).provenance
                )
            }

        changed = set()
        for key, output in outputs.items():
            current = existing.get(key)
            if (
                current is None
                or current.valid != output["valid"]
                or existing_semantic_supports(current) != output["supports"]
            ):
                changed.add(key)
        propagated = True
        while propagated:
            propagated = False
            for key, output in outputs.items():
                if key in changed:
                    continue
                if any(
                    reference[0] == "derived" and reference[1] in changed
                    for _, premises, _ in output["supports"]
                    for reference in premises
                ):
                    changed.add(key)
                    propagated = True

        result_ids = {}
        for key in outputs:
            current = existing.get(key)
            result_ids[key] = (
                normalize_version_id()
                if current is None or key in changed
                else current.version_id
            )

        def encoded_supports(output: MaintenanceOutput) -> list[EncodedJustification]:
            encoded = []
            for rule_id, premises, absences in sorted(
                output["supports"], key=justification_order
            ):
                premise_ids = tuple(
                    reference[1]
                    if reference[0] == "version"
                    else result_ids[reference[1]] for reference in premises
                )
                encoded.append(
                    cast(EncodedJustification, RuleJustification(rule_id, premise_ids, absences).to_dict())
                )
            return encoded

        writes = []
        operations = []
        for key in sorted(outputs):
            if key not in changed:
                continue
            output = outputs[key]
            claim = Claim(
                output["subject"],
                output["predicate"],
                output["object"],
                ClaimPolarity.POSITIVE,
                RULE_ENGINE_AGENT,
                world=output["world"],
                provenance={
                    "derived_by": "gestaltdb.rules",
                    "justifications": encoded_supports(output),
                },
            )
            current = existing.get(key)
            if current is None:
                writes.append(ClaimVersionWrite.assertion(
                    claim, output["valid"], version_id=result_ids[key]
                ))
                operations.append("assert")
            else:
                writes.append(ClaimVersionWrite.correction(
                    claim,
                    supersedes_version_id=current.version_id,
                    valid=output["valid"],
                    version_id=result_ids[key],
                ))
                operations.append("correct")
                if current.valid.start < output["valid"].start:
                    writes.append(ClaimVersionWrite.retraction(
                        current.logical_id,
                        supersedes_version_id=current.version_id,
                        valid=TemporalInterval(
                            current.valid.start, output["valid"].start
                        ),
                        reason="derived validity no longer supported",
                    ))
                if output["valid"].end is not None and (
                    current.valid.end is None
                    or current.valid.end > output["valid"].end
                ):
                    writes.append(ClaimVersionWrite.retraction(
                        current.logical_id,
                        supersedes_version_id=current.version_id,
                        valid=TemporalInterval(
                            output["valid"].end, current.valid.end
                        ),
                        reason="derived validity no longer supported",
                    ))
        for key in sorted(set(existing) - set(outputs)):
            current = existing[key]
            writes.append(ClaimVersionWrite.retraction(
                current.logical_id,
                supersedes_version_id=current.version_id,
                valid=current.valid,
                reason="final rule support disappeared",
            ))
            operations.append("retract")

        commit = None
        if writes:
            commit = temporal.commit_versions(
                writes,
                metadata={
                    "truth_maintenance": {
                        "as_of_us": instant.epoch_microseconds,
                        "input_through_commit": input_horizon,
                        "iterations": iterations,
                    }
                },
                index_mode=index_mode,
            )

        premise_dependents: dict[str, set[str]] = {}
        rule_dependents: dict[str, set[str]] = {}
        derived = {}
        for key, output in outputs.items():
            claim = Claim(
                output["subject"], output["predicate"], output["object"],
                ClaimPolarity.POSITIVE, RULE_ENGINE_AGENT, world=output["world"],
            )
            derived[claim.claim_id] = {
                "version_id": result_ids[key],
                "support_count": len(output["supports"]),
            }
            for justification in encoded_supports(output):
                rule_dependents.setdefault(justification["rule_version_id"], set()).add(claim.claim_id)
                for premise_id in justification["premise_version_ids"]:
                    premise_dependents.setdefault(premise_id, set()).add(claim.claim_id)
        state_horizon = commit.commit_id if commit is not None else input_horizon
        temporal.store.put_metadata(truth_state_key, canonical_json_bytes({
            "format_version": 1,
            "valid_time_us": instant.epoch_microseconds,
            "through_commit": state_horizon,
            "derived": derived,
            "premise_dependents": {
                key: sorted(value) for key, value in sorted(premise_dependents.items())
            },
            "rule_dependents": {
                key: sorted(value) for key, value in sorted(rule_dependents.items())
            },
        }))
        versions = () if commit is None else commit.versions
        return TruthMaintenanceResult(
            input_horizon,
            None if commit is None else commit.commit_id,
            operations.count("assert"),
            operations.count("correct"),
            operations.count("retract"),
            support_count,
            versions,
        )


def explain_claim(temporal: TemporalEngine, *, claim_id: str, valid_time: PointInput,
                system_time: PointInput | None = None, max_depth: int = 100, max_nodes: int = 10_000) -> ClaimExplanation | None:
    """Return a finite derivation graph for a visible historical claim."""
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 0:
        raise ValueError("max_depth must be a non-negative integer")
    if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes < 1:
        raise ValueError("max_nodes must be a positive integer")
    instant = normalize_temporal_instant(valid_time)
    horizon = temporal.system_horizon(system_time=system_time)
    root = temporal.entity_as_of("claim", claim_id, valid_time=instant, through_commit=horizon)
    if root is None:
        return None
    commit = temporal.get_temporal_commit(horizon)
    if commit is None:
        raise TemporalCorruptionError("explanation horizon has no visible commit")
    rules = {rule.version_id: rule for rule in iter_rule_versions(temporal.store)}
    nodes: dict[str, ExplanationNode] = {}
    edges: set[ExplanationEdge] = set()
    expanded: set[str] = set()
    truncated = False

    def add_node(node_id: str, kind: str, value: ClaimVersion | RuleVersion | RuleAbsence) -> bool:
        nonlocal truncated
        if node_id in nodes:
            return True
        if len(nodes) >= max_nodes:
            truncated = True
            return False
        nodes[node_id] = ExplanationNode(node_id, kind, value)
        return True

    def visit(version: ClaimVersion, depth: int) -> None:
        nonlocal truncated
        if not add_node(version.version_id, "claim", version):
            return
        if version.version_id in expanded:
            return
        expanded.add(version.version_id)
        supports = rule_justifications(cast(Claim, version.claim).provenance)
        if not supports:
            return
        if depth >= max_depth:
            truncated = True
            return
        for rule_id, premise_ids, absences in sorted(
            supports, key=justification_order
        ):
            rule = rules.get(rule_id)
            if rule is None or not add_node(rule_id, "rule", rule):
                truncated = True
                continue
            edges.add(ExplanationEdge(version.version_id, rule_id, "derived_by"))
            for ordinal, premise_id in enumerate(premise_ids):
                premise = temporal.history().get_claim_version(premise_id, through_commit=horizon)
                if premise is None:
                    truncated = True
                    continue
                if not add_node(premise_id, "claim", premise):
                    continue
                edges.add(ExplanationEdge(rule_id, premise_id, "premise", ordinal))
                visit(premise, depth + 1)
            for absence in absences:
                absence_id = absence_node_id(rule_id, absence)
                if not add_node(absence_id, "absence", absence):
                    continue
                edges.add(ExplanationEdge(
                    rule_id, absence_id, "absent_premise", absence.position
                ))

    visit(root, 0)
    ordered_nodes = tuple(nodes[key] for key in sorted(nodes))
    ordered_edges = tuple(sorted(
        edges,
        key=lambda edge: (
            edge.source_id,
            edge.target_id,
            edge.relationship,
            -1 if edge.premise_ordinal is None else edge.premise_ordinal,
        ),
    ))
    return ClaimExplanation(
        root.logical_id,
        root.version_id,
        instant,
        commit.system_time,
        ordered_nodes,
        ordered_edges,
        truncated,
    )
