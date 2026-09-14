"""Sampling APIs for GestaltDB.

This package preserves the original typed traversal configuration objects while
adding array-native sampler snapshot and engine primitives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence, Union


@dataclass(frozen=True)
class SamplingHop:
    """Configuration for one typed sampling hop."""

    edge_type: str
    direction: str = "out"
    sample_size: int = 10

    def __post_init__(self):
        if self.direction not in {"out", "in", "any"}:
            raise ValueError("direction must be 'out', 'in', or 'any'")
        if self.sample_size < 1:
            raise ValueError("sample_size must be at least 1")

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "SamplingHop":
        return cls(
            edge_type=str(data["edge_type"]),
            direction=str(data.get("direction", "out")),
            sample_size=int(data.get("sample_size", 10)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "edge_type": self.edge_type,
            "direction": self.direction,
            "sample_size": self.sample_size,
        }


@dataclass(frozen=True)
class SamplingPattern:
    """Ordered typed sampling pattern."""

    hops: Sequence[Union[SamplingHop, Mapping[str, object]]]

    def __post_init__(self):
        object.__setattr__(self, "hops", tuple(as_sampling_hop(hop) for hop in self.hops))

    def __iter__(self):
        return iter(self.hops)

    def __len__(self):
        return len(self.hops)

    @classmethod
    def from_dicts(cls, hops: Iterable[Mapping[str, object]]) -> "SamplingPattern":
        return cls(list(hops))

    def to_dicts(self) -> list[dict[str, object]]:
        return [hop.to_dict() for hop in self.hops]


def as_sampling_hop(hop: Union[SamplingHop, Mapping[str, object]]) -> SamplingHop:
    """Normalize a hop configuration to ``SamplingHop``."""
    if isinstance(hop, SamplingHop):
        return hop
    return SamplingHop.from_dict(hop)


def as_sampling_pattern(
    pattern: Union[SamplingPattern, Iterable[Union[SamplingHop, Mapping[str, object]]]],
) -> SamplingPattern:
    """Normalize a sampling pattern to ``SamplingPattern``."""
    if isinstance(pattern, SamplingPattern):
        return pattern
    return SamplingPattern(list(pattern))


from .batch import SampledSubgraphBatch  # noqa: E402
from .engine import LayeredSampleBatch, NeighborSamplingSpec, SampledNeighbors, SamplerEngine  # noqa: E402
from .feeder import AsyncBatchFeeder  # noqa: E402
from .negatives import HardNegativeConfig  # noqa: E402
from .snapshot import SamplerSnapshot  # noqa: E402


__all__ = [
    "HardNegativeConfig",
    "AsyncBatchFeeder",
    "LayeredSampleBatch",
    "NeighborSamplingSpec",
    "SampledNeighbors",
    "SampledSubgraphBatch",
    "SamplerEngine",
    "SamplerSnapshot",
    "SamplingHop",
    "SamplingPattern",
    "as_sampling_hop",
    "as_sampling_pattern",
]
