from __future__ import annotations

import argparse
import random
import tempfile
import time
from collections import defaultdict

import numpy as np

from gestaltdb.graphdb import Edge, Node
from gestaltdb.sampling import SamplerEngine, SamplerSnapshot


class SyntheticStore:
    def __init__(self, nodes, edges):
        self.nodes = {node.get_id_bytes: node for node in nodes}
        self.edges = {edge.get_id_bytes: edge for edge in edges}

    def get_node_keys_generator(self):
        return iter(self.nodes.keys())

    def get_edge_keys_generator(self):
        return iter(self.edges.keys())


class SyntheticGraph:
    def __init__(self, *, num_nodes: int, num_edges: int, num_relations: int, seed: int):
        rng = random.Random(seed)
        node_kinds = ["drug", "protein", "disease", "pathway"]
        nodes = [Node(node_id=f"n{i}", properties={"kind": node_kinds[i % len(node_kinds)]}) for i in range(num_nodes)]
        edges = []
        self.edge_payloads = {}
        self.out_by_node_rel = defaultdict(list)
        for edge_idx in range(num_edges):
            src = rng.randrange(num_nodes)
            dst = rng.randrange(num_nodes - 1)
            if dst >= src:
                dst += 1
            rel = rng.randrange(num_relations)
            relation_name = f"r{rel:06d}"
            edge = Edge(edge_id=f"e{edge_idx}", source=f"n{src}", target=f"n{dst}", properties={"type": relation_name})
            edges.append(edge)
            self.edge_payloads[edge_idx] = (src, dst, relation_name, edge.get_id_bytes)
            self.out_by_node_rel[(src, rel)].append(edge_idx)
        self.store = SyntheticStore(nodes, edges)

    def get_node(self, node_key):
        return self.store.nodes.get(node_key)

    def get_edge(self, edge_key):
        return self.store.edges.get(edge_key)

    def key_to_string(self, key):
        return key.decode("utf-8") if isinstance(key, bytes) else key

    def sample_neighbors(self, node_int: int, rel_int: int, *, sample_size: int, rng: random.Random):
        edge_indices = self.out_by_node_rel.get((node_int, rel_int), [])
        if len(edge_indices) <= sample_size:
            chosen = list(edge_indices)
        else:
            chosen = []
            seen = 0
            for edge_idx in edge_indices:
                seen += 1
                if len(chosen) < sample_size:
                    chosen.append(edge_idx)
                    continue
                replacement_idx = rng.randrange(seen)
                if replacement_idx < sample_size:
                    chosen[replacement_idx] = edge_idx
        # Materialize fresh dict records like GraphDB.iter_typed_adjacency does.
        return [self._record(edge_idx) for edge_idx in chosen]

    def _record(self, edge_idx: int) -> dict[str, object]:
        src, dst, relation_name, edge_id = self.edge_payloads[edge_idx]
        return {
            "edge_id": edge_id,
            "neighbor_id": f"n{dst}".encode("utf-8"),
            "source_id": f"n{src}".encode("utf-8"),
            "target_id": f"n{dst}".encode("utf-8"),
            "edge_type": relation_name,
            "direction": "out",
        }


def time_baseline(graph: SyntheticGraph, nodes: np.ndarray, relations: np.ndarray, *, fanout: int, iterations: int, seed: int) -> tuple[float, int]:
    rng = random.Random(seed)
    sampled = 0
    started = time.perf_counter()
    for _ in range(iterations):
        for node, rel in zip(nodes.tolist(), relations.tolist()):
            sampled += len(graph.sample_neighbors(node, rel, sample_size=fanout, rng=rng))
    return time.perf_counter() - started, sampled


def time_engine(engine: SamplerEngine, nodes: np.ndarray, relations: np.ndarray, *, fanout: int, iterations: int) -> tuple[float, int]:
    sampled = 0
    started = time.perf_counter()
    for _ in range(iterations):
        for rel in np.unique(relations):
            rel_nodes = nodes[relations == rel]
            sampled += int(engine.sample_neighbors(rel_nodes, fanout, direction="out", relations=[int(rel)]).edge_indices.size)
    return time.perf_counter() - started, sampled


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark GraphDB-style sampling against SamplerEngine array sampling.")
    parser.add_argument("--nodes", type=int, default=1_000)
    parser.add_argument("--edges", type=int, default=500_000)
    parser.add_argument("--relations", type=int, default=8)
    parser.add_argument("--batch-nodes", type=int, default=512)
    parser.add_argument("--fanout", type=int, default=15)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    graph = SyntheticGraph(num_nodes=args.nodes, num_edges=args.edges, num_relations=args.relations, seed=args.seed)
    rng = np.random.default_rng(args.seed)
    batch_nodes = rng.integers(args.nodes, size=args.batch_nodes, dtype=np.int64)
    batch_relations = rng.integers(args.relations, size=args.batch_nodes, dtype=np.int64)

    with tempfile.TemporaryDirectory(prefix="gestaltdb-sampler-bench-") as tmpdir:
        snapshot = SamplerSnapshot.build(graph, tmpdir)
        engine = SamplerEngine(snapshot, seed=args.seed)
        baseline_seconds, baseline_sampled = time_baseline(graph, batch_nodes, batch_relations, fanout=args.fanout, iterations=args.iterations, seed=args.seed)
        engine_seconds, engine_sampled = time_engine(engine, batch_nodes, batch_relations, fanout=args.fanout, iterations=args.iterations)

    baseline_rate = baseline_sampled / baseline_seconds if baseline_seconds else 0.0
    engine_rate = engine_sampled / engine_seconds if engine_seconds else 0.0
    speedup = engine_rate / baseline_rate if baseline_rate else float("inf")
    print(f"synthetic graph: nodes={args.nodes:,} edges={args.edges:,} relations={args.relations:,}")
    print(f"workload: batch_nodes={args.batch_nodes:,} fanout={args.fanout:,} iterations={args.iterations:,}")
    print(f"baseline GraphDB-style sampler: {baseline_rate:,.0f} sampled edges/s ({baseline_seconds:.3f}s)")
    print(f"array SamplerEngine:          {engine_rate:,.0f} sampled edges/s ({engine_seconds:.3f}s)")
    print(f"speedup: {speedup:.2f}x")


if __name__ == "__main__":
    main()
