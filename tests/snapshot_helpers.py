"""Small-fixture legacy encoding and independent integrity resealing helpers."""

import hashlib
import json

import numpy as np


def reseal(path, metadata=None):
    if metadata is None:
        metadata = json.loads((path / "metadata.json").read_text())
    catalog = {}
    for name in metadata["artifacts"]:
        file = path / f"{name}.npy"
        array = np.load(file, allow_pickle=False)
        catalog[name] = {"sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                         "dtype": str(array.dtype), "shape": list(array.shape)}
    metadata["artifacts"] = catalog
    def encode(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    raw = encode(metadata)
    (path / "metadata.json").write_bytes(raw)
    (path / "completion.json").write_bytes(encode({
        "format_version": 2, "complete": True, "metadata_sha256": hashlib.sha256(raw).hexdigest(),
        "artifact_catalog_sha256": hashlib.sha256(encode(catalog)).hexdigest(),
    }))


def dense_snapshot(path):
    """Convert the four small relation directories to historical dense encoding."""
    metadata = json.loads((path / "metadata.json").read_text())
    size = max(1, metadata["node_count"] * max(1, metadata["relation_count"]))
    for name in ("relation_out", "relation_in", "temporal_out", "temporal_in"):
        file = path / f"{name}_row_keys.npy"
        if not file.exists():
            continue
        rows = np.load(file)
        indptr = np.load(path / f"{name}_indptr.npy")
        counts = np.zeros(size, dtype=np.int64)
        counts[rows] = np.diff(indptr)
        np.save(path / f"{name}_indptr.npy", np.concatenate(([0], np.cumsum(counts))))
        file.unlink()
        metadata["artifacts"].pop(f"{name}_row_keys")
    metadata.pop("relation_adjacency_encoding", None)
    reseal(path, metadata)
