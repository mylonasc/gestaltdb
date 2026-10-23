"""Logical compatibility is exact and remains distinct from authentication."""

from dataclasses import replace

import pytest

from gestaltdb.reasoning.generation import GenerationIdentity, TemporalSourceIdentity
from gestaltdb.reasoning.model import ReasoningError
from gestaltdb.reasoning.protocols import InputContext
from gestaltdb.reasoning.relational import CompilationIdentity


def identity():
    return GenerationIdentity(TemporalSourceIdentity("db", 1, "token", 50),
                              InputContext("context", "base", "revision", "policy", "mapping"),
                              "program", "catalog", "positive", "dictionary")


@pytest.mark.parametrize("field,value", [("program_digest", "new"), ("catalog_digest", "new"),
                                        ("semantics_profile", "new"), ("dictionary_id", "new")])
def test_every_logical_revision_rejects_reuse(field, value):
    first = identity()
    changed = replace(first, **{field: value})
    assert first.digest != changed.digest
    with pytest.raises(ReasoningError, match=field):
        first.require_compatible(changed)


@pytest.mark.parametrize("field,value", [("context_id", "new"), ("world", "other"),
                                        ("ontology_revision", "new"), ("source_policy_id", "new"),
                                        ("mapping_id", "new")])
def test_context_selection_and_mapping_cannot_be_interchanged(field, value):
    first = identity()
    changed = replace(first, context=replace(first.context, **{field: value}))
    assert first.digest != changed.digest
    with pytest.raises(ReasoningError, match="context"):
        first.require_compatible(changed)


@pytest.mark.parametrize("field,value", [("database_id", "other"), ("commit_horizon", 2),
                                        ("provenance_token", "new"), ("valid_time_us", 51)])
def test_source_mismatch_rejects_reuse(field, value):
    first = identity()
    changed = replace(first, source=replace(first.source, **{field: value}))
    assert first.digest != changed.digest
    with pytest.raises(ReasoningError, match="source"):
        first.require_compatible(changed)


def test_compilation_binding_does_not_choose_an_executor():
    first = identity()
    first.require_compatible(replace(first))
    plan = CompilationIdentity(first.program_digest, first.context, first.dictionary_id,
                               first.semantics_profile, "executor-1")
    first.require_compilation(plan)
    first.require_compilation(replace(plan, executor_version="executor-2"))
    with pytest.raises(ReasoningError, match="dictionary_id"):
        first.require_compilation(replace(plan, dictionary_id="other"))


@pytest.mark.parametrize("field,value", [("commit_horizon", True), ("commit_horizon", -1),
                                        ("valid_time_us", False), ("database_id", "")])
def test_invalid_source_values_fail_early(field, value):
    with pytest.raises(ReasoningError):
        replace(identity().source, **{field: value})
