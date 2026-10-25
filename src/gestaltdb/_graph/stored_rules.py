"""Stored Horn rules and fixpoint evaluation (Phase 6 extraction, part 1).

``GraphDB.create_rule`` versions validated safe Horn rules into a catalog;
``run_rules`` evaluates the active program semi-naively over positive
entity claims in one world and persists deduplicated conclusions with
positive premise and absence evidence.

Evaluation consumes a temporal engine: history selection, guarded commits
and the live-store tripwire stay scope-consistent by construction.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..kvstores import KVStore

from ..epistemic import Claim, ClaimObjectKind, ClaimPolarity
from ..ingestion import IndexMaintenanceMode
from ..rules import (
    ClaimExplanation,
    ExplanationEdge,
    ExplanationNode,
    RuleAbsence,
    RuleError,
    RuleEvaluationLimitError,
    RuleJustification,
    RuleRunResult,
    RuleVersion,
    TruthMaintenanceResult,
    absence_node_id,
    normalize_rule_definition,
    stratify_rules,
)
from ..temporal import TemporalInstant, TemporalInterval
from ..versioning import (
    TemporalCorruptionError,
    ClaimVersion,
    ClaimVersionWrite,
    NodeVersion,
    canonical_json_bytes,
    normalize_temporal_instant,
    normalize_version_id,
)
from .temporal_selection import _UNSET

RULE_CATALOG_KEY = b"rules:catalog:v1"
RULE_ENGINE_AGENT = "gestaltdb:rules"
TRUTH_MAINTENANCE_STATE_KEY = b"rules:truth-maintenance:v1"


def load_rule_catalog(store: KVStore) -> tuple[RuleVersion, ...]:
    """Load validated rule versions in catalog order."""
    payload = store.get_metadata(RULE_CATALOG_KEY)
    if payload is None:
        return ()
    try:
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, dict) or decoded.get("format_version") != 1:
            raise TypeError
        versions = tuple(RuleVersion.from_dict(item) for item in decoded["versions"])
        if [item.catalog_ordinal for item in versions] != list(range(1, len(versions) + 1)):
            raise ValueError
        if len({item.version_id for item in versions}) != len(versions):
            raise ValueError
        return versions
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuleError("invalid rule catalog") from exc


def create_rule(temporal, rule_id, *, when, then,
                not_when=(), closed_world=(), version_id=None) -> RuleVersion:
    """Validate and append one immutable version of a Horn rule."""
    store = temporal.store
    with temporal.write_guard():
        catalog = load_rule_catalog(store)
        normalized_version_id = normalize_version_id(version_id)
        if any(item.version_id == normalized_version_id for item in catalog):
            raise RuleError(f"rule version ID already exists: {normalized_version_id}")
        previous_time = catalog[-1].system_time if catalog else None
        system_time = temporal.next_system_time(previous_time)
        rule = normalize_rule_definition(
            rule_id,
            when,
            then,
            version_id=normalized_version_id,
            system_time=system_time,
            catalog_ordinal=len(catalog) + 1,
            not_when=not_when,
            closed=closed_world,
        )
        active = {item.rule_id: item for item in catalog}
        active[rule.rule_id] = rule
        # Fail closed on negative cycles or undeclared absence before any
        # catalog write, so an unstratifiable program never becomes active.
        stratify_rules(tuple(active.values()))
        store.put_metadata(
            RULE_CATALOG_KEY,
            canonical_json_bytes({
                "format_version": 1,
                "versions": [item.to_dict() for item in (*catalog, rule)],
            }),
        )
        return rule


def iter_rule_versions(store: KVStore, rule_id=None, *, system_time=None):
    """Iterate immutable rule versions in catalog order at a system-time horizon."""
    if rule_id is not None and (not isinstance(rule_id, str) or not rule_id):
        raise RuleError("rule_id must be a non-empty string")
    horizon = None if system_time is None else normalize_temporal_instant(system_time)
    for rule in load_rule_catalog(store):
        if horizon is not None and rule.system_time > horizon:
            continue
        if rule_id is None or rule.rule_id == rule_id:
            yield rule


def get_rule(store: KVStore, rule_id, *, system_time=None) -> RuleVersion | None:
    """Return the latest version of a named rule at a system-time horizon."""
    versions = tuple(iter_rule_versions(store, rule_id, system_time=system_time))
    return versions[-1] if versions else None


def rule_justifications(provenance) -> set:
    """Extract stored derivation justifications from claim provenance."""
    if not isinstance(provenance, Mapping) or provenance.get("derived_by") != "gestaltdb.rules":
        return set()
    result = set()
    values = provenance.get("justifications", ())
    if not isinstance(values, (tuple, list)):
        return result
    for value in values:
        if not isinstance(value, Mapping):
            continue
        try:
            justification = RuleJustification.from_dict(value)
        except RuleError:
            continue
        result.add((
            justification.rule_version_id,
            justification.premise_version_ids,
            justification.absences,
        ))
    return result


def justification_order(justification):
    """Deterministic sort key for derivation justifications."""
    rule_version_id, premise_ids, absences = justification
    return (
        rule_version_id,
        premise_ids,
        tuple(
            (
                item.position,
                item.predicate,
                item.subject,
                item.object,
                item.scope,
            )
            for item in absences
        ),
    )


def rule_program(store: KVStore, *, system_time):
    """Return active rule versions and their stratification schedule."""
    by_id = {}
    for rule in iter_rule_versions(store, system_time=system_time):
        by_id[rule.rule_id] = rule
    rules = tuple(sorted(by_id.values(), key=lambda item: item.rule_id))
    return rules, stratify_rules(rules)


def rule_scope(rules) -> tuple[str, ...]:
    """Return the sorted closed-world predicate scope of a program."""
    return tuple(sorted({predicate for rule in rules for predicate in rule.closed}))


def absence_checks(rule, bindings, valid, world, facts_by_predicate, scope):
    """Return ground absence witnesses or ``None`` when negation is blocked.

    A negated atom is satisfied only when no matching positive claim
    overlaps the candidate conclusion's whole validity interval. Partial
    overlap blocks the derivation instead of producing an interval fragment
    that the claim model cannot represent.
    """
    checks = []
    for position, atom in enumerate(rule.not_when):
        subject = bindings.get(atom.subject, atom.subject)
        object_value = bindings.get(atom.object, atom.object)
        for fact in facts_by_predicate.get(atom.predicate, ()):
            if world is not None and fact["world"] != world:
                continue
            if fact["subject"] != subject or fact["object"] != object_value:
                continue
            if valid.intersection(fact["valid"]) is not None:
                return None
        checks.append(RuleAbsence(position, atom.predicate, subject, object_value, scope))
    return tuple(checks)


def run_rules(temporal, *,
              as_of, system_time=None, world=None,
              max_iterations=100, max_derivations=10_000, max_justifications=100_000,
              index_mode=IndexMaintenanceMode.MAINTAIN) -> RuleRunResult:
    """Evaluate active rules semi-naively and persist a bounded fixpoint.

    Positive components run in stratification order. Only positive
    entity-object claims participate; ``not_when`` atoms are closed-world
    absence checks against completed lower strata, not negative claims.
    History selection and guarded commits go through one temporal engine,
    so evaluation cannot mix scopes.
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

    instant = normalize_temporal_instant(as_of)
    horizon = temporal.system_horizon(system_time=system_time)
    rules, strata = rule_program(temporal.store, system_time=system_time)
    if not rules:
        return RuleRunResult(0, 0, 0, None, ())
    scope = rule_scope(rules)

    facts = []
    facts_by_predicate = {}
    existing_derived = {}

    def conclusion_key(subject, predicate, object_value, claim_world):
        return subject, predicate, object_value, claim_world

    def union_validity(left, right):
        start = min(left.start, right.start)
        if left.end is None or right.end is None:
            end = None
        else:
            end = max(left.end, right.end)
        return TemporalInterval(start, end)

    def add_fact(version_id, claim, valid):
        fact = {
            "version_id": version_id,
            "subject": claim.subject,
            "predicate": claim.predicate,
            "object": claim.object,
            "world": claim.world,
            "valid": valid,
        }
        facts.append(fact)
        facts_by_predicate.setdefault(claim.predicate, []).append(fact)
        return fact

    has_negation = any(rule.not_when for rule in rules)
    for version in temporal.iter_claims_as_of(
        valid_time=instant,
        polarity=ClaimPolarity.POSITIVE,
        world=world,
        through_commit=horizon,
        live_store=lambda: temporal.store,
    ):
        claim = version.claim
        if claim is None or claim.object_kind is not ClaimObjectKind.ENTITY:
            continue
        engine_derived = (
            claim.agent == RULE_ENGINE_AGENT
            and claim.provenance.get("derived_by") == "gestaltdb.rules"
        )
        if engine_derived:
            key = conclusion_key(
                claim.subject, claim.predicate, claim.object, claim.world
            )
            # Negation needs an authoritative base: a stale conclusion from
            # an earlier run must not block or satisfy an absence check.
            # Positive-only programs keep their historical seed behaviour.
            if has_negation:
                existing_derived[key] = (version, None)
                continue
            existing_derived[key] = (
                version, add_fact(version.version_id, claim, version.valid)
            )
            continue
        add_fact(version.version_id, claim, version.valid)

    outputs = {}
    generated_justifications = set()
    generated_count = 0
    iterations = 0

    def unify(term, value, bindings):
        if not term.startswith("?"):
            return bindings if term == value else None
        current = bindings.get(term, _UNSET)
        if current is not _UNSET:
            return bindings if current == value else None
        updated = dict(bindings)
        updated[term] = value
        return updated

    for stratum in strata:
        stratum_rules = tuple(
            rule for rule in rules if rule.then.predicate in stratum
        )
        if not stratum_rules:
            continue
        # Lower strata are complete before this component starts. Seeding
        # each component's delta with the facts available at entry keeps
        # semi-naive evaluation local without re-reading unfinished state.
        delta_ids = {fact["version_id"] for fact in facts}
        while delta_ids:
            if iterations >= max_iterations:
                raise RuleEvaluationLimitError(
                    f"rule evaluation exceeded max_iterations={max_iterations}"
                )
            next_delta = {}
            for rule in stratum_rules:
                rows = [({}, (), None, None, False)]
                for atom in rule.when:
                    joined = []
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
                                (*premises, fact["version_id"]),
                                intersection,
                                fact["world"],
                                used_delta or fact["version_id"] in delta_ids,
                            ))
                    rows = joined
                    if not rows:
                        break
                for bindings, premises, valid, row_world, used_delta in rows:
                    if not used_delta:
                        continue
                    absences = absence_checks(
                        rule, bindings, valid, row_world, facts_by_predicate, scope
                    )
                    if absences is None:
                        continue
                    subject = bindings.get(rule.then.subject, rule.then.subject)
                    object_value = bindings.get(rule.then.object, rule.then.object)
                    key = conclusion_key(subject, rule.then.predicate, object_value, row_world)
                    justification = (rule.version_id, premises, absences)
                    marker = (key, justification)
                    output = outputs.get(key)
                    if output is None:
                        existing_entry = existing_derived.get(key)
                        existing = None if existing_entry is None else existing_entry[0]
                        version_id = (
                            existing.version_id if existing is not None else normalize_version_id()
                        )
                        output = {
                            "existing": existing,
                            "version_id": version_id,
                            "subject": subject,
                            "predicate": rule.then.predicate,
                            "object": object_value,
                            "world": row_world,
                            "valid": existing.valid if existing is not None else valid,
                            "fact": None if existing_entry is None else existing_entry[1],
                            "justifications": set(),
                        }
                        outputs[key] = output
                        if existing is None:
                            generated_count += 1
                            if generated_count > max_derivations:
                                raise RuleEvaluationLimitError(
                                    f"rule evaluation exceeded max_derivations={max_derivations}"
                                )
                            next_delta[key] = output
                        elif output["fact"] is None:
                            # An excluded prior conclusion becomes a fact
                            # again once re-derived, so higher strata see it
                            # even when its validity did not change.
                            next_delta[key] = output
                    merged_valid = union_validity(output["valid"], valid)
                    if merged_valid != output["valid"]:
                        output["valid"] = merged_valid
                        next_delta[key] = output
                    if marker in generated_justifications:
                        continue
                    generated_justifications.add(marker)
                    if len(generated_justifications) > max_justifications:
                        raise RuleEvaluationLimitError(
                            f"rule evaluation exceeded max_justifications={max_justifications}"
                        )
                    output["justifications"].add(justification)
            iterations += 1
            added_facts = []
            for output in next_delta.values():
                fact = output["fact"]
                if fact is None:
                    claim = Claim(
                        output["subject"], output["predicate"], output["object"],
                        ClaimPolarity.POSITIVE, RULE_ENGINE_AGENT,
                        world=output["world"],
                        provenance={"derived_by": "gestaltdb.rules"},
                    )
                    fact = add_fact(output["version_id"], claim, output["valid"])
                    output["fact"] = fact
                else:
                    fact["valid"] = output["valid"]
                added_facts.append(fact)
            delta_ids = {fact["version_id"] for fact in added_facts}

    writes = []
    output_order = sorted(outputs)
    for key in output_order:
        output = outputs[key]
        existing = output["existing"]
        justifications = set(output["justifications"])
        if existing is not None:
            justifications.update(rule_justifications(existing.claim.provenance))
        encoded_justifications = [
            RuleJustification(rule_version_id, premise_ids, absences).to_dict()
            for rule_version_id, premise_ids, absences in sorted(
                justifications, key=justification_order
            )
        ]
        if (
            existing is not None
            and output["valid"] == existing.valid
            and justifications == rule_justifications(existing.claim.provenance)
        ):
            continue
        claim = Claim(
            output["subject"],
            output["predicate"],
            output["object"],
            ClaimPolarity.POSITIVE,
            RULE_ENGINE_AGENT,
            world=output["world"],
            provenance={
                "derived_by": "gestaltdb.rules",
                "justifications": encoded_justifications,
            },
        )
        if existing is None:
            writes.append(ClaimVersionWrite.assertion(
                claim, output["valid"], version_id=output["version_id"]
            ))
        else:
            writes.append(ClaimVersionWrite.correction(
                claim,
                supersedes_version_id=existing.version_id,
                valid=output["valid"],
            ))

    if not writes:
        return RuleRunResult(iterations, 0, len(generated_justifications), None, ())
    commit = temporal.commit_versions(
        writes,
        metadata={
            "rule_run": {
                "as_of_us": instant.epoch_microseconds,
                "input_through_commit": horizon,
                "iterations": iterations,
            }
        },
        index_mode=index_mode,
    )
    return RuleRunResult(
        iterations,
        len(commit.versions),
        len(generated_justifications),
        commit.commit_id,
        commit.versions,
    )
