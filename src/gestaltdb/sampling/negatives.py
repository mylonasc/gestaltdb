"""Negative sampling configuration."""

from __future__ import annotations

from dataclasses import dataclass



@dataclass(frozen=True, slots=True)
class HardNegativeConfig:
    """Configuration for KG hard-negative generation."""

    negatives_per_positive: int = 1
    source: str = "random"
    reject_known_positives: bool = True
    same_endpoint_type: bool = False
    relation_endpoint_types: bool = False
    candidate_fanout: int | None = None
    head_probability: float = 0.5
    max_retries: int = 100

    def __post_init__(self):
        if self.negatives_per_positive < 0:
            raise ValueError("negatives_per_positive must be non-negative")
        if not 0.0 <= self.head_probability <= 1.0:
            raise ValueError("head_probability must be between 0 and 1")
        if self.max_retries < 1:
            raise ValueError("max_retries must be at least 1")
        if self.candidate_fanout is not None and self.candidate_fanout < 1:
            raise ValueError("candidate_fanout must be at least 1 when provided")
