"""Fail-closed generation-bound admission matrix."""

from dataclasses import replace

import pytest

from gestaltdb.reasoning.admission import AdmissionRequest, admit
from gestaltdb.reasoning.executors import INDEXED_PYTHON, INDEXED_SCC, POLARS_PROTOTYPE
from gestaltdb.reasoning.generation import GenerationIdentity, TemporalSourceIdentity
from gestaltdb.reasoning.model import ReasoningError
from gestaltdb.reasoning.protocols import InputContext


def generation(dictionary="external-strings-v1"):
    return GenerationIdentity(TemporalSourceIdentity("db", 1, "token", 50),
                              InputContext("context", "base", "revision", "policy", "mapping"),
                              "program", "catalog", "positive", dictionary)


def test_indexed_modes_admit_with_generation_binding():
    for mode in ("closure", "proofs", "maintenance-supports", "temporal-closure"):
        for executor in (INDEXED_PYTHON, INDEXED_SCC):
            admitted = admit(generation(), AdmissionRequest(mode, executor, "external-strings-v1"))
            assert admitted.generation_digest == generation().digest
            assert admitted.mode == mode and admitted.executor == executor
            assert admitted.rationale


def test_polars_stays_closure_only_and_dictionary_is_exact():
    admitted = admit(generation(), AdmissionRequest("closure", POLARS_PROTOTYPE, "external-strings-v1"))
    assert admitted.executor == POLARS_PROTOTYPE
    for mode in ("proofs", "maintenance-supports", "temporal-closure"):
        with pytest.raises(ReasoningError):
            admit(generation(), AdmissionRequest(mode, POLARS_PROTOTYPE, "external-strings-v1"))
    with pytest.raises(ReasoningError, match="dictionary"):
        admit(generation(), AdmissionRequest("closure", INDEXED_PYTHON, "other-dictionary"))
    with pytest.raises(ReasoningError, match="dictionary"):
        admit(generation("other-dictionary"), AdmissionRequest("closure", INDEXED_PYTHON, "external-strings-v1"))


def test_unknown_modes_executors_and_types_fail_closed():
    with pytest.raises(ReasoningError, match="mode"):
        AdmissionRequest("streaming", INDEXED_PYTHON, "external-strings-v1")
    with pytest.raises(ReasoningError, match="executor"):
        AdmissionRequest("closure", "native-kernel-v9", "external-strings-v1")
    with pytest.raises(ReasoningError):
        admit(generation(), replace(AdmissionRequest(), mode="closure", executor="native-kernel-v9"))
    with pytest.raises(ReasoningError):
        admit(object(), AdmissionRequest())
    with pytest.raises(ReasoningError):
        admit(generation(), object())
    # Admission binds one exact generation: a changed program digest or
    # source horizon means a different generation, hence a new admission.
    changed = replace(generation(), program_digest="new")
    assert admit(changed, AdmissionRequest()).generation_digest != admit(generation(), AdmissionRequest()).generation_digest
