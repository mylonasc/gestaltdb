"""Bounded OWL EL classification feasibility: consequence-based, experimental.

This is a dedicated experimental profile (``el-experimental-v0``), not a
production classifier. It closes subsumption between named concepts with
completion rules over a finite vocabulary — no fresh individuals, no
anonymous nesting, no full OWL DL, no JVM. Every entry point demands the
exact profile name and fails closed otherwise.

Consequence-based classification is evaluated separately from generating
witnesses: ``classify`` returns the closure plus one derivation per
non-trivial conclusion with rule identity and exact tagged grounds, and
``check_proof`` replays those grounds in round order through an independent
checker. Termination is structural (finite vocabulary, monotone growth)
with strict derivation and round budgets on top. ``holds_in_all_models``
is the independent model-theoretic oracle: brute-force finite
interpretations that completion must agree with on satisfiable inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .model import ReasoningError, ReasoningLimitError, _text, check_cancelled

EL_PROFILE = "el-experimental-v0"


def _gate(profile: str) -> None:
    _text(profile, "profile")
    if profile != EL_PROFILE:
        raise ReasoningError(f"experimental EL profile required: {EL_PROFILE!r}")


def _concept(value: object, name: str) -> str:
    _text(value, name)
    return value  # type: ignore[return-value]


@dataclass(frozen=True)
class Subsumption:
    """Named ``sub ⊑ sup``."""

    sub: str
    sup: str

    def __post_init__(self) -> None:
        _concept(self.sub, "sub")
        _concept(self.sup, "sup")

    def canonical(self) -> tuple:
        return ("sub", self.sub, self.sup)


@dataclass(frozen=True)
class Conjunction:
    """Named ``members ⊑ sup`` with at least two distinct members."""

    members: tuple[str, ...]
    sup: str

    def __post_init__(self) -> None:
        if not isinstance(self.members, (tuple, list)) or not self.members:
            raise ReasoningError("conjunction members must be a non-empty tuple or list")
        for member in self.members:
            _concept(member, "member")
        _concept(self.sup, "sup")
        object.__setattr__(self, "members", tuple(sorted(set(self.members))))

    def canonical(self) -> tuple:
        return ("and", self.members, self.sup)


@dataclass(frozen=True)
class ExistentialRight:
    """Named ``sub ⊑ ∃role.filler``."""

    sub: str
    role: str
    filler: str

    def __post_init__(self) -> None:
        _concept(self.sub, "sub")
        _concept(self.role, "role")
        _concept(self.filler, "filler")

    def canonical(self) -> tuple:
        return ("some-right", self.sub, self.role, self.filler)


@dataclass(frozen=True)
class ExistentialLeft:
    """Named ``∃role.filler ⊑ sup``."""

    role: str
    filler: str
    sup: str

    def __post_init__(self) -> None:
        _concept(self.role, "role")
        _concept(self.filler, "filler")
        _concept(self.sup, "sup")

    def canonical(self) -> tuple:
        return ("some-left", self.role, self.filler, self.sup)


def normalize_axiom(spec: object, *, profile: str):
    """Accept exactly the four named EL forms; reject anonymous nesting.

    Accepted specs: ``("sub", C, D)``, ``("and", (C1, ...), D)``,
    ``("some-right", C, R, D)``, ``("some-left", R, D, C)``. Every term must
    be a named concept or role; nested tuples, empty members, unknown tags
    and non-string terms fail closed naming the restriction.
    """
    _gate(profile)
    if not isinstance(spec, (tuple, list)) or not spec:
        raise ReasoningError("axiom spec must be a non-empty tuple")
    tag, rest = spec[0], spec[1:]
    if tag == "sub" and len(rest) == 2 and all(isinstance(term, str) for term in rest):
        return Subsumption(rest[0], rest[1])
    if tag == "and" and len(rest) == 2 and isinstance(rest[0], (tuple, list)):
        members = rest[0]
        if members and all(isinstance(term, str) for term in members) and isinstance(rest[1], str):
            unique = tuple(sorted(set(members)))
            if len(unique) == 1:
                return Subsumption(unique[0], rest[1])
            if len(unique) >= 2:
                return Conjunction(unique, rest[1])
        raise ReasoningError("anonymous or empty conjunctions are rejected: name every conjunct")
    if tag == "some-right" and len(rest) == 3 and all(isinstance(term, str) for term in rest):
        return ExistentialRight(rest[0], rest[1], rest[2])
    if tag == "some-left" and len(rest) == 3 and all(isinstance(term, str) for term in rest):
        return ExistentialLeft(rest[0], rest[1], rest[2])
    if isinstance(tag, str) and any(isinstance(term, (tuple, list)) for term in rest):
        raise ReasoningError("anonymous nesting is rejected: existentials and conjunctions take named terms only")
    raise ReasoningError(f"unsupported EL axiom form: {tag!r}")


def _check_ground(ground: object) -> tuple:
    """Validate one tagged ground: sub/some/axiom with exact arity."""
    if not isinstance(ground, (tuple, list)) or not ground:
        raise ReasoningError("grounds must be non-empty tagged tuples")
    tag = ground[0]
    if tag == "sub" and len(ground) == 3 and all(isinstance(term, str) and term for term in ground[1:]):
        return ("sub", ground[1], ground[2])
    if tag == "some" and len(ground) == 4 and all(isinstance(term, str) and term for term in ground[1:]):
        return ("some", ground[1], ground[2], ground[3])
    if tag == "axiom" and len(ground) == 2 and isinstance(ground[1], (tuple, list)):
        return ("axiom", tuple(ground[1]))
    raise ReasoningError(f"unknown ground form: {ground!r}")


def _check_conclusion(conclusion: object) -> tuple:
    if not isinstance(conclusion, (tuple, list)) or not conclusion:
        raise ReasoningError("conclusion must be a tagged tuple")
    if conclusion[0] == "sub" and len(conclusion) == 3 and all(
            isinstance(term, str) and term for term in conclusion[1:]):
        return ("sub", conclusion[1], conclusion[2])
    if conclusion[0] == "some" and len(conclusion) == 4 and all(
            isinstance(term, str) and term for term in conclusion[1:]):
        return ("some", conclusion[1], conclusion[2], conclusion[3])
    raise ReasoningError(f"unknown conclusion form: {conclusion!r}")


@dataclass(frozen=True)
class ELDerivation:
    """One completion step: rule, tagged conclusion and exact grounds."""

    rule: str
    conclusion: tuple
    premises: tuple[tuple, ...]
    round: int

    def __post_init__(self) -> None:
        _text(self.rule, "rule")
        if self.rule not in ("transitivity", "conjunction", "some-intro", "existential-left"):
            raise ReasoningError(f"unknown completion rule: {self.rule!r}")
        object.__setattr__(self, "conclusion", _check_conclusion(self.conclusion))
        if not isinstance(self.premises, (tuple, list)) or not self.premises:
            raise ReasoningError("premises must be non-empty")
        object.__setattr__(self, "premises", tuple(sorted(set(_check_ground(item) for item in self.premises))))
        if isinstance(self.round, bool) or not isinstance(self.round, int) or self.round < 1:
            raise ReasoningError("round must be a positive integer")

    @property
    def digest(self) -> str:
        import hashlib
        import json

        return "sha256:" + hashlib.sha256(json.dumps(
            ["gestaltdb-el-derivation-v1", self.rule, list(self.conclusion),
             [list(premise) for premise in self.premises], self.round],
            ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ELClassification:
    """Closed named subsumptions with one derivation per non-trivial entry."""

    subsumptions: tuple[tuple[str, str], ...]
    derivations: tuple[ELDerivation, ...]
    rounds: int

    def __post_init__(self) -> None:
        if not isinstance(self.subsumptions, (tuple, list)) or not all(
                isinstance(item, (tuple, list)) and len(item) == 2 for item in self.subsumptions):
            raise ReasoningError("subsumptions must be (sub, sup) pairs")
        object.__setattr__(self, "subsumptions",
                           tuple(sorted({(item[0], item[1]) for item in self.subsumptions})))
        if not isinstance(self.derivations, (tuple, list)) or not all(
                isinstance(item, ELDerivation) for item in self.derivations):
            raise ReasoningError("derivations must contain only ELDerivation values")
        object.__setattr__(self, "derivations", tuple(self.derivations))
        if isinstance(self.rounds, bool) or not isinstance(self.rounds, int) or self.rounds < 0:
            raise ReasoningError("rounds must be a non-negative integer")

    def holds(self, sub: str, sup: str) -> bool:
        _concept(sub, "sub")
        _concept(sup, "sup")
        if sub == sup:
            return True  # reflexivity is a logical axiom, not a derivation
        return (sub, sup) in set(self.subsumptions)

    def witnesses(self, sub: str, sup: str) -> tuple[ELDerivation, ...]:
        _concept(sub, "sub")
        _concept(sup, "sup")
        return tuple(item for item in self.derivations if item.conclusion == ("sub", sub, sup))


def _concepts(axioms) -> list[str]:
    names: set[str] = set()
    for axiom in axioms:
        if isinstance(axiom, Subsumption):
            names.update((axiom.sub, axiom.sup))
        elif isinstance(axiom, Conjunction):
            names.update(axiom.members)
            names.add(axiom.sup)
        elif isinstance(axiom, ExistentialRight):
            names.update((axiom.sub, axiom.filler))
        elif isinstance(axiom, ExistentialLeft):
            names.update((axiom.filler, axiom.sup))
    return sorted(names)


def classify(specs: Iterable[object], *, profile: str, max_subsumptions: int = 10_000,
             max_rounds: int = 1_000, cancel=None) -> ELClassification:
    """Close named subsumptions with completion rules under strict budgets.

    Rules: reflexivity init, transitivity, named-conjunction introduction,
    existential introduction right, existential elimination left. Rounds run
    to fixpoint; monotone growth over the finite concept vocabulary
    guarantees termination, and the budgets fail closed first. One
    derivation per non-trivial conclusion carries the rule and exact tagged
    grounds with strictly increasing rounds.
    """
    _gate(profile)
    if isinstance(max_subsumptions, bool) or not isinstance(max_subsumptions, int) or max_subsumptions < 1:
        raise ReasoningError("max_subsumptions must be a positive integer")
    if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 1:
        raise ReasoningError("max_rounds must be a positive integer")
    check_cancelled(cancel)
    try:
        raw = list(specs)
    except TypeError as exc:
        raise ReasoningError("specs must be iterable") from exc
    axioms = [normalize_axiom(spec, profile=profile) for spec in raw]
    named: dict[str, set[str]] = {concept: {concept} for concept in _concepts(axioms)}
    existential: dict[str, set[tuple[str, str]]] = {concept: set() for concept in named}
    conjunctions = [axiom for axiom in axioms if isinstance(axiom, Conjunction)]
    rights = [axiom for axiom in axioms if isinstance(axiom, ExistentialRight)]
    lefts = [axiom for axiom in axioms if isinstance(axiom, ExistentialLeft)]
    for axiom in axioms:
        if isinstance(axiom, Subsumption):
            named[axiom.sub].add(axiom.sup)
    derivations: list[ELDerivation] = []
    seen: set[tuple] = set()

    def count_named():
        return sum(len(sups) for sups in named.values())

    def emit(rule, conclusion, premises, round_no):
        key = (conclusion, rule)
        if key not in seen:
            seen.add(key)
            if conclusion[0] == "sub" and conclusion[2] not in named[conclusion[1]]:
                named[conclusion[1]].add(conclusion[2])
            if conclusion[0] == "some" and (conclusion[2], conclusion[3]) not in existential[conclusion[1]]:
                existential[conclusion[1]].add((conclusion[2], conclusion[3]))
            derivations.append(ELDerivation(rule, conclusion, premises, round_no))
            if count_named() + len(derivations) > max_subsumptions:
                raise ReasoningLimitError("EL classification exceeded max_subsumptions")

    rounds = 0
    changed = True
    while changed:
        check_cancelled(cancel)
        if rounds >= max_rounds:
            raise ReasoningLimitError("EL classification exceeded max_rounds")
        rounds += 1
        changed = False
        before = (count_named(), sum(len(pairs) for pairs in existential.values()),
                  len(derivations))
        for middle in sorted(named):
            for sub in sorted(named):
                if middle in named[sub]:
                    for sup in sorted(named[middle]):
                        if sup not in named[sub]:
                            emit("transitivity", ("sub", sub, sup),
                                 (("sub", sub, middle), ("sub", middle, sup)), rounds)
                            changed = True
        for axiom in conjunctions:
            for sub in sorted(named):
                if all(member in named[sub] for member in axiom.members):
                    if axiom.sup not in named[sub]:
                        emit("conjunction", ("sub", sub, axiom.sup),
                             tuple([("sub", sub, member) for member in axiom.members]
                                   + [("axiom", axiom.canonical())]), rounds)
                        changed = True
        for axiom in rights:
            for sub in sorted(named):
                if axiom.sub in named[sub] and (axiom.role, axiom.filler) not in existential[sub]:
                    emit("some-intro", ("some", sub, axiom.role, axiom.filler),
                         (("sub", sub, axiom.sub), ("axiom", axiom.canonical())), rounds)
                    changed = True
        for axiom in lefts:
            for sub in sorted(named):
                for role, filler in sorted(existential[sub]):
                    if role == axiom.role and axiom.filler in named.get(filler, set()):
                        if axiom.sup not in named[sub]:
                            emit("existential-left", ("sub", sub, axiom.sup),
                                 (("some", sub, role, filler), ("sub", filler, axiom.filler),
                                  ("axiom", axiom.canonical())), rounds)
                            changed = True
        after = (count_named(), sum(len(pairs) for pairs in existential.values()),
                 len(derivations))
        changed = changed or after != before
    subs = sorted((sub, sup) for sub, sups in named.items() for sup in sups)
    return ELClassification(tuple(subs), tuple(derivations), rounds)


def check_proof(specs: Iterable[object], classification: ELClassification, *,
                profile: str) -> int:
    """Independently replay every derivation in round order and verify it.

    Seeds tables from the axioms and reflexive inits, then checks each
    claimed ground (subsumptions present, existentials active, axioms
    input) and each rule application before applying its conclusion.
    Returns the number of checked steps; any mismatch fails closed.
    """
    _gate(profile)
    if not isinstance(classification, ELClassification):
        raise ReasoningError("classification must be an ELClassification")
    try:
        axioms = [normalize_axiom(spec, profile=profile) for spec in list(specs)]
    except TypeError as exc:
        raise ReasoningError("specs must be iterable") from exc
    canonicals = {axiom.canonical() for axiom in axioms}
    named: dict[str, set[str]] = {concept: {concept} for concept in _concepts(axioms)}
    existential: dict[str, set[tuple[str, str]]] = {concept: set() for concept in named}
    for axiom in axioms:
        if isinstance(axiom, Subsumption):
            named[axiom.sub].add(axiom.sup)
    ordered = sorted(classification.derivations, key=lambda item: (item.round, item.digest))
    steps = 0
    pending = list(ordered)
    while pending:
        progressed = False
        for derivation in list(pending):
            if not _grounds_ready(derivation, named, existential, canonicals):
                continue
            _apply_checked(derivation, named, existential, canonicals)
            pending.remove(derivation)
            steps += 1
            progressed = True
        if not progressed:
            raise ReasoningError("derivations do not close in dependency order; proof rejected")
    replayed = sorted((sub, sup) for sub, sups in named.items() for sup in sups)
    if replayed != sorted(classification.subsumptions):
        raise ReasoningError("replayed closure differs from the claimed classification")
    return steps


def _grounds_ready(derivation, named, existential, canonicals) -> bool:
    for ground in derivation.premises:
        if ground[0] == "sub" and ground[2] not in named.get(ground[1], set()):
            return False
        if ground[0] == "some" and (ground[2], ground[3]) not in existential.get(ground[1], set()):
            return False
        if ground[0] == "axiom" and ground[1] not in canonicals:
            return False
    return True


def _apply_checked(derivation, named, existential, canonicals) -> None:
    for ground in derivation.premises:
        if ground[0] == "sub" and ground[2] not in named.get(ground[1], set()):
            raise ReasoningError(f"unproven premise {ground!r} in {derivation.rule!r}")
        if ground[0] == "some" and (ground[2], ground[3]) not in existential.get(ground[1], set()):
            raise ReasoningError(f"inactive existential {ground!r} in {derivation.rule!r}")
        if ground[0] == "axiom" and ground[1] not in canonicals:
            raise ReasoningError(f"non-input axiom {ground!r} in {derivation.rule!r}")
    conclusion = derivation.conclusion
    if derivation.rule == "transitivity":
        if len(derivation.premises) != 2:
            raise ReasoningError("transitivity needs exactly two premises")
        if any(item[0] != "sub" or len(item) != 3 for item in derivation.premises):
            raise ReasoningError("transitivity needs subsumption premises")
        pairs = {(item[1], item[2]) for item in derivation.premises}
        middles = {first for first, _ in pairs} | {second for _, second in pairs}
        chained = any((conclusion[1], middle) in pairs and (middle, conclusion[2]) in pairs
                      for middle in middles)
        if not chained or conclusion[0] != "sub":
            raise ReasoningError("transitivity conclusion does not chain its premises")
    elif derivation.rule == "conjunction":
        axiom_grounds = [item for item in derivation.premises if item[0] == "axiom"]
        if len(axiom_grounds) != 1 or axiom_grounds[0][1][0] != "and":
            raise ReasoningError("conjunction needs its axiom ground")
        _tag, members, sup = axiom_grounds[0][1]
        needed = {(conclusion[1], member) for member in members}
        have = {(item[1], item[2]) for item in derivation.premises if item[0] == "sub"}
        if conclusion != ("sub", conclusion[1], sup) or needed != have:
            raise ReasoningError("conjunction premises do not cover the axiom members")
    elif derivation.rule == "some-intro":
        axiom_grounds = [item for item in derivation.premises if item[0] == "axiom"]
        sub_grounds = [item for item in derivation.premises if item[0] == "sub"]
        if (len(axiom_grounds) != 1 or len(sub_grounds) != 1
                or axiom_grounds[0][1][0] != "some-right"):
            raise ReasoningError("some-intro needs one subsumption and its axiom")
        _t, anchor, role, filler = axiom_grounds[0][1]
        if conclusion != ("some", sub_grounds[0][1], role, filler) or sub_grounds[0][2] != anchor:
            raise ReasoningError("some-intro conclusion does not follow its grounds")
    elif derivation.rule == "existential-left":
        some = [item for item in derivation.premises if item[0] == "some"]
        subs = [item for item in derivation.premises if item[0] == "sub"]
        axiom_grounds = [item for item in derivation.premises if item[0] == "axiom"]
        if len(some) != 1 or len(subs) != 1 or len(axiom_grounds) != 1:
            raise ReasoningError("existential-left needs some, sub and axiom grounds")
        if axiom_grounds[0][1][0] != "some-left":
            raise ReasoningError("existential-left needs a some-left axiom")
        _t, role, axiom_filler, sup = axiom_grounds[0][1]
        if some[0][0] != "some" or some[0][1] != conclusion[1] or some[0][2] != role:
            raise ReasoningError("existential-left some ground does not match the axiom role")
        if subs[0] != ("sub", some[0][3], axiom_filler):
            raise ReasoningError("existential-left filler chain is broken")
        if conclusion != ("sub", conclusion[1], sup):
            raise ReasoningError("existential-left conclusion does not follow its grounds")
    else:
        raise ReasoningError(f"unknown completion rule: {derivation.rule!r}")
    if conclusion[0] == "sub":
        named.setdefault(conclusion[1], {conclusion[1]}).add(conclusion[2])
    else:
        existential.setdefault(conclusion[1], set()).add((conclusion[2], conclusion[3]))


def holds_in_all_models(specs: Iterable[object], sub: str, sup: str, *, profile: str,
                        domain_size: int = 2) -> bool | None:
    """Independent model-theoretic oracle over finite interpretations.

    Enumerates every interpretation on ``domain_size`` individuals and checks
    the query in all models of the axioms. Returns ``None`` when the axioms
    are unsatisfiable at that size (no models to check) instead of a vacuous
    verdict. Exponential by design; test domains only.
    """
    _gate(profile)
    _concept(sub, "sub")
    _concept(sup, "sup")
    if isinstance(domain_size, bool) or not isinstance(domain_size, int) or domain_size < 1 or domain_size > 3:
        raise ReasoningError("domain_size must be 1, 2 or 3")
    try:
        axioms = [normalize_axiom(spec, profile=profile) for spec in list(specs)]
    except TypeError as exc:
        raise ReasoningError("specs must be iterable") from exc
    concepts = sorted({term for axiom in axioms for term in _axiom_concepts(axiom)} | {sub, sup})
    roles = sorted({axiom.role for axiom in axioms
                    if isinstance(axiom, (ExistentialRight, ExistentialLeft))})
    domain = list(range(domain_size))
    import itertools

    concept_powers = list(itertools.product([False, True], repeat=len(concepts) * domain_size))
    role_powers = list(itertools.product([False, True], repeat=len(roles) * domain_size * domain_size))
    models = 0
    for cbits in concept_powers:
        extension = {concept: {individual for individual in domain if cbits[index * domain_size + individual]}
                     for index, concept in enumerate(concepts)}
        for rbits in role_powers:
            relation = {}
            offset = 0
            for role in roles:
                pairs = {(first, second) for first in domain for second in domain
                         if rbits[offset + first * domain_size + second]}
                relation[role] = pairs
                offset += domain_size * domain_size
            if all(_holds(axiom, extension, relation, domain) for axiom in axioms):
                models += 1
                if not extension[sub] <= extension[sup]:
                    return False
    return True if models else None


def _axiom_concepts(axiom):
    if isinstance(axiom, Subsumption):
        return (axiom.sub, axiom.sup)
    if isinstance(axiom, Conjunction):
        return (*axiom.members, axiom.sup)
    if isinstance(axiom, ExistentialRight):
        return (axiom.sub, axiom.filler)
    if isinstance(axiom, ExistentialLeft):
        return (axiom.filler, axiom.sup)
    return ()


def _holds(axiom, extension, relation, domain) -> bool:
    if isinstance(axiom, Subsumption):
        return extension.get(axiom.sub, set()) <= extension.get(axiom.sup, set())
    if isinstance(axiom, Conjunction):
        meeting = set(domain)
        for member in axiom.members:
            meeting &= extension.get(member, set())
        return meeting <= extension.get(axiom.sup, set())
    if isinstance(axiom, ExistentialRight):
        for individual in extension.get(axiom.sub, set()):
            if not any((individual, other) in relation.get(axiom.role, set())
                       and other in extension.get(axiom.filler, set()) for other in domain):
                return False
        return True
    if isinstance(axiom, ExistentialLeft):
        for first in domain:
            for second in domain:
                if ((first, second) in relation.get(axiom.role, set())
                        and second in extension.get(axiom.filler, set())
                        and first not in extension.get(axiom.sup, set())):
                    return False
        return True
    raise ReasoningError(f"unknown axiom: {axiom!r}")
