import importlib.util
import multiprocessing

import pytest

from gestaltdb.graphdb import GraphDB, Node
from gestaltdb.kvstores import LMDBStore, PyRexStore
from gestaltdb.serializers import JSONSerializer


def _transactional_backends():
    backends = []
    if importlib.util.find_spec("lmdb") is not None:
        backends.append(("lmdb", lambda path: LMDBStore(path=str(path))))
    if importlib.util.find_spec("pyrex") is not None:
        import pyrex

        if getattr(pyrex, "has_transactions", False):
            backends.append((
                "pyrex",
                lambda path: PyRexStore(path=str(path), transactional=True),
            ))
    return backends


TRANSACTIONAL_BACKENDS = _transactional_backends() or [
    pytest.param(None, marks=pytest.mark.skip(reason="no transactional backend installed"))
]


def _commit_temporal_versions(path, worker, count, start, results):
    graph = GraphDB.open(path)
    try:
        start.wait()
        commit_ids = []
        for ordinal in range(count):
            version = graph.put_node_version(
                Node(f"worker-{worker}-{ordinal}"), valid=(0, None)
            )
            commit_ids.append(version.commit_id)
        results.put(commit_ids)
    finally:
        graph.close()


@pytest.mark.parametrize(
    "backend",
    TRANSACTIONAL_BACKENDS,
    ids=lambda item: item[0] if item is not None else "none",
)
def test_rolled_back_temporal_reservations_are_not_reused(backend, tmp_path):
    backend_name, store_factory = backend
    graph = GraphDB(store_factory(tmp_path / backend_name), JSONSerializer())
    rolled_back = []
    try:
        with pytest.raises(RuntimeError):
            with graph.transaction() as transaction:
                rolled_back.append(transaction.put_node_version(Node("first"), valid=(0, None)))
                rolled_back.append(transaction.put_node_version(Node("second"), valid=(0, None)))
                raise RuntimeError("rollback")

        committed = graph.put_node_version(Node("committed"), valid=(0, None))
        assert committed.commit_id > max(version.commit_id for version in rolled_back)
        assert list(graph.iter_node_versions("first")) == []
        assert list(graph.iter_node_versions("second")) == []
    finally:
        graph.close()


def test_temporal_writers_are_serialized_across_processes(tmp_path):
    pytest.importorskip("lmdb")
    path = tmp_path / "concurrent-lmdb"
    graph = GraphDB.create(path, backend="lmdb", serializer="json")
    graph.close()

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_commit_temporal_versions,
            args=(path, worker, 4, start, results),
        )
        for worker in range(3)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(timeout=20)
        assert not process.is_alive(), "temporal writer process deadlocked"
        assert process.exitcode == 0

    commit_ids = [commit_id for _ in processes for commit_id in results.get(timeout=2)]
    assert sorted(commit_ids) == list(range(1, 13))

    reopened = GraphDB.open(path)
    try:
        assert [commit.commit_id for commit in reopened.iter_temporal_commits()] == list(range(1, 13))
    finally:
        reopened.close()
