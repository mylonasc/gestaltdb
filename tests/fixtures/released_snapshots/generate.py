"""Regenerate the frozen sampler-snapshot compatibility fixtures.

These artifacts must be produced by *released* wheels, not by the working
tree, so the compatibility test proves that snapshots written by older
releases still load. Run from the repository root with one venv per version:

    uv venv --python 3.12 /tmp/venv-0.10.2
    uv pip install --python /tmp/venv-0.10.2/bin/python 'gestaltdb[lmdb]==0.10.2'
    /tmp/venv-0.10.2/bin/python tests/fixtures/released_snapshots/generate.py

    uv venv --python 3.12 /tmp/venv-0.12.1
    uv pip install --python /tmp/venv-0.12.1/bin/python 'gestaltdb[lmdb]==0.12.1'
    /tmp/venv-0.12.1/bin/python tests/fixtures/released_snapshots/generate.py

The script detects the installed version, writes that version's fixtures, and
records hashes plus the seeded expectations consumed by the loader test.
"""

import hashlib
import json
import platform
import shutil
import sys
from pathlib import Path

import numpy as np
import gestaltdb
from gestaltdb import SamplerEngine, SamplerSnapshot
from gestaltdb.graphdb import Edge, GraphDB, Node

HERE = Path(__file__).resolve().parent
SUPPORTED = {"0.10.2": "dense_v1", "0.12.1": ("dense_v2", "temporal_v2")}


def _result(result):
    return {
        "input_nodes": np.asarray(result.input_nodes).tolist(),
        "edge_indices": np.asarray(result.edge_indices).tolist(),
        "neighbor_nodes": np.asarray(result.neighbor_nodes).tolist(),
    }


def _hashes(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def dense(out):
    snapshot = SamplerSnapshot.from_edge_arrays(
        out,
        external_node_ids=["n0", "n1", "n2", "n3", "n4", "n5"],
        external_edge_ids=["e0", "e1", "e2", "e3", "e4", "e5", "e6", "e7"],
        external_relation_ids=["KNOWS", "WORKS_AT"],
        src_int=np.array([0, 0, 0, 1, 2, 3, 4, 5], dtype=np.int64),
        dst_int=np.array([1, 2, 3, 4, 5, 4, 5, 0], dtype=np.int64),
        rel_int=np.array([0, 0, 1, 0, 1, 0, 0, 1], dtype=np.int64),
        node_type_values=["Person", "Person", "Person", "Person", "Org", "Org"],
    )
    engine = SamplerEngine.load(snapshot.path, seed=7)
    expected = {
        "format_version": snapshot.metadata["format_version"],
        "num_nodes": snapshot.num_nodes,
        "num_edges": snapshot.num_edges,
        "num_relations": snapshot.num_relations,
        "sampled": _result(engine.sample_neighbors([0, 1], fanout=2, direction="out")),
        "relation_filtered": _result(engine.sample_neighbors(
            [0], fanout=2, direction="out", relations=[1])),
    }
    if hasattr(snapshot, "node_int"):
        expected["node_lookup"] = {"n0": snapshot.node_int("n0"), "n5": snapshot.node_int("n5")}
        expected["relation_lookup"] = {
            "KNOWS": snapshot.relation_int("KNOWS"),
            "WORKS_AT": snapshot.relation_int("WORKS_AT"),
        }
    return expected


def temporal(out):
    db = out.parent / "temporal-db"
    graph = GraphDB.create(db, backend="lmdb", serializer="json")
    try:
        for node in [
            Node("n0", labels=["Person"], properties={"kind": "Person"}),
            Node("n1", labels=["Person"], properties={"kind": "Person"}),
            Node("n2", labels=["Person"], properties={"kind": "Person"}),
            Node("n3", labels=["Org"], properties={"kind": "Org"}),
        ]:
            graph.put_node(node)
        graph.put_edge(Edge("e0", "n0", "n1", {"type": "KNOWS"}))
        graph.put_edge(Edge("e1", "n0", "n2", {"type": "KNOWS"}))
        graph.put_edge(Edge("e2", "n0", "n3", {"type": "WORKS_AT"}))

        # e0 is corrected at t=100; e1 spans the window; e2 is bounded.
        graph.put_edge_version(Edge("e0", "n0", "n1", {"type": "KNOWS", "note": "old"}),
                               valid=(0, 100))
        graph.put_edge_version(Edge("e0", "n0", "n1", {"type": "KNOWS", "note": "new"}),
                               valid=(100, 200))
        graph.put_edge_version(Edge("e1", "n0", "n2", {"type": "KNOWS"}), valid=(0, 200))
        graph.put_edge_version(Edge("e2", "n0", "n3", {"type": "WORKS_AT"}), valid=(50, 150))

        snapshot = graph.build_sampler_snapshot(
            out, temporal=True, time_bucket="day", source_db_reference=False,
            source_db={
                "path": "temporal-db",
                "path_type": "relative_to_project",
                "backend": "lmdb",
                "serializer": "json",
            })
        assert snapshot.temporal
        engine = SamplerEngine.load(snapshot.path, seed=7)
        from gestaltdb import TemporalContext

        cases = {}
        for label, context in [
            ("at_50", TemporalContext.as_of(50)),
            ("at_100", TemporalContext.as_of(100)),
            ("at_150", TemporalContext.as_of(150)),
            ("at_200", TemporalContext.as_of(200)),
            ("window_120_160", TemporalContext.during(120, 160)),
        ]:
            result = engine.sample_neighbors(
                [0], fanout=10, direction="out", temporal=context)
            case = _result(result)
            case["edge_version_ids"] = [
                snapshot.edge_version_ids[index] for index in case["edge_indices"]]
            cases[label] = case
        return {
            "format_version": snapshot.metadata["format_version"],
            "temporal": snapshot.temporal,
            "num_nodes": snapshot.num_nodes,
            "num_edge_versions": int(len(snapshot.edge_version_ids)),
            "cases": cases,
        }
    finally:
        graph.close()
        shutil.rmtree(db, ignore_errors=True)


def main():
    version = gestaltdb.__version__ if hasattr(gestaltdb, "__version__") else None
    if version is None:
        from importlib.metadata import version as dist_version
        version = dist_version("gestaltdb")
    if version not in SUPPORTED:
        raise SystemExit(f"unsupported fixture version: {version}")
    fixtures = SUPPORTED[version]
    if isinstance(fixtures, str):
        fixtures = (fixtures,)
    manifest_path = HERE / "expected.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest["python"] = platform.python_version()
    manifest["numpy"] = np.__version__
    manifest.setdefault("fixtures", {})
    for name in fixtures:
        out = HERE / name
        shutil.rmtree(out, ignore_errors=True)
        if name == "dense_v1" or name == "dense_v2":
            expected = dense(out)
        else:
            expected = temporal(out)
        expected["gestaltdb_version"] = version
        expected["files"] = _hashes(out)
        manifest["fixtures"][name] = expected
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest["fixtures"], indent=2, sort_keys=True)[:2000])


if __name__ == "__main__":
    main()
