"""Immutable neighbor-sampling policies (kept importable from engine)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .validation import _integer_array, _validate_count
from ._arrays import Array


@dataclass(frozen=True)
class NeighborSamplingSpec:
    """Sampling policy for one layer of neighborhood expansion."""

    fanout: int
    direction: str = "out"
    relations: Sequence[int] | Array | None = None
    replace: bool = False
    strategy: str = "uniform"
    balanced: bool = False

    def __post_init__(self) -> None:
        _validate_count(self.fanout, "fanout")
        if self.direction not in {"out", "in", "any"}:
            raise ValueError("direction must be 'out', 'in', or 'any'")
        if self.strategy not in {"uniform", "weighted"}:
            raise ValueError("strategy must be 'uniform' or 'weighted'")
        if not isinstance(self.balanced, bool):
            raise ValueError("balanced must be a boolean")
        if self.relations is not None:
            relations = _integer_array(self.relations, "relations")
            object.__setattr__(self, "relations", tuple(int(rel) for rel in relations))


@dataclass(frozen=True)
class ExternalNeighborSamplingSpec:
    """Sampling policy using external relation IDs for one layer."""

    fanout: int
    direction: str = "out"
    relations: Sequence[object] | None = None
    replace: bool = False
    strategy: str = "uniform"
    balanced: bool = False

    def __post_init__(self) -> None:
        _validate_count(self.fanout, "fanout")
        if self.direction not in {"out", "in", "any"}:
            raise ValueError("direction must be 'out', 'in', or 'any'")
        if self.strategy not in {"uniform", "weighted"}:
            raise ValueError("strategy must be 'uniform' or 'weighted'")
        if not isinstance(self.balanced, bool):
            raise ValueError("balanced must be a boolean")
        if self.relations is not None:
            if isinstance(self.relations, (str, bytes)):
                raise TypeError("relations must be a sequence, not a scalar string")
            normalized = np.asarray(list(self.relations), dtype=str).tolist()
            object.__setattr__(self, "relations", tuple(normalized))
