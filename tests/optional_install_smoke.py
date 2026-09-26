import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def main() -> None:
    mode = sys.argv[1]
    if mode == "core":
        import gestaltdb
        import gestaltdb.kvstores
        import gestaltdb.serializers

        assert gestaltdb is not None
        for package in ("lmdb", "plyvel", "pyrex", "msgpack", "google.protobuf"):
            try:
                spec = importlib.util.find_spec(package)
            except ModuleNotFoundError:
                spec = None
            assert spec is None, package
        return

    backend, serializer = mode.split("+")
    from gestaltdb.graphdb import GraphDB, Node

    with TemporaryDirectory() as tmpdir:
        graph = GraphDB.create(Path(tmpdir) / "graph", backend=backend, serializer=serializer)
        try:
            graph.put_node(Node("node-1", properties={"value": 1}))
            assert graph.get_node(b"node-1").properties == {"value": 1}
        finally:
            graph.close()


if __name__ == "__main__":
    main()
