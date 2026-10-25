"""Deterministic executor selection over measured evidence; no auto-tuning.

The default stays the indexed Python engine. The Polars prototype is honored
only on explicit request for closure-only runs with a declared-available
runtime. Proofs always route to indexed engines. Unknown profiles, modes or
preferences fail closed. Callers declare optional-runtime availability; this
module never imports optional dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import ReasoningError, _text


PROFILE = "positive-binary-entity-v1"
TYPED_PROFILE = "positive-typed-point-v1"
TYPED_DICTIONARY = "typed-values-v1"
TYPED_INDEXED_PYTHON = "typed-indexed-python-v1"
INDEXED_PYTHON = "indexed-python-v1"
INDEXED_SCC = "indexed-scc-v1"
POLARS_PROTOTYPE = "polars-prototype-v1"
EXECUTORS = (INDEXED_PYTHON, INDEXED_SCC, POLARS_PROTOTYPE, TYPED_INDEXED_PYTHON)
MODES = ("closure", "proofs")
PREFERENCES = ("default", "indexed-python", "indexed-scc", "polars-prototype")


@dataclass(frozen=True)
class ExecutorRequest:
    """Explicit selection inputs; availability flags are caller-probed.

    ``preferred`` selects an executor family outright instead of the default.
    ``polars_available`` must reflect the real installed runtime, not intent.
    """

    profile: str = PROFILE
    mode: str = "closure"
    preferred: str = "default"
    polars_available: bool = False

    def __post_init__(self) -> None:
        _text(self.profile, "profile")
        if self.mode not in MODES:
            raise ReasoningError(f"mode must be one of {MODES}")
        if self.preferred not in PREFERENCES:
            raise ReasoningError(f"preferred must be one of {PREFERENCES}")
        if not isinstance(self.polars_available, bool):
            raise ReasoningError("polars_available must be a bool")


@dataclass(frozen=True)
class ExecutorDecision:
    """Chosen executor plus the evidence codes behind the choice."""

    kind: str
    rationale: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in EXECUTORS:
            raise ReasoningError(f"kind must be one of {EXECUTORS}")
        if not isinstance(self.rationale, (tuple, list)) or not self.rationale:
            raise ReasoningError("rationale must be a non-empty tuple of codes")
        for code in self.rationale:
            _text(code, "rationale code")
        object.__setattr__(self, "rationale", tuple(self.rationale))


def select_executor(request: ExecutorRequest) -> ExecutorDecision:
    """Select an executor deterministically; identical requests agree.

    Selection encodes the RE-01/RE-03/RE-04/RE-05 measurements: indexed Python
    is the reference default with the smallest startup and RSS and the fastest
    small-workload pipelines; the Polars prototype wins dense closures but
    costs import time and memory and supports no proofs. No workload-size
    heuristic overrides an explicit request, and no request silently falls
    back to a weaker executor.
    """
    if not isinstance(request, ExecutorRequest):
        raise ReasoningError("request must be an ExecutorRequest")
    if request.profile == TYPED_PROFILE:
        if request.mode != "closure":
            raise ReasoningError("positive typed execution supports closure only; no witness downgrade")
        if request.preferred not in ("default", "indexed-python"):
            raise ReasoningError("positive typed execution requires the typed indexed engine; no implicit recoding")
        return ExecutorDecision(TYPED_INDEXED_PYTHON,
                                ("typed-values-require-generic-indexed", "closure-only-no-proofs"))
    if request.profile != PROFILE:
        raise ReasoningError(f"unsupported semantics profile: {request.profile}")
    if request.mode == "proofs":
        if request.preferred == "polars-prototype":
            raise ReasoningError("polars prototype does not support witness collection")
        if request.preferred in ("indexed-python", "default"):
            kind = INDEXED_PYTHON
        else:
            kind = INDEXED_SCC
        return ExecutorDecision(kind, ("proofs-require-indexed-witnesses",
                                       "explicit-preference-honored" if request.preferred != "default" else "reference-default"))
    if request.preferred == "polars-prototype":
        if not request.polars_available:
            raise ReasoningError("polars prototype requested but polars_available is False")
        return ExecutorDecision(POLARS_PROTOTYPE, ("explicit-preference-honored",
                                                   "closure-only-no-proofs",
                                                   "dense-closure-tradeoff-documented"))
    if request.preferred == "indexed-scc":
        return ExecutorDecision(INDEXED_SCC, ("explicit-preference-honored",
                                              "reusable-plans-telemetry-available"))
    return ExecutorDecision(INDEXED_PYTHON, ("reference-default",
                                             "smallest-startup-and-rss",
                                             "small-workload-pipeline-fastest"))
