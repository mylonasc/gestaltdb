"""Explicit cross-context, temporal-modal and confidence design gates.

Nothing here evaluates, combines or inherits: this module specifies the
boundaries that wider reasoning must satisfy before it exists. Cross-world
and cross-context inheritance is default-deny — only an explicit
``InheritanceGrant`` authorizes flow, and temporal-modal evaluation stays
inside declared operator/frame/depth bounds without changing the existing
bounded modal API. Confidence, fuzzy and probabilistic combination are
separately selected calculi, never logical truth and never accidental
multiplication: ``select_calculus`` fails closed until combination,
retraction, convergence and provenance policies each have independent
tests plus a registered user-value scenario. No calculus ships approved;
the registry records exactly what approval requires.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import ReasoningError, _text


@dataclass(frozen=True)
class InheritanceGrant:
    """One authorized inheritance edge: agent, source, target and frame."""

    agent: str
    source_world: str
    target_world: str
    frame: str

    def __post_init__(self) -> None:
        for field in ("agent", "source_world", "target_world", "frame"):
            _text(getattr(self, field), field)
        if self.source_world == self.target_world:
            raise ReasoningError("inheritance requires distinct source and target worlds")


@dataclass(frozen=True)
class InheritancePolicy:
    """Default-deny allowlist for cross-world/context inheritance."""

    grants: tuple[InheritanceGrant, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.grants, (tuple, list)):
            raise ReasoningError("grants must be a tuple or list")
        if not all(isinstance(item, InheritanceGrant) for item in self.grants):
            raise ReasoningError("grants must contain only InheritanceGrant values")
        object.__setattr__(self, "grants", tuple(sorted(
            set(self.grants),
            key=lambda item: (item.agent, item.source_world, item.target_world, item.frame))))

    def permits(self, agent: str, source_world: str, target_world: str, frame: str) -> bool:
        for field, value in (("agent", agent), ("source_world", source_world),
                             ("target_world", target_world), ("frame", frame)):
            _text(value, field)
        return InheritanceGrant(agent, source_world, target_world, frame) in set(self.grants)

    def check(self, agent: str, source_world: str, target_world: str, frame: str) -> None:
        """Fail closed unless an explicit grant authorizes this flow."""
        if not self.permits(agent, source_world, target_world, frame):
            raise ReasoningError(
                f"no inheritance grant for {agent!r} from {source_world!r} "
                f"to {target_world!r} in frame {frame!r}")


@dataclass(frozen=True)
class ModalBoundary:
    """Temporal-modal evaluation bounds for one operator in one frame."""

    operator: str
    frame: str
    max_depth: int

    def __post_init__(self) -> None:
        _text(self.operator, "operator")
        _text(self.frame, "frame")
        if isinstance(self.max_depth, bool) or not isinstance(self.max_depth, int) or self.max_depth < 0:
            raise ReasoningError("max_depth must be a non-negative integer")


@dataclass(frozen=True)
class ModalScope:
    """Declared operator/frame/depth bounds; evaluation stays inside them."""

    boundaries: tuple[ModalBoundary, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.boundaries, (tuple, list)):
            raise ReasoningError("boundaries must be a tuple or list")
        if not all(isinstance(item, ModalBoundary) for item in self.boundaries):
            raise ReasoningError("boundaries must contain only ModalBoundary values")
        pairs = [(item.operator, item.frame) for item in self.boundaries]
        if len(set(pairs)) != len(pairs):
            raise ReasoningError("modal boundaries must be unique per operator and frame")
        object.__setattr__(self, "boundaries", tuple(sorted(
            self.boundaries, key=lambda item: (item.operator, item.frame))))

    def check(self, operator: str, frame: str, depth: int) -> None:
        """Fail closed outside declared bounds; never widens evaluation."""
        _text(operator, "operator")
        _text(frame, "frame")
        if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
            raise ReasoningError("depth must be a non-negative integer")
        for boundary in self.boundaries:
            if boundary.operator == operator and boundary.frame == frame:
                if depth > boundary.max_depth:
                    raise ReasoningError(
                        f"{operator!r} in frame {frame!r} exceeds max_depth={boundary.max_depth}")
                return
        raise ReasoningError(f"no modal boundary declares {operator!r} in frame {frame!r}")


@dataclass(frozen=True)
class CalculusPolicy:
    """One independently tested policy backing a calculus: name plus evidence."""

    name: str
    evidence: str

    def __post_init__(self) -> None:
        _text(self.name, "name")
        _text(self.evidence, "evidence")


@dataclass(frozen=True)
class CalculusSpec:
    """A separately selected combination calculus with all four policies.

    Combination, retraction, convergence and provenance must each name an
    independently tested policy plus the user-value scenario they serve.
    Registration without all four fails closed; nothing ships approved.
    """

    calculus: str
    combination: CalculusPolicy
    retraction: CalculusPolicy
    convergence: CalculusPolicy
    provenance: CalculusPolicy
    scenario: str

    def __post_init__(self) -> None:
        _text(self.calculus, "calculus")
        _text(self.scenario, "scenario")
        for field in ("combination", "retraction", "convergence", "provenance"):
            if not isinstance(getattr(self, field), CalculusPolicy):
                raise ReasoningError(f"{field} must be a CalculusPolicy with test evidence")


@dataclass(frozen=True)
class CalculusRegistry:
    """Approved calculi only; empty until an extension earns its row."""

    calculi: tuple[CalculusSpec, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.calculi, (tuple, list)):
            raise ReasoningError("calculi must be a tuple or list")
        if not all(isinstance(item, CalculusSpec) for item in self.calculi):
            raise ReasoningError("calculi must contain only CalculusSpec values")
        names = [item.calculus for item in self.calculi]
        if len(set(names)) != len(names):
            raise ReasoningError("calculus names must be unique")
        object.__setattr__(self, "calculi", tuple(sorted(self.calculi, key=lambda item: item.calculus)))

    def select(self, calculus: str) -> CalculusSpec:
        """Select one approved calculus; anything else fails closed."""
        _text(calculus, "calculus")
        for item in self.calculi:
            if item.calculus == calculus:
                return item
        raise ReasoningError(
            f"calculus {calculus!r} is not approved: register combination, retraction, "
            "convergence and provenance policies with independent tests plus a "
            "user-value scenario first")


def approval_gates() -> tuple[str, ...]:
    """What a confidence calculus or cross-world extension must present."""
    return ("independent combination tests with a user-value scenario",
            "independent retraction tests preserving alternatives",
            "independent convergence tests with explicit termination",
            "independent provenance tests tracing every combined value")
