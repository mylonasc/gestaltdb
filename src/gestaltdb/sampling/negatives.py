"""Negative sampling configuration."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Real



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
    temporal_positive_policy: str = "any_time"
    temporal_positive_window_policy: str = "overlap"
    temporal_candidate_window_days: float | None = None
    allow_future_candidates: bool = False
    exhaustion_policy: str = "raise"

    def __post_init__(self):
        if self.negatives_per_positive < 0:
            raise ValueError("negatives_per_positive must be non-negative")
        if not 0.0 <= self.head_probability <= 1.0:
            raise ValueError("head_probability must be between 0 and 1")
        if self.max_retries < 1:
            raise ValueError("max_retries must be at least 1")
        if self.candidate_fanout is not None and self.candidate_fanout < 1:
            raise ValueError("candidate_fanout must be at least 1 when provided")
        if self.temporal_positive_policy not in {"any_time", "at_positive_time", "window"}:
            raise ValueError("temporal_positive_policy must be 'any_time', 'at_positive_time', or 'window'")
        if self.temporal_positive_window_policy not in {"overlap", "contained"}:
            raise ValueError("temporal_positive_window_policy must be 'overlap' or 'contained'")
        if self.temporal_candidate_window_days is not None:
            if isinstance(self.temporal_candidate_window_days, bool) or not isinstance(
                self.temporal_candidate_window_days, Real
            ):
                raise TypeError("temporal_candidate_window_days must be a number")
            if self.temporal_candidate_window_days <= 0:
                raise ValueError("temporal_candidate_window_days must be positive")
        if self.exhaustion_policy not in {"raise", "repeat"}:
            raise ValueError("exhaustion_policy must be 'raise' or 'repeat'")
