"""Typed sampling configuration objects for GestaltDB.

The graph sampling APIs accept these objects as a structured alternative to
plain dictionaries while preserving dict compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence, Union


@dataclass(frozen=True)
class SamplingHop:
    """Configuration for one typed sampling hop.

    Args:
        edge_type: Edge type to traverse, read from ``edge.properties["type"]``.
        direction: Traversal direction. Use ``"out"`` for source to target,
            ``"in"`` for target to source, or ``"any"`` for both directions.
        sample_size: Maximum number of neighbors to sample at this hop for each
            node in the current frontier.

    Examples:
        >>> hop = SamplingHop("drug-to-protein", direction="out", sample_size=2)
        >>> hop.to_dict()
        {'edge_type': 'drug-to-protein', 'direction': 'out', 'sample_size': 2}
    """

    edge_type: str
    direction: str = "out"
    sample_size: int = 10

    def __post_init__(self):
        """Validate hop values after dataclass initialization.

        Raises:
            ValueError: If direction is invalid or sample_size is less than 1.

        Examples:
            >>> SamplingHop("drug-to-protein", sample_size=1).sample_size
            1
        """
        if self.direction not in {"out", "in", "any"}:
            raise ValueError("direction must be 'out', 'in', or 'any'")
        if self.sample_size < 1:
            raise ValueError("sample_size must be at least 1")

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "SamplingHop":
        """Create a hop from a dictionary-style sampling configuration.

        Args:
            data: Mapping with ``edge_type`` and optional ``direction`` and
                ``sample_size`` keys.

        Returns:
            A validated ``SamplingHop`` instance.

        Examples:
            >>> SamplingHop.from_dict({'edge_type': 'drug-to-protein', 'sample_size': 2})
            SamplingHop(edge_type='drug-to-protein', direction='out', sample_size=2)
        """
        return cls(
            edge_type=str(data["edge_type"]),
            direction=str(data.get("direction", "out")),
            sample_size=int(data.get("sample_size", 10)),
        )

    def to_dict(self) -> dict[str, object]:
        """Return a dictionary compatible with the original sampling API.

        Returns:
            A dictionary containing ``edge_type``, ``direction``, and
            ``sample_size``.

        Examples:
            >>> SamplingHop("drug-to-protein", sample_size=2).to_dict()["sample_size"]
            2
        """
        return {
            "edge_type": self.edge_type,
            "direction": self.direction,
            "sample_size": self.sample_size,
        }


@dataclass(frozen=True)
class SamplingPattern:
    """Ordered typed sampling pattern.

    Args:
        hops: Sequence of ``SamplingHop`` objects or dictionary-style hop
            configurations.

    Examples:
        >>> pattern = SamplingPattern([
        ...     SamplingHop("drug-to-protein", sample_size=2),
        ...     {"edge_type": "protein-to-disease", "direction": "out"},
        ... ])
        >>> len(pattern)
        2
    """

    hops: Sequence[Union[SamplingHop, Mapping[str, object]]]

    def __post_init__(self):
        """Normalize hop inputs to ``SamplingHop`` objects.

        Examples:
            >>> SamplingPattern([{'edge_type': 'drug-to-protein'}]).hops[0].direction
            'out'
        """
        object.__setattr__(self, "hops", tuple(as_sampling_hop(hop) for hop in self.hops))

    def __iter__(self):
        """Iterate over normalized hops.

        Examples:
            >>> [hop.edge_type for hop in SamplingPattern([SamplingHop('a-to-b')])]
            ['a-to-b']
        """
        return iter(self.hops)

    def __len__(self):
        """Return the number of hops in the pattern.

        Examples:
            >>> len(SamplingPattern([SamplingHop('a-to-b')]))
            1
        """
        return len(self.hops)

    @classmethod
    def from_dicts(cls, hops: Iterable[Mapping[str, object]]) -> "SamplingPattern":
        """Create a pattern from dictionary-style hop configurations.

        Args:
            hops: Iterable of mappings accepted by ``SamplingHop.from_dict``.

        Returns:
            A normalized sampling pattern.

        Examples:
            >>> SamplingPattern.from_dicts([{'edge_type': 'drug-to-protein'}]).to_dicts()[0]['edge_type']
            'drug-to-protein'
        """
        return cls(list(hops))

    def to_dicts(self) -> list[dict[str, object]]:
        """Return dictionary configurations for all hops.

        Returns:
            List of dictionary-style hop configurations.

        Examples:
            >>> SamplingPattern([SamplingHop('a-to-b')]).to_dicts()[0]['direction']
            'out'
        """
        return [hop.to_dict() for hop in self.hops]


def as_sampling_hop(hop: Union[SamplingHop, Mapping[str, object]]) -> SamplingHop:
    """Normalize a hop configuration to ``SamplingHop``.

    Args:
        hop: Either a ``SamplingHop`` or dictionary-style hop configuration.

    Returns:
        A ``SamplingHop`` instance.

    Examples:
        >>> as_sampling_hop({'edge_type': 'drug-to-protein'}).edge_type
        'drug-to-protein'
    """
    if isinstance(hop, SamplingHop):
        return hop
    return SamplingHop.from_dict(hop)


def as_sampling_pattern(
    pattern: Union[SamplingPattern, Iterable[Union[SamplingHop, Mapping[str, object]]]],
) -> SamplingPattern:
    """Normalize a sampling pattern to ``SamplingPattern``.

    Args:
        pattern: A ``SamplingPattern`` or iterable of hop configurations.

    Returns:
        A ``SamplingPattern`` instance.

    Examples:
        >>> as_sampling_pattern([{'edge_type': 'drug-to-protein'}]).hops[0].sample_size
        10
    """
    if isinstance(pattern, SamplingPattern):
        return pattern
    return SamplingPattern(list(pattern))
