"""Mandatory real-backend persistence checks; missing extras fail, never skip.

Run only in the fully provisioned release job, not the base-install matrix.
No mocks, import substitution, or optional capability fallback is used here.
The full pytest suite separately exercises transactions, snapshots, native scans,
reasoning sessions/publication, and deliberate corruption/fault injection.
"""

from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

from gestaltdb.graphdb import Edge, GraphDB, Node


def main():
    for module, distribution in (
        ("lmdb", "lmdb"), ("plyvel", "plyvel"), ("pyrex", "pyrex-rocksdb"),
        ("msgpack", "msgpack"), ("google.protobuf", "protobuf"),
    ):
        imported = import_module(module)  # ImportError must fail release CI.
        print(f"{module} {version(distribution)} {imported.__file__}", flush=True)

    for backend in ("lmdb", "leveldb", "pyrex"):
        for serializer in ("pickle", "json", "messagepack", "protobuf"):
            with TemporaryDirectory(prefix="gestaltdb-release-backend-") as tmp:
                path = Path(tmp) / "db"
                graph = GraphDB.create(path, backend=backend, serializer=serializer)
                try:
                    graph.put_node(Node("a", labels=["Person"], properties={"name": "Alice"}))
                    graph.put_node(Node("b", labels=["Person"], properties={"name": "Bob"}))
                    graph.put_edge(Edge("ab", "a", "b", properties={"type": "knows"}))
                    claim = graph.assert_claim(subject="a", predicate="knows", object="b",
                        agent="release-check", polarity="positive", valid=(0, 100))
                    database_id = graph.database_id
                finally:
                    graph.close()

                graph = GraphDB.open(path)
                try:
                    assert graph.database_id == database_id
                    assert graph.get_node(b"a").properties == {"name": "Alice"}
                    assert graph.get_edge(b"ab").properties == {"type": "knows"}
                    visible = tuple(graph.iter_claims_as_of(valid_time=50, predicate="knows"))
                    assert len(visible) == 1 and visible[0].version_id == claim.version_id
                    assert not tuple(graph.iter_claims_as_of(valid_time=100, predicate="knows"))
                    graph.retract_claim(claim.logical_id, valid=(0, 100))
                    assert not tuple(graph.iter_claims_as_of(valid_time=50, predicate="knows"))
                finally:
                    graph.close()

                graph = GraphDB.open(path)
                try:
                    assert not tuple(graph.iter_claims_as_of(valid_time=50, predicate="knows"))
                    assert len(tuple(graph.iter_temporal_commits())) == 2
                finally:
                    graph.close()
            print(f"PASS real persistence: {backend}/{serializer}", flush=True)


if __name__ == "__main__":
    main()
