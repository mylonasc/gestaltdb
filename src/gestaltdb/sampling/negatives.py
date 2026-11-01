"""Negative sampling configuration."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
from typing import Literal



@dataclass(frozen=True, slots=True)
class HardNegativeConfig:
    """Configuration for KG hard-negative generation.

    IDs are compact snapshot IDs; use ``*_external`` engine entry points to
    pass application string IDs, then resolve results with ``to_external``.

    Attributes:
        negatives_per_positive: Negatives drawn per positive triple.
        source: Candidate source (``"random"`` default). ``"in-batch"`` is
            enabled separately via ``in_batch=True`` below.
        reject_known_positives: Skip candidates that match a known positive.
        same_endpoint_type: Restrict corrupted endpoints to the positive's type.
        relation_endpoint_types: Restrict candidates by relation endpoint types.
        candidate_fanout: Candidate pool size per draw; ``None`` uses the
            engine default.
        head_probability: Probability of corrupting the head (vs tail).
        max_retries: Attempts per negative before exhaustion handling.
        temporal_positive_policy: ``"any_time"`` (default), ``"at_positive_time"``,
            or ``"window"``. Time-aware policies need ``positive_times_us`` or a
            ``temporal`` context unless ``allow_future_candidates=True``.
        temporal_positive_window_policy: ``"overlap"`` or ``"contained"`` for
            ``"window"`` membership.
        temporal_candidate_window_days: Trailing candidate window size; ``None``
            disables windowing.
        allow_future_candidates: Opt in to candidates after the positive time.
        exhaustion_policy: ``"raise"`` (default) or ``"repeat"`` an accepted
            negative when the pool is exhausted.
        in_batch: Also try other in-batch positives as negatives first.
        candidate_distribution: ``"uniform"`` or ``"degree"`` (weights the
            random fallback by ``incident_degree ** degree_exponent``).
        degree_exponent: Exponent for ``"degree"`` weighting (default 0.75).

    Examples:
        Leakage-safe time-aware negatives::

            config = HardNegativeConfig(
                temporal_positive_policy="at_positive_time",
                temporal_candidate_window_days=90,
            )

    See Also:
        ``docs/typed-sampling.rst`` "Time-Aware Hard Negatives" for policies,
        diagnostics, and exhaustion semantics.
    """

    negatives_per_positive: int = 1
    source: Literal["random", "non_visited_relation_neighbors", "context_relation_neighbors"] = "random"
    reject_known_positives: bool = True
    same_endpoint_type: bool = False
    relation_endpoint_types: bool = False
    candidate_fanout: int | None = None
    head_probability: float = 0.5
    max_retries: int = 100
    temporal_positive_policy: Literal["any_time", "at_positive_time", "window"] = "any_time"
    temporal_positive_window_policy: Literal["overlap", "contained"] = "overlap"
    temporal_candidate_window_days: float | None = None
    allow_future_candidates: bool = False
    exhaustion_policy: Literal["raise", "repeat"] = "raise"
    in_batch: bool = False
    candidate_distribution: Literal["uniform", "degree"] = "uniform"
    degree_exponent: float = 0.75

    def __post_init__(self) -> None:
        if self.negatives_per_positive < 0:
            raise ValueError("negatives_per_positive must be non-negative")
        if not isinstance(self.in_batch, bool):
            raise ValueError("in_batch must be a boolean")
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
        if self.candidate_distribution not in {"uniform", "degree"}:
            raise ValueError("candidate_distribution must be 'uniform' or 'degree'")
        if isinstance(self.degree_exponent, bool) or not isinstance(self.degree_exponent, Real):
            raise TypeError("degree_exponent must be a number")
        if self.degree_exponent < 0:
            raise ValueError("degree_exponent must be non-negative")
