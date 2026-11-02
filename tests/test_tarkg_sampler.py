from pathlib import Path

import numpy as np
import pytest


@pytest.mark.parametrize("relative_paths", [False, True], ids=["absolute", "relative"])
def test_tarkg_sampler_first_build_records_portable_sources(tmp_path, monkeypatch, relative_paths):
    pl = pytest.importorskip("polars")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from example.gnn_kg.config import AppConfig
    from example.gnn_kg.sampler import SamplerEngineKGNeighborhoodSampler

    monkeypatch.chdir(tmp_path)
    root = Path("data") if relative_paths else tmp_path / "data"
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True)
    (root / "db").mkdir()
    cfg = AppConfig()
    cfg.paths.db_path = root / "db"
    cfg.paths.sampler_snapshot_path = artifacts / "snapshot"
    artifact_names = ["edge_arrays", "node_map", "relation_map", "node_info", "node_mappings", "edge_mappings"]
    for name in artifact_names:
        suffix = ".npz" if name == "edge_arrays" else ".parquet"
        setattr(cfg.paths, f"{name}_path", artifacts / f"{name}{suffix}")
    np.savez(
        cfg.paths.edge_arrays_path,
        edge_id=["ab"], src=[0], dst=[1], rel=[0],
        node1_type=["drug"], node2_type=["protein"],
    )
    pl.DataFrame({"node_int": [1, 0], "node_id": ["b", "a"], "kind": ["protein", "drug"]}).write_parquet(cfg.paths.node_map_path)
    pl.DataFrame({"rel_int": [0], "edge_type": ["R"]}).write_parquet(cfg.paths.relation_map_path)

    sampler = SamplerEngineKGNeighborhoodSampler(cfg)
    snapshot = sampler.snapshot
    assert snapshot.source_graph_exists()
    assert snapshot.metadata["source_db"]["path_type"] == "relative_to_snapshot"
    assert Path(snapshot.metadata["source_db"]["path"]) == Path("../../db")
    assert snapshot.external_node_id(0) == "a"
    assert snapshot.external_edge_id(0) == "ab"
    for name in artifact_names:
        assert (snapshot.path / snapshot.metadata["source_artifacts"][name]).resolve() == getattr(cfg.paths, f"{name}_path").resolve()

    # Existing snapshots should load without touching the input artifact files.
    cfg.paths.edge_arrays_path.unlink()
    loaded = SamplerEngineKGNeighborhoodSampler(cfg, mode="memmap").snapshot
    assert loaded.metadata == snapshot.metadata
    assert loaded.external_relation_id(0) == "R"
