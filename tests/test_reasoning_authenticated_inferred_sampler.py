"""Authenticated input/state, independent artifact reconstruction and immutability."""

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.readview import ProvenanceMismatchError
from gestaltdb.reasoning.adapters.authenticated_inferred_sampler import (
    InferredSnapshotLimits, InferredSnapshotSelection,
    build_authenticated_inferred_snapshot as build, load_authenticated_inferred_snapshot as load,
)
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.adapters.inferred_sampler import build_inferred_snapshot
from gestaltdb.reasoning.adapters.session_generation import bind_session_generation, catalog_for_typed_program
from gestaltdb.reasoning.adapters.sessions import SessionScope, start_session, advance_session
from gestaltdb.reasoning.evidence_ledger import EvidenceLedger
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError, ReasoningCancelledError, ReasoningLimits, Variable
from gestaltdb.reasoning.typed import GenericProgram, GenericRule, TypedAtom
from gestaltdb.sampling.snapshot import SamplerSnapshot


@pytest.fixture(params=[("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def source(tmp_path, request):
    backend, module = request.param
    pytest.importorskip(module)
    graph = GraphDB.create(tmp_path / "db", backend=backend, serializer="json")
    for subject, agent in (("a", "one"), ("a", "two"), ("b", "one")):
        graph.assert_claim(subject=subject, predicate="memberOf", object="g", agent=agent,
                           polarity="positive", valid=(0, 100))
    x, y = Variable("x"), Variable("y")
    program = GenericProgram([GenericRule("r", [TypedAtom("memberOf", [x, y])], [], TypedAtom("person", [x]))])
    session = start_session(graph, program, catalog_for_typed_program(program),
        scope=SessionScope("default", 50, ClaimSelection(), ClaimMapping()), limits=ReasoningLimits())
    binding = bind_session_generation(graph, session)
    yield graph, session, binding, tmp_path / "artifact"
    graph.close()


def pins(binding):
    return dict(expected_generation=binding.identity, expected_session_digest=binding.session_digest)


def bytes_of(path):
    return {p.name: p.read_bytes() for p in path.iterdir()}


def test_exact_build_hydration_legacy_parity_and_new_generation(source):
    graph, session, binding, path = source
    snap = build(graph, session, path, **pins(binding))
    before = bytes_of(path)
    hydrated = load(graph, session, path, **pins(binding))
    assert hydrated.metadata == snap.metadata and bytes_of(path) == before
    legacy = build_inferred_snapshot(session, path.parent / "legacy")
    for field in ("external_node_ids", "external_edge_ids", "external_relation_ids", "src_int", "dst_int", "rel_int", "edge_weights"):
        np.testing.assert_array_equal(getattr(snap, field), getattr(legacy, field))
    meta = hydrated.metadata["authenticated_reasoning_generation"]
    assert meta["format_version"] == 1 and meta["session_digest"] == binding.session_digest
    assert meta["generation"]["source"]["provenance_token"] == binding.provenance.token
    assert meta["scope"]["selection"]["include_derived"] is False
    graph.assert_claim(subject="c", predicate="memberOf", object="g", agent="new", polarity="positive", valid=(0, 100))
    with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
        load(graph, session, path, **pins(binding))
    assert load(graph, session, path, **pins(binding), freshness="historical").num_edges == 2
    moved = advance_session(graph, session, limits=ReasoningLimits()).session
    new_binding = bind_session_generation(graph, moved)
    new = build(graph, moved, path.parent / "new", **pins(new_binding))
    assert new.num_edges == 3 and bytes_of(path) == before
    with pytest.raises(ReasoningError, match="mismatch"):
        load(graph, moved, path, **pins(new_binding))


@pytest.mark.parametrize("dimension", ["database_id", "commit_horizon", "valid_time_us", "provenance_token",
    "program_digest", "catalog_digest", "semantics_profile", "dictionary_id", "world", "ontology_revision",
    "source_policy_id", "mapping_id", "session_digest"])
def test_exact_pins_fail_without_writing(source, dimension):
    graph, session, binding, path = source
    identity = binding.identity
    kwargs = pins(binding)
    good = path.parent / "good"
    build(graph, session, good, **kwargs)
    before = bytes_of(good)
    if dimension == "session_digest":
        kwargs["expected_session_digest"] = "sha256:" + "0" * 64
    elif dimension in identity.source.__dataclass_fields__:
        value = 999 if dimension in ("commit_horizon", "valid_time_us") else "other"
        kwargs["expected_generation"] = replace(identity, source=replace(identity.source, **{dimension: value}))
    elif dimension in identity.context.__dataclass_fields__:
        kwargs["expected_generation"] = replace(identity, context=replace(identity.context, **{dimension: "other"}))
    else:
        kwargs["expected_generation"] = replace(identity, **{dimension: "other"})
    with pytest.raises(ReasoningError):
        build(graph, session, path, **kwargs)
    with pytest.raises(ReasoningError):
        load(graph, session, good, **kwargs)
    assert bytes_of(good) == before
    assert not path.exists()


def test_missing_independent_source_version_is_not_hidden_by_equal_facts(source):
    graph, session, binding, path = source
    removed = session.ledger.versions[0].version_id
    forged = replace(session, ledger=EvidenceLedger(session.ledger.scope_id,
        tuple(v for v in session.ledger.versions if v.version_id != removed)),
        version_logical=tuple(v for v in session.version_logical if v[0] != removed))
    from gestaltdb.reasoning.adapters.session_store import _digest, session_to_dict
    with pytest.raises(ProvenanceMismatchError, match="evidence"):
        build(graph, forged, path, expected_generation=binding.identity,
              expected_session_digest=_digest(session_to_dict(forged)))
    assert not path.exists()


def test_canonical_selection_is_separate_and_exact_on_hydration(source):
    graph, session, binding, path = source
    selection = InferredSnapshotSelection("person", ["other", "memberOf", "memberOf"])
    assert selection == InferredSnapshotSelection("person", ["memberOf", "other"])
    build(graph, session, path, **pins(binding), selection=selection)
    load(graph, session, path, **pins(binding), selection=InferredSnapshotSelection("person", {"other", "memberOf"}))
    with pytest.raises(ReasoningError, match="mismatch"):
        load(graph, session, path, **pins(binding))
    empty = build(graph, session, path.parent / "empty", **pins(binding), selection=InferredSnapshotSelection("unknown"))
    assert empty.num_nodes == empty.num_edges == 0


def resign(path):
    """Attacker updates ALL checksums; source-independent integrity still passes."""
    from gestaltdb.sampling.snapshot import _canonical_json_bytes
    meta = json.loads((path / "metadata.json").read_bytes())
    for name in meta["artifacts"]:
        artifact = path / f"{name}.npy"
        arr = np.load(artifact, allow_pickle=False)
        meta["artifacts"][name] = dict(sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                                       dtype=str(arr.dtype), shape=list(arr.shape))
    raw = _canonical_json_bytes(meta)
    (path / "metadata.json").write_bytes(raw)
    completion = dict(format_version=2, complete=True, metadata_sha256=hashlib.sha256(raw).hexdigest(),
        artifact_catalog_sha256=hashlib.sha256(_canonical_json_bytes(meta["artifacts"])).hexdigest())
    (path / "completion.json").write_bytes(_canonical_json_bytes(completion))


@pytest.mark.parametrize("field", ["edge_weights", "src_int", "out_edge_indices", "node_type_ids", "external_node_ids", "metadata"])
def test_self_consistent_altered_artifacts_fail_independent_reconstruction(source, field):
    graph, session, binding, path = source
    build(graph, session, path, **pins(binding))
    if field == "metadata":
        file = path / "metadata.json"
        meta = json.loads(file.read_bytes())
        meta["authenticated_reasoning_generation"]["scope"]["world"] = "forged"
        file.write_text(json.dumps(meta))
    else:
        file = path / f"{field}.npy"
        arr = np.load(file, allow_pickle=False)
        arr[0] = "z" if field == "external_node_ids" else (0.5 if field == "edge_weights" else 1)
        np.save(file, arr, allow_pickle=False)
    resign(path)
    before = bytes_of(path)
    with pytest.raises(ReasoningError, match="mismatch"):
        load(graph, session, path, **pins(binding))
    assert bytes_of(path) == before


def test_legacy_artifact_is_not_silently_upgraded(source):
    graph, session, binding, path = source
    build_inferred_snapshot(session, path)
    before = bytes_of(path)
    with pytest.raises(ReasoningError, match="mismatch"):
        load(graph, session, path, **pins(binding))
    assert bytes_of(path) == before


@pytest.mark.parametrize("limits", [InferredSnapshotLimits(max_nodes=1), InferredSnapshotLimits(max_edges=1),
                                   InferredSnapshotLimits(max_bytes=1)])
def test_limits_clean_staging_without_installing(source, limits):
    graph, session, binding, path = source
    with pytest.raises(ReasoningLimitError):
        build(graph, session, path, **pins(binding), limits=limits)
    assert not path.exists() and not list(path.parent.glob(".artifact.inferred.*"))


def test_existing_paths_and_cancellation_never_replace_files(source, monkeypatch):
    import gestaltdb.reasoning.adapters.authenticated_inferred_sampler as adapter
    graph, session, binding, path = source
    path.mkdir()
    with pytest.raises(ReasoningError, match="already exists"):
        build(graph, session, path, **pins(binding))
    sentinel = path / "user-file"
    sentinel.write_text("keep")
    with pytest.raises(ReasoningError):
        build(graph, session, path, **pins(binding))
    assert sentinel.read_text() == "keep"
    target = path.parent / "cancel"
    staged = []
    original = adapter._reconstruct
    def ready(*args):
        result = original(*args)
        staged.append(True)
        return result
    monkeypatch.setattr(adapter, "_reconstruct", ready)
    with pytest.raises(ReasoningCancelledError):
        build(graph, session, target, **pins(binding), cancel=lambda: bool(staged))
    assert not target.exists() and not list(path.parent.glob(".cancel.inferred.*"))


def test_source_move_after_staging_fails_before_install(source, monkeypatch):
    import gestaltdb.reasoning.adapters.authenticated_inferred_sampler as adapter
    graph, session, binding, path = source
    original = adapter._reconstruct
    def moved(*args):
        result = original(*args)
        graph.assert_claim(subject="c", predicate="memberOf", object="g", agent="new", polarity="positive", valid=(0, 100))
        return result
    monkeypatch.setattr(adapter, "_reconstruct", moved)
    with pytest.raises(ProvenanceMismatchError, match="horizon moved"):
        build(graph, session, path, **pins(binding))
    assert not path.exists() and not list(path.parent.glob(".artifact.inferred.*"))


def test_competing_builder_waits_and_cannot_replace_first_artifact(source, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import gestaltdb.reasoning.adapters.authenticated_inferred_sampler as adapter
    graph, session, binding, path = source
    entered, release, attempted, finished = Event(), Event(), Event(), Event()
    original = adapter._reconstruct
    def paused(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    def competitor():
        attempted.set()
        try:
            return build(graph, session, path, **pins(binding))
        finally:
            finished.set()
    monkeypatch.setattr(adapter, "_reconstruct", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(build, graph, session, path, **pins(binding))
        try:
            assert entered.wait(5)
            second = pool.submit(competitor)
            assert attempted.wait(5) and not finished.wait(0.05)
        finally:
            release.set()
        first.result(timeout=5)
        before = bytes_of(path)
        with pytest.raises(ReasoningError, match="already exists"):
            second.result(timeout=5)
    assert bytes_of(path) == before


def test_projection_and_hydration_cancellation_are_cooperative(source, monkeypatch):
    from gestaltdb.reasoning.adapters.inferred_sampler import _project_inferred
    import gestaltdb.reasoning.adapters.authenticated_inferred_sampler as adapter
    graph, session, binding, path = source
    seen = []
    def cancel():
        seen.append(True)
        return len(seen) > 2
    with pytest.raises(ReasoningCancelledError):
        _project_inferred(session, "person", ("memberOf",), cancel=cancel)
    build(graph, session, path, **pins(binding))
    before = bytes_of(path)
    staged = []
    original = adapter._reconstruct
    def ready(*args):
        result = original(*args)
        staged.append(True)
        return result
    monkeypatch.setattr(adapter, "_reconstruct", ready)
    with pytest.raises(ReasoningCancelledError):
        load(graph, session, path, **pins(binding), cancel=lambda: bool(staged))
    assert bytes_of(path) == before


@pytest.mark.parametrize("mutation", ["extra", "symlink", "truncated", "bytes"])
def test_noncanonical_or_oversized_artifacts_fail_without_repair(source, mutation):
    graph, session, binding, path = source
    build(graph, session, path, **pins(binding))
    kwargs = {}
    if mutation == "extra":
        (path / "surprise").write_text("not admitted")
    elif mutation == "symlink":
        file = path / "src_int.npy"
        original = path.parent / "src_int.npy"
        file.rename(original)
        file.symlink_to(original)
    elif mutation == "truncated":
        (path / "metadata.json").write_bytes(b"{")
    else:
        kwargs["limits"] = InferredSnapshotLimits(max_bytes=1)
    before = bytes_of(path)
    with pytest.raises(ReasoningError):
        load(graph, session, path, **pins(binding), **kwargs)
    assert bytes_of(path) == before


def test_failed_install_cleans_only_own_stage(source, monkeypatch):
    import gestaltdb.reasoning.adapters.authenticated_inferred_sampler as adapter
    graph, session, binding, path = source
    user_dir = path.parent / ".artifact.inferred.user"
    user_dir.mkdir()
    (user_dir / "keep").write_text("keep")
    def failed(*args):
        raise OSError("rename failed")
    monkeypatch.setattr(adapter.os, "rename", failed)
    with pytest.raises(OSError, match="rename failed"):
        build(graph, session, path, **pins(binding))
    assert not path.exists()
    assert list(path.parent.glob(".artifact.inferred.*")) == [user_dir]
    assert (user_dir / "keep").read_text() == "keep"


@pytest.mark.parametrize("backend,module", [("lmdb", "lmdb"), ("leveldb", "plyvel"), ("pyrex", "pyrex")])
def test_hydration_after_independent_process_restart(tmp_path, backend, module):
    pytest.importorskip(module)
    from gestaltdb.reasoning.adapters.session_store import save_session
    graph = GraphDB.create(tmp_path / "db", backend=backend, serializer="json")
    try:
        x, y = Variable("x"), Variable("y")
        program = GenericProgram([GenericRule("r", [TypedAtom("memberOf", [x, y])], [], TypedAtom("person", [x]))])
        graph.assert_claim(subject="a", predicate="memberOf", object="g", agent="one", polarity="positive", valid=(0, 100))
        session = start_session(graph, program, catalog_for_typed_program(program),
            scope=SessionScope("default", 50, ClaimSelection(), ClaimMapping()), limits=ReasoningLimits())
        binding = bind_session_generation(graph, session)
        save_session(session, tmp_path / "session.json")
        build(graph, session, tmp_path / "artifact", **pins(binding))
    finally:
        graph.close()
    code = '''
import json,sys
from pathlib import Path
from gestaltdb.graphdb import GraphDB
from gestaltdb.reasoning.generation import GenerationIdentity, TemporalSourceIdentity
from gestaltdb.reasoning.protocols import InputContext
from gestaltdb.reasoning.adapters.session_store import load_session
from gestaltdb.reasoning.adapters.authenticated_inferred_sampler import load_authenticated_inferred_snapshot
root=Path(sys.argv[1]); raw=json.loads(sys.argv[2])
raw['source']=TemporalSourceIdentity(**raw['source']); raw['context']=InputContext(**raw['context'])
graph=GraphDB.open(root/'db')
try:
    snapshot=load_authenticated_inferred_snapshot(graph, load_session(root/'session.json'), root/'artifact',
        expected_generation=GenerationIdentity(**raw), expected_session_digest=sys.argv[3])
    assert snapshot.num_edges == 1
finally:
    graph.close()
'''
    done = subprocess.run([sys.executable, "-c", code, str(tmp_path), json.dumps(asdict(binding.identity)), binding.session_digest],
                          capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr


def test_authenticated_inferred_documentation_example():
    pytest.importorskip("lmdb")
    text = (Path(__file__).resolve().parents[1] / "docs/reasoning-authenticated-inferred.rst").read_text()
    blocks = re.findall(r"\.\. code-block:: python\n\n((?:   .*\n|\n)+)", text)
    assert len(blocks) == 1
    exec("\n".join(line[3:] if line.startswith("   ") else line for line in blocks[0].splitlines()), {})


def test_nondefault_source_policy_mapping_and_retraction_remain_exact(source):
    graph, _, _, path = source
    x, y = Variable("x"), Variable("y")
    program = GenericProgram([GenericRule("r", [TypedAtom("link", [x, y])], [], TypedAtom("entity", [x]))])
    scope = SessionScope("default", 50, ClaimSelection(agents=("one",)), ClaimMapping((("memberOf", "link"),)))
    session = start_session(graph, program, catalog_for_typed_program(program), scope=scope, limits=ReasoningLimits())
    binding = bind_session_generation(graph, session)
    selection = InferredSnapshotSelection("entity", ("link",))
    first = build(graph, session, path, **pins(binding), selection=selection)
    metadata = first.metadata["authenticated_reasoning_generation"]
    assert metadata["scope"]["selection"]["agents"] == ["one"]
    assert metadata["scope"]["mapping"]["predicate_aliases"] == [["memberOf", "link"]]
    before = bytes_of(path)
    versions = [v for v in graph.iter_claim_versions() if v.claim is not None and v.claim.subject == "a" and v.claim.agent == "one"]
    assert len(versions) == 1
    v = versions[0]
    graph.retract_claim(v.logical_id, valid=(0, 100), supersedes_version_id=v.version_id)
    advanced = advance_session(graph, session, limits=ReasoningLimits()).session
    new_binding = bind_session_generation(graph, advanced)
    second = build(graph, advanced, path.parent / "after-retraction", **pins(new_binding), selection=selection)
    assert first.num_edges == 2 and second.num_edges == 1
    assert load(graph, session, path, **pins(binding), selection=selection, freshness="historical").num_edges == 2
    assert bytes_of(path) == before


def test_decoder_rebinding_after_reconstruction_fails_before_install(source, monkeypatch):
    import gestaltdb.reasoning.adapters.authenticated_inferred_sampler as adapter
    from gestaltdb.serializers import JSONSerializer
    graph, session, binding, path = source
    original = adapter._reconstruct
    serializer = graph.serializer
    def rebound(*args):
        result = original(*args)
        graph.serializer = JSONSerializer()
        return result
    monkeypatch.setattr(adapter, "_reconstruct", rebound)
    try:
        with pytest.raises((ReasoningError, ProvenanceMismatchError)):
            build(graph, session, path, **pins(binding))
    finally:
        graph.serializer = serializer
    assert not path.exists()
