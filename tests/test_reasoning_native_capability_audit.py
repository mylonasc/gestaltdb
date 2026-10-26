"""Version-qualified raw upstream gates; never infer claim-source admission."""

from importlib.metadata import version

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.reasoning.adapters.native import NativeKeyScan, require_native_scan, _at_least
from gestaltdb.reasoning.adapters.native_claims import PinnedNativeClaimSource
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.extraction import ExtractionCapability, ExtractionRequest
from gestaltdb.reasoning.model import ReasoningError, ReasoningLimitError


@pytest.fixture(params=[False, True], ids=["plain", "transactional"])
def graph(tmp_path, request):
    pytest.importorskip("pyrex")
    pytest.importorskip("pyarrow")
    graph = GraphDB.create(tmp_path / "db", backend="pyrex", serializer="json",
                           backend_options={"transactional": request.param})
    try:
        yield graph
    finally:
        graph.close()


def raw_rows(graph, **kwargs):
    with NativeKeyScan(graph.store, prefix=b"audit:", **kwargs) as scan:
        return tuple(row for batch in scan for row in batch.rows)


def require_audited_runtime(graph):
    if not graph.store.has_native_arrow_scan():
        with pytest.raises(ReasoningError, match="no native Arrow scan"):
            require_native_scan(graph.store)
        pytest.skip("this handle/runtime has no raw native scan; explicit rejection checked")


def test_version_qualified_raw_capability_and_separate_claim_source_gate(graph):
    db, transactional = graph.store.db, graph.store.transactional
    present = hasattr(db, "scan_arrow") and hasattr(db, "create_snapshot")
    capability = ExtractionCapability.probe(graph.store)
    assert graph.store.has_native_arrow_scan() is present
    assert capability.native_arrow_scan is present
    if transactional and version("pyrex-rocksdb") == "0.5.0":
        assert not present and capability.snapshot_kind == "pyrex-transaction-snapshot"
    if _at_least(version("pyrex-rocksdb"), "0.6.0"):
        assert present and capability.snapshot_kind == "pyrex-native-snapshot"
    if present:
        assert require_native_scan(graph.store) is db
        assert "default-column-family" in capability.notes[0]
        db.put(b"audit:a", b"raw")
        assert raw_rows(graph) == ((b"audit:a", b"raw"),)
    else:
        with pytest.raises(ReasoningError, match="no native Arrow scan"):
            require_native_scan(graph.store)
    if transactional:
        with graph.temporal.open_read_view(graph, manifest=graph.manifest, database_id=graph.database_id, valid_time=50) as view:
            with pytest.raises(ReasoningError, match="plain PyRex"):
                PinnedNativeClaimSource(view, request=ExtractionRequest("default"),
                    ontology_revision="audit", selection=ClaimSelection(), mapping=ClaimMapping())


def test_shared_raw_snapshot_isolates_mutations_and_prefix_ranges(graph):
    require_audited_runtime(graph)
    db = graph.store.db
    for key, value in ((b"audit:a", b"a"), (b"audit:b", b"b"), (b"audit:c", b"c")):
        db.put(key, value)
    snapshot = db.create_snapshot()
    try:
        before = raw_rows(graph, snapshot=snapshot, batch_rows=1)
        db.put(b"audit:a", b"changed")
        db.put(b"audit:d", b"new")
        assert raw_rows(graph, snapshot=snapshot) == before
        assert snapshot.is_valid()  # Borrowed scans must not release it.
        assert raw_rows(graph, snapshot=snapshot, start=b"audit:b", stop=b"audit:c") == ((b"audit:b", b"b"),)
        assert raw_rows(graph) != before
    finally:
        snapshot.release()
    with pytest.raises(ReasoningError, match="snapshot"):
        raw_rows(graph, snapshot=snapshot)
    assert before == ((b"audit:a", b"a"), (b"audit:b", b"b"), (b"audit:c", b"c"))


def test_owned_raw_snapshot_and_single_use_lifetimes(graph):
    require_audited_runtime(graph)
    graph.store.db.put(b"audit:a", b"a")
    scan = NativeKeyScan(graph.store, prefix=b"audit:")
    snapshot = scan._snapshot
    assert snapshot.is_valid()
    rows = tuple(row for batch in scan for row in batch.rows)
    assert not snapshot.is_valid()
    with pytest.raises(ReasoningError, match="single-use"):
        list(scan)
    scan.close()
    assert rows == ((b"audit:a", b"a"),)
    scan = NativeKeyScan(graph.store, prefix=b"audit:")
    snapshot = scan._snapshot
    scan.close()
    assert not snapshot.is_valid()


def test_raw_row_limits_fail_without_success_or_poisoning(graph):
    require_audited_runtime(graph)
    graph.store.db.put(b"audit:a", b"a")
    graph.store.db.put(b"audit:b", b"b")
    scan = NativeKeyScan(graph.store, prefix=b"audit:", max_rows=1)
    snapshot = scan._snapshot
    with pytest.raises(ReasoningLimitError, match="max_rows"):
        list(scan)
    assert not snapshot.is_valid()
    assert len(raw_rows(graph)) == 2


def test_transaction_scan_observes_committed_default_family_only(graph):
    if not graph.store.transactional:
        pytest.skip("transaction visibility is a TransactionDB-only gate")
    require_audited_runtime(graph)
    db = graph.store.db
    db.put(b"audit:a", b"before")
    family = db.create_column_family("audit-private")
    db.put_cf(family, b"audit:hidden", b"private")
    # Named family handles are not databases or silently coerced to default CF.
    assert not hasattr(family, "scan_arrow") and not hasattr(family, "create_snapshot")
    with pytest.raises(ReasoningError, match="no native Arrow scan"):
        require_native_scan(family)
    snapshot = db.create_snapshot()
    transaction = db.begin_transaction()
    try:
        transaction.put(b"audit:a", b"after")
        transaction.put(b"audit:pending", b"pending")
        assert raw_rows(graph) == ((b"audit:a", b"before"),)
        transaction.commit()
        assert raw_rows(graph, snapshot=snapshot) == ((b"audit:a", b"before"),)
        assert raw_rows(graph) == ((b"audit:a", b"after"), (b"audit:pending", b"pending"))
    finally:
        snapshot.release()
    rollback = db.begin_transaction()
    rollback.put(b"audit:rolled-back", b"hidden")
    rollback.rollback()
    assert all(key not in (b"audit:hidden", b"audit:rolled-back") for key, _ in raw_rows(graph))
