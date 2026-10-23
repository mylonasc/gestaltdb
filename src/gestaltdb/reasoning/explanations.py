"""Bounded minimal/top-k proof contracts, export and generation checkpoints.

A proof here is a chosen witness, never the whole support set: ``minimal_witness``
picks the fewest-premise single derivation deterministically and ``top_k``
ranks derivations by premise count then digest. Deep-tree minimality is
combinatorial and explicitly deferred — the contract covers one-step
witnesses with exact bounds, and ``export_proof`` always labels which kind
of witness it carries alongside the complete alternative supports, so a
chosen witness is never confused with all supports.

``checkpoint_closure`` persists only authenticated generations — program
digest, fact horizon, support evidence and the index digest bound
together. ``resume_checkpoint`` rebuilds and re-validates instead of
trusting stored derivations; incompatible generations fail closed and
incomplete checkpoints are discarded explicitly, never silently repaired.
Ranked probabilistic provenance stays out: exports carry no scores or
confidences, only deterministic evidence (see the separately gated
confidence calculi).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .maintenance import DependencyIndex, Support, build_index
from .model import ReasoningError, ReasoningLimitError, _text
from .terms import Literal
from .typed import GenericProgram, TypedFact, _digest_facts, _sort_key


@dataclass(frozen=True)
class RankedWitness:
    """One chosen derivation with its deterministic rank and total."""

    fact: TypedFact
    rule_id: str
    premises: tuple[TypedFact, ...]
    rank: int
    total: int
    kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.fact, TypedFact):
            raise ReasoningError("fact must be a TypedFact")
        _text(self.rule_id, "rule_id")
        if not isinstance(self.premises, (tuple, list)) or not all(isinstance(item, TypedFact) for item in self.premises):
            raise ReasoningError("premises must contain only TypedFact values")
        object.__setattr__(self, "premises", tuple(sorted(set(self.premises), key=_sort_key)))
        for field in ("rank", "total"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ReasoningError(f"{field} must be a positive integer")
        if self.rank > self.total:
            raise ReasoningError("rank must not exceed total")
        if self.kind not in ("chosen-minimal", "chosen-top-k"):
            raise ReasoningError("kind must be chosen-minimal or chosen-top-k")

    @property
    def digest(self) -> str:
        import hashlib
        import json

        from .typed import _encoded_term

        return "sha256:" + hashlib.sha256(json.dumps(
            ["gestaltdb-ranked-witness-v1", self.kind, self.rule_id, self.rank, self.total,
             [self.fact.predicate, [_encoded_term(value) for value in self.fact.values]],
             sorted([item.predicate, [_encoded_term(value) for value in item.values]] for item in self.premises)],
            ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _ordered(supports: Iterable[Support]) -> list[Support]:
    try:
        items = [item for item in list(supports) if isinstance(item, Support)]
    except TypeError as exc:
        raise ReasoningError("supports must be iterable") from exc
    if len(items) != len(list(supports)):
        raise ReasoningError("supports must contain only Support values")
    return sorted(items, key=lambda item: (len(item.premises), item.rule_id, item.digest))


def minimal_witness(fact: TypedFact, supports: Iterable[Support]) -> RankedWitness:
    """Fewest-premise single derivation, deterministic on ties."""
    if not isinstance(fact, TypedFact):
        raise ReasoningError("fact must be a TypedFact")
    ranked = _ordered([item for item in supports if item.fact == fact])
    if not ranked:
        raise ReasoningError(f"no support justifies {fact!r}; mind the gap, do not invent one")
    first = ranked[0]
    return RankedWitness(fact, first.rule_id, first.premises, 1, len(ranked), "chosen-minimal")


def top_k(fact: TypedFact, supports: Iterable[Support], k: int) -> tuple[RankedWitness, ...]:
    """Deterministic top-k derivations by premise count then digest."""
    if not isinstance(fact, TypedFact):
        raise ReasoningError("fact must be a TypedFact")
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ReasoningError("k must be a positive integer")
    ranked = _ordered([item for item in supports if item.fact == fact])
    if not ranked:
        raise ReasoningError(f"no support justifies {fact!r}; mind the gap, do not invent one")
    return tuple(RankedWitness(fact, item.rule_id, item.premises, position, len(ranked), "chosen-top-k")
                 for position, item in enumerate(ranked[:k], 1))


def export_proof(fact: TypedFact, supports: Iterable[Support], witness: RankedWitness, *,
                 max_proof_bytes: int = 1_000_000) -> dict[str, object]:
    """Stable model-output interchange: chosen witness plus all supports.

    The ``witness_kind`` field keeps the chosen derivation distinct from
    ``all_supports``; consumers must not read the witness as exhaustive.
    No scores, confidences or ranks beyond the deterministic order travel
    here — ranked probabilistic provenance is a separately gated calculus.
    """
    if not isinstance(fact, TypedFact):
        raise ReasoningError("fact must be a TypedFact")
    if not isinstance(witness, RankedWitness):
        raise ReasoningError("witness must be a RankedWitness")
    if isinstance(max_proof_bytes, bool) or not isinstance(max_proof_bytes, int) or max_proof_bytes < 1:
        raise ReasoningError("max_proof_bytes must be a positive integer")
    if witness.fact != fact:
        raise ReasoningError("witness must justify the exported fact")
    try:
        items = list(supports)
    except TypeError as exc:
        raise ReasoningError("supports must be iterable") from exc
    if not all(isinstance(item, Support) for item in items):
        raise ReasoningError("supports must contain only Support values")
    if not any(item.fact == fact and item.rule_id == witness.rule_id
               and set(item.premises) == set(witness.premises) for item in items):
        raise ReasoningError("witness must come from the exported support set")
    from .typed import _encoded_term

    def encoded(fact_value: TypedFact) -> list:
        return [fact_value.predicate, [_encoded_term(value) for value in fact_value.values]]

    document = {"format": "gestaltdb-proof-v1", "witness_kind": witness.kind,
                "fact": encoded(fact),
                "witness": {"rule": witness.rule_id, "rank": witness.rank, "total": witness.total,
                            "digest": witness.digest,
                            "premises": [encoded(item) for item in witness.premises]},
                "all_supports": sorted(
                    [{"rule": item.rule_id,
                      "premises": [encoded(premise) for premise in item.premises]}
                     for item in items if item.fact == fact],
                    key=lambda entry: (len(entry["premises"]), entry["rule"]))}
    import json

    encoded_bytes = len(json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
    if encoded_bytes > max_proof_bytes:
        raise ReasoningLimitError(f"proof export exceeded max_proof_bytes={max_proof_bytes}")
    return document


CHECKPOINT_FORMAT_VERSION = 2


def _encode_fact(fact: TypedFact) -> list:
    from .typed import _encoded_term

    return [fact.predicate, [_encoded_term(value) for value in fact.values]]


def _decode_fact(payload: object) -> TypedFact:
    if not isinstance(payload, (tuple, list)) or len(payload) != 2:
        raise ReasoningError("fact payload must be a [predicate, values] pair")
    predicate, values = payload
    _text(predicate, "predicate")
    if not isinstance(values, (tuple, list)):
        raise ReasoningError("fact values must be a list")
    decoded = []
    for value in values:
        if not isinstance(value, (tuple, list)) or not value:
            raise ReasoningError("fact values must be encoded [kind, ...] pairs")
        if value[0] == "entity" and len(value) == 2:
            _text(value[1], "entity")
            decoded.append(value[1])
        elif value[0] == "literal" and len(value) == 3:
            decoded.append(Literal.from_lexical(value[1], value[2]))
        else:
            raise ReasoningError("fact values must be [entity, value] or [literal, kind, value] triples")
    return TypedFact(predicate, decoded)


def checkpoint_closure(program: GenericProgram, facts: Iterable[TypedFact],
                       supports: Iterable[Support]) -> dict[str, object]:
    """Persist an authenticated generation: program, horizon, supports, index."""
    if not isinstance(program, GenericProgram):
        raise ReasoningError("program must be a GenericProgram")
    try:
        raw = list(facts)
    except TypeError as exc:
        raise ReasoningError("facts must be iterable") from exc
    if not all(isinstance(item, TypedFact) for item in raw):
        raise ReasoningError("facts must contain only TypedFact values")
    known = sorted(set(raw), key=_sort_key)
    try:
        items = list(supports)
    except TypeError as exc:
        raise ReasoningError("supports must be iterable") from exc
    if not all(isinstance(item, Support) for item in items):
        raise ReasoningError("supports must contain only Support values")
    index = build_index(program, known, items)
    return {"format_version": CHECKPOINT_FORMAT_VERSION,
            "program_digest": program.digest,
            "horizon": _digest_facts(known),
            "facts": [_encode_fact(item) for item in known],
            "supports": [{"fact": _encode_fact(item.fact), "rule": item.rule_id,
                          "premises": [_encode_fact(premise) for premise in item.premises]}
                         for item in sorted(set(items),
                                             key=lambda item: (item.rule_id, _sort_key(item.fact)))],
            "index_digest": index.digest}


def is_complete_checkpoint(value: object) -> bool:
    """Schema plus self-consistency check, without trusting any generation."""
    if not isinstance(value, dict):
        return False
    try:
        if value.get("format_version") != CHECKPOINT_FORMAT_VERSION:
            return False
        facts = [_decode_fact(item) for item in value["facts"]]
        if _digest_facts(sorted(set(facts), key=_sort_key)) != value.get("horizon"):
            return False
        supports = [Support(_decode_fact(item["fact"]), str(item["rule"]),
                            tuple(_decode_fact(premise) for premise in item["premises"]))
                    for item in value["supports"]]
        rebuilt = DependencyIndex(str(value.get("program_digest", "")), str(value.get("horizon", "")),
                                  tuple(supports))
        return rebuilt.digest == value.get("index_digest")
    except (ReasoningError, KeyError, TypeError, ValueError):
        return False


def resume_checkpoint(value: object, *, program: GenericProgram) -> tuple[tuple[TypedFact, ...], DependencyIndex]:
    """Rebuild and re-validate a checkpoint instead of trusting it.

    Incompatible program digests fail closed; incomplete checkpoints fail
    closed naming the defect (discard them explicitly with
    ``discard_checkpoint``). Supports are re-validated through
    ``build_index`` against the live program, never installed blindly.
    """
    if not isinstance(program, GenericProgram):
        raise ReasoningError("program must be a GenericProgram")
    if not isinstance(value, dict) or value.get("format_version") != CHECKPOINT_FORMAT_VERSION:
        raise ReasoningError("incomplete checkpoint: unsupported or missing format_version")
    try:
        facts = sorted({_decode_fact(item) for item in value["facts"]}, key=_sort_key)
        supports = [Support(_decode_fact(item["fact"]), str(item["rule"]),
                            tuple(_decode_fact(premise) for premise in item["premises"]))
                    for item in value["supports"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise ReasoningError(f"incomplete checkpoint: {exc}") from exc
    if not isinstance(value.get("program_digest"), str) or value["program_digest"] != program.digest:
        raise ReasoningError("incompatible checkpoint: program digest drifted; rematerialize explicitly")
    if _digest_facts(facts) != value.get("horizon"):
        raise ReasoningError("incomplete checkpoint: horizon does not cover the stored facts")
    index = build_index(program, facts, supports)
    if index.digest != value.get("index_digest"):
        raise ReasoningError("incomplete checkpoint: index digest mismatch; discard explicitly")
    return tuple(facts), index


def discard_checkpoint(value: object, *, consent: bool = False) -> bool:
    """Discard only incomplete checkpoints, and only with explicit consent.

    Complete generations are never discarded here — archiving them is a
    separate, deliberate act. Without ``consent=True`` this fails closed
    without touching anything.
    """
    if consent is not True:
        raise ReasoningError("checkpoint discard requires explicit consent=True")
    if is_complete_checkpoint(value):
        raise ReasoningError("refusing to discard a complete authenticated generation")
    return True
