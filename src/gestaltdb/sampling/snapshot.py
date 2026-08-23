"""Sampler snapshot build/load support."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from .adjacency import CSRAdjacency, build_csr


SNAPSHOT_FORMAT_VERSION = 1


@dataclass(slots=True)
class SamplerSnapshot:
    """Read-optimized array snapshot used by ``SamplerEngine``.

    A sampler snapshot is a derived, immutable index over a graph. It stores
    compact integer node IDs, compact integer relation IDs, edge endpoint arrays,
    CSR adjacency indexes, and exact positive triples. The durable graph database
    remains the source of truth; this object is optimized for training-time
    randomized sampling.

    Attributes:
        path: Directory containing the persisted snapshot arrays and metadata.
        metadata: JSON-compatible snapshot metadata.
        external_node_ids: External node identifiers ordered by compact node ID.
        node_type_ids: Integer node type ID per compact node, or ``-1`` when
            unknown.
        external_edge_ids: External edge identifiers ordered by compact edge ID.
        external_relation_ids: External relation identifiers ordered by compact
            relation ID.
        relation_src_type_ids: Expected source node type ID per relation, or
            ``-1`` when the source type is heterogeneous or unknown.
        relation_dst_type_ids: Expected target node type ID per relation, or
            ``-1`` when the target type is heterogeneous or unknown.
        src_int: Source compact node ID for each edge.
        dst_int: Target compact node ID for each edge.
        rel_int: Relation compact ID for each edge.
        out: Source-to-edge CSR adjacency.
        in_: Target-to-edge CSR adjacency.
        incident: Undirected incident edge CSR adjacency.
        relation_out: Relation-grouped source-to-edge CSR adjacency using keys
            ``node_id * num_relations + relation_id``.
        relation_in: Relation-grouped target-to-edge CSR adjacency using keys
            ``node_id * num_relations + relation_id``.
        positive_triples: Array of ``(src, rel, dst)`` positives.

    Examples:
        Build from already compact arrays and sample with ``SamplerEngine``::

            snapshot = SamplerSnapshot.from_edge_arrays(
                "snapshot",
                external_node_ids=["drug-1", "protein-1"],
                external_edge_ids=["edge-1"],
                external_relation_ids=["binds"],
                src_int=[0],
                dst_int=[1],
                rel_int=[0],
                node_type_values=["drug", "protein"],
            )
    """

    path: Path
    metadata: dict
    external_node_ids: np.ndarray
    node_type_ids: np.ndarray
    external_edge_ids: np.ndarray
    external_relation_ids: np.ndarray
    relation_src_type_ids: np.ndarray
    relation_dst_type_ids: np.ndarray
    src_int: np.ndarray
    dst_int: np.ndarray
    rel_int: np.ndarray
    out: CSRAdjacency
    in_: CSRAdjacency
    incident: CSRAdjacency
    relation_out: CSRAdjacency
    relation_in: CSRAdjacency
    positive_triples: np.ndarray

    @property
    def num_nodes(self) -> int:
        return int(self.external_node_ids.size)

    @property
    def num_edges(self) -> int:
        return int(self.src_int.size)

    @property
    def num_relations(self) -> int:
        return int(self.external_relation_ids.size)

    @classmethod
    def build(
        cls,
        graph,
        output_path: str | Path,
        *,
        node_filter: Callable[[object], bool] | None = None,
        edge_filter: Callable[[object], bool] | None = None,
        edge_type_property: str = "type",
        node_type_property: str = "kind",
        directed: bool = True,
        include_reverse: bool = True,
        reverse_relation_policy: str = "adjacency_only",
        storage: str = "npy",
        layout: str = "csr",
    ) -> "SamplerSnapshot":
        """Build and persist a static sampler snapshot from a ``GraphDB``.

        Args:
            graph: GraphDB-like object exposing ``store.get_node_keys_generator``,
                ``store.get_edge_keys_generator``, ``get_node``, ``get_edge``, and
                ``key_to_string``.
            output_path: Directory where snapshot metadata and ``.npy`` arrays
                are written.
            node_filter: Optional predicate receiving each node object. Nodes for
                which the predicate returns ``False`` are omitted.
            edge_filter: Optional predicate receiving each edge object. Edges for
                which the predicate returns ``False`` are omitted.
            edge_type_property: Edge property containing the relation identifier.
            node_type_property: Node property containing the semantic node type.
            directed: Stored in metadata for consumers; current adjacency arrays
                preserve directed source/target endpoints and also build an
                incident view.
            include_reverse: Stored in metadata for consumers. Reverse synthetic
                relations are not currently materialized.
            reverse_relation_policy: Metadata policy for reverse traversal. Must
                be ``"adjacency_only"``, ``"synthetic_relations"``, or ``"none"``.
            storage: Metadata storage hint. Arrays are currently written as
                ``.npy`` files.
            layout: Adjacency layout. Only ``"csr"`` is currently supported.

        Returns:
            Loaded ``SamplerSnapshot`` pointing at ``output_path``.

        Raises:
            ValueError: If ``reverse_relation_policy`` or ``layout`` is invalid.

        Examples:
            Build a snapshot from a graph database::

                snapshot = graph.build_sampler_snapshot(
                    "data/sampler",
                    edge_type_property="type",
                    node_type_property="kind",
                )
        """
        if reverse_relation_policy not in {"adjacency_only", "synthetic_relations", "none"}:
            raise ValueError("reverse_relation_policy must be 'adjacency_only', 'synthetic_relations', or 'none'")
        if layout != "csr":
            raise ValueError("only layout='csr' is currently supported")

        path = Path(output_path)
        path.mkdir(parents=True, exist_ok=True)

        node_ids: list[str] = []
        node_types: list[object] = []
        for node_key in graph.store.get_node_keys_generator():
            node = graph.get_node(node_key)
            if node is None or (node_filter is not None and not node_filter(node)):
                continue
            node_ids.append(str(node.get_id))
            node_types.append(node.properties.get(node_type_property))

        node_to_int = {node_id: idx for idx, node_id in enumerate(node_ids)}
        type_values = sorted({str(value) for value in node_types if value is not None})
        type_to_int = {value: idx for idx, value in enumerate(type_values)}
        node_type_ids = np.asarray([type_to_int.get(str(value), -1) if value is not None else -1 for value in node_types], dtype=np.int64)

        edge_ids: list[str] = []
        src_values: list[int] = []
        dst_values: list[int] = []
        rel_values: list[str] = []
        for edge_key in graph.store.get_edge_keys_generator():
            edge = graph.get_edge(edge_key)
            if edge is None or (edge_filter is not None and not edge_filter(edge)):
                continue
            src = graph.key_to_string(edge.source)
            dst = graph.key_to_string(edge.target)
            if src not in node_to_int or dst not in node_to_int:
                continue
            relation = edge.properties.get(edge_type_property)
            if relation is None:
                continue
            edge_ids.append(str(edge.get_id))
            src_values.append(node_to_int[src])
            dst_values.append(node_to_int[dst])
            rel_values.append(str(relation))

        relations = sorted(set(rel_values))
        rel_to_int = {relation: idx for idx, relation in enumerate(relations)}
        src_int = np.asarray(src_values, dtype=np.int64)
        dst_int = np.asarray(dst_values, dtype=np.int64)
        rel_int = np.asarray([rel_to_int[relation] for relation in rel_values], dtype=np.int64)
        relation_src_type_ids = np.full(len(relations), -1, dtype=np.int64)
        relation_dst_type_ids = np.full(len(relations), -1, dtype=np.int64)
        for relation, rel_idx in rel_to_int.items():
            rel_mask = rel_int == rel_idx
            src_types = set(node_type_ids[src_int[rel_mask]].astype(np.int64).tolist()) if src_int.size else set()
            dst_types = set(node_type_ids[dst_int[rel_mask]].astype(np.int64).tolist()) if dst_int.size else set()
            relation_src_type_ids[rel_idx] = src_types.pop() if len(src_types) == 1 else -1
            relation_dst_type_ids[rel_idx] = dst_types.pop() if len(dst_types) == 1 else -1
        edge_indices = np.arange(src_int.size, dtype=np.int64)

        out = build_csr(src_int, edge_indices, len(node_ids))
        in_ = build_csr(dst_int, edge_indices, len(node_ids))
        incident = build_csr(
            np.concatenate([src_int, dst_int]) if src_int.size else np.empty(0, dtype=np.int64),
            np.concatenate([edge_indices, edge_indices]) if edge_indices.size else np.empty(0, dtype=np.int64),
            len(node_ids),
        )
        relation_size = max(1, len(node_ids) * max(1, len(relations)))
        relation_out = build_csr(src_int * max(1, len(relations)) + rel_int, edge_indices, relation_size)
        relation_in = build_csr(dst_int * max(1, len(relations)) + rel_int, edge_indices, relation_size)
        positive_triples = np.stack([src_int, rel_int, dst_int], axis=1) if src_int.size else np.empty((0, 3), dtype=np.int64)

        metadata = {
            "format_version": SNAPSHOT_FORMAT_VERSION,
            "node_count": len(node_ids),
            "edge_count": int(src_int.size),
            "relation_count": len(relations),
            "node_type_count": len(type_values),
            "edge_type_property": edge_type_property,
            "node_type_property": node_type_property,
            "directed": directed,
            "include_reverse": include_reverse,
            "reverse_relation_policy": reverse_relation_policy,
            "storage": storage,
            "layout": layout,
            "dtypes": {"ids": "str", "indices": "int64"},
        }

        cls._write(path, metadata, node_ids, node_type_ids, edge_ids, relations, relation_src_type_ids, relation_dst_type_ids, src_int, dst_int, rel_int, out, in_, incident, relation_out, relation_in, positive_triples)
        return cls.load(path)

    @classmethod
    def from_edge_arrays(
        cls,
        output_path: str | Path,
        *,
        external_node_ids: Sequence[object],
        external_edge_ids: Sequence[object],
        external_relation_ids: Sequence[object],
        src_int,
        dst_int,
        rel_int,
        node_type_values: Sequence[object] | None = None,
        node_type_ids=None,
        edge_src_type_values: Sequence[object] | None = None,
        edge_dst_type_values: Sequence[object] | None = None,
        relation_src_type_ids=None,
        relation_dst_type_ids=None,
        metadata: dict | None = None,
    ) -> "SamplerSnapshot":
        """Build and persist a snapshot from generic edge arrays.

        This is the preferred API for pipelines that already have compact graph
        arrays, such as parquet/Arrow/NumPy preprocessing jobs. The method is
        deliberately free of pandas, Polars, Arrow, or database dependencies: the
        caller loads data with whichever tool is appropriate, then passes plain
        array-like values.

        Exactly one of ``node_type_values`` or ``node_type_ids`` may be provided.
        If ``node_type_values`` is provided, values are deterministically encoded
        to integer IDs sorted by their string representation, with missing values
        encoded as ``-1``. If edge endpoint type values are provided, they are
        used to derive relation-compatible source and target type constraints.
        Otherwise, relation endpoint type constraints are inferred from the node
        type IDs of the actual edge endpoints.

        Args:
            output_path: Directory where the snapshot is written.
            external_node_ids: External node IDs ordered by compact node ID.
            external_edge_ids: External edge IDs ordered by compact edge ID.
            external_relation_ids: External relation IDs ordered by compact
                relation ID.
            src_int: Source compact node ID per edge.
            dst_int: Target compact node ID per edge.
            rel_int: Relation compact ID per edge.
            node_type_values: Optional semantic node type values ordered by
                compact node ID. Values are encoded to integer IDs.
            node_type_ids: Optional already encoded node type IDs ordered by
                compact node ID.
            edge_src_type_values: Optional semantic source endpoint type value per
                edge, used to derive relation source endpoint constraints.
            edge_dst_type_values: Optional semantic target endpoint type value per
                edge, used to derive relation target endpoint constraints.
            relation_src_type_ids: Optional precomputed relation source type IDs.
            relation_dst_type_ids: Optional precomputed relation target type IDs.
            metadata: Optional JSON-compatible metadata merged into the generated
                snapshot metadata.

        Returns:
            Loaded ``SamplerSnapshot``.

        Raises:
            ValueError: If array lengths are inconsistent, compact IDs are out of
                range, or both ``node_type_values`` and ``node_type_ids`` are
                provided.

        Examples:
            Build from NumPy arrays without any dataframe dependency::

                snapshot = SamplerSnapshot.from_edge_arrays(
                    "snapshot",
                    external_node_ids=["n0", "n1", "n2"],
                    external_edge_ids=["e0", "e1"],
                    external_relation_ids=["r0"],
                    src_int=np.array([0, 1]),
                    dst_int=np.array([1, 2]),
                    rel_int=np.array([0, 0]),
                    node_type_values=["drug", "protein", "protein"],
                )
        """
        if node_type_values is not None and node_type_ids is not None:
            raise ValueError("provide either node_type_values or node_type_ids, not both")
        external_node_ids = np.asarray(external_node_ids, dtype=str)
        external_edge_ids = np.asarray(external_edge_ids, dtype=str)
        external_relation_ids = np.asarray(external_relation_ids, dtype=str)
        src_int = np.asarray(src_int, dtype=np.int64)
        dst_int = np.asarray(dst_int, dtype=np.int64)
        rel_int = np.asarray(rel_int, dtype=np.int64)
        cls._validate_edge_arrays(external_node_ids, external_edge_ids, external_relation_ids, src_int, dst_int, rel_int)

        type_mapping = None
        if node_type_values is not None:
            node_type_ids, type_mapping = _encode_type_values(node_type_values, expected_size=external_node_ids.size)
        elif node_type_ids is None:
            node_type_ids = np.full(external_node_ids.size, -1, dtype=np.int64)
        else:
            node_type_ids = np.asarray(node_type_ids, dtype=np.int64)
            if node_type_ids.size != external_node_ids.size:
                raise ValueError("node_type_ids length must match external_node_ids")

        if relation_src_type_ids is None or relation_dst_type_ids is None:
            relation_src_type_ids, relation_dst_type_ids = cls._derive_relation_endpoint_types(
                node_type_ids,
                src_int,
                dst_int,
                rel_int,
                external_relation_ids.size,
                node_type_mapping=type_mapping,
                edge_src_type_values=edge_src_type_values,
                edge_dst_type_values=edge_dst_type_values,
            )

        snapshot_metadata = dict(metadata or {})
        if type_mapping is not None:
            snapshot_metadata.setdefault("node_type_values", {str(idx): value for value, idx in type_mapping.items()})
        return cls.from_arrays(
            output_path,
            external_node_ids=external_node_ids,
            node_type_ids=node_type_ids,
            external_edge_ids=external_edge_ids,
            external_relation_ids=external_relation_ids,
            src_int=src_int,
            dst_int=dst_int,
            rel_int=rel_int,
            relation_src_type_ids=relation_src_type_ids,
            relation_dst_type_ids=relation_dst_type_ids,
            metadata=snapshot_metadata,
        )

    @classmethod
    def from_arrays(
        cls,
        output_path: str | Path,
        *,
        external_node_ids,
        node_type_ids,
        external_edge_ids,
        external_relation_ids,
        src_int,
        dst_int,
        rel_int,
        relation_src_type_ids=None,
        relation_dst_type_ids=None,
        metadata: dict | None = None,
    ) -> "SamplerSnapshot":
        """Build and persist a snapshot from fully encoded compact arrays.

        Args:
            output_path: Directory where the snapshot is written.
            external_node_ids: External node IDs ordered by compact node ID.
            node_type_ids: Integer node type ID per compact node.
            external_edge_ids: External edge IDs ordered by compact edge ID.
            external_relation_ids: External relation IDs ordered by compact
                relation ID.
            src_int: Source compact node ID per edge.
            dst_int: Target compact node ID per edge.
            rel_int: Relation compact ID per edge.
            relation_src_type_ids: Optional source endpoint type ID per relation.
            relation_dst_type_ids: Optional target endpoint type ID per relation.
            metadata: Optional metadata merged into generated metadata.

        Returns:
            Loaded ``SamplerSnapshot``.

        Examples:
            Use this lower-level API when all type IDs are already encoded::

                snapshot = SamplerSnapshot.from_arrays(
                    "snapshot",
                    external_node_ids=["n0", "n1"],
                    node_type_ids=[0, 1],
                    external_edge_ids=["e0"],
                    external_relation_ids=["r0"],
                    src_int=[0],
                    dst_int=[1],
                    rel_int=[0],
                )
        """
        path = Path(output_path)
        path.mkdir(parents=True, exist_ok=True)
        external_node_ids = np.asarray(external_node_ids, dtype=str)
        node_type_ids = np.asarray(node_type_ids, dtype=np.int64)
        external_edge_ids = np.asarray(external_edge_ids, dtype=str)
        external_relation_ids = np.asarray(external_relation_ids, dtype=str)
        src_int = np.asarray(src_int, dtype=np.int64)
        dst_int = np.asarray(dst_int, dtype=np.int64)
        rel_int = np.asarray(rel_int, dtype=np.int64)
        edge_indices = np.arange(src_int.size, dtype=np.int64)

        relation_count = int(external_relation_ids.size)
        if relation_src_type_ids is None or relation_dst_type_ids is None:
            relation_src_type_ids = np.full(relation_count, -1, dtype=np.int64)
            relation_dst_type_ids = np.full(relation_count, -1, dtype=np.int64)
            for rel_idx in range(relation_count):
                rel_mask = rel_int == rel_idx
                src_types = set(node_type_ids[src_int[rel_mask]].astype(np.int64).tolist()) if src_int.size else set()
                dst_types = set(node_type_ids[dst_int[rel_mask]].astype(np.int64).tolist()) if dst_int.size else set()
                relation_src_type_ids[rel_idx] = src_types.pop() if len(src_types) == 1 else -1
                relation_dst_type_ids[rel_idx] = dst_types.pop() if len(dst_types) == 1 else -1
        else:
            relation_src_type_ids = np.asarray(relation_src_type_ids, dtype=np.int64)
            relation_dst_type_ids = np.asarray(relation_dst_type_ids, dtype=np.int64)

        out = build_csr(src_int, edge_indices, external_node_ids.size)
        in_ = build_csr(dst_int, edge_indices, external_node_ids.size)
        incident = build_csr(
            np.concatenate([src_int, dst_int]) if src_int.size else np.empty(0, dtype=np.int64),
            np.concatenate([edge_indices, edge_indices]) if edge_indices.size else np.empty(0, dtype=np.int64),
            external_node_ids.size,
        )
        relation_size = max(1, external_node_ids.size * max(1, relation_count))
        relation_out = build_csr(src_int * max(1, relation_count) + rel_int, edge_indices, relation_size)
        relation_in = build_csr(dst_int * max(1, relation_count) + rel_int, edge_indices, relation_size)
        positive_triples = np.stack([src_int, rel_int, dst_int], axis=1) if src_int.size else np.empty((0, 3), dtype=np.int64)
        snapshot_metadata = {
            "format_version": SNAPSHOT_FORMAT_VERSION,
            "node_count": int(external_node_ids.size),
            "edge_count": int(src_int.size),
            "relation_count": relation_count,
            "node_type_count": int(len(set(node_type_ids.tolist()))),
            "storage": "npy",
            "layout": "csr",
            "dtypes": {"ids": "str", "indices": "int64"},
        }
        if metadata:
            snapshot_metadata.update(metadata)
        cls._write(path, snapshot_metadata, external_node_ids, node_type_ids, external_edge_ids, external_relation_ids, relation_src_type_ids, relation_dst_type_ids, src_int, dst_int, rel_int, out, in_, incident, relation_out, relation_in, positive_triples)
        return cls.load(path)

    @classmethod
    def load(cls, path: str | Path, *, mmap: bool = False) -> "SamplerSnapshot":
        """Load a sampler snapshot from disk.

        Args:
            path: Snapshot directory containing ``metadata.json`` and array files.
            mmap: If ``True``, arrays are loaded using NumPy memory mapping.

        Returns:
            Loaded ``SamplerSnapshot``.

        Raises:
            ValueError: If the snapshot format version is unsupported.

        Examples:
            Load eagerly into RAM::

                snapshot = SamplerSnapshot.load("snapshot")

            Load arrays through memory maps::

                snapshot = SamplerSnapshot.load("snapshot", mmap=True)
        """
        path = Path(path)
        with (path / "metadata.json").open("r", encoding="utf-8") as handle:
            metadata = json.load(handle)
        if metadata.get("format_version") != SNAPSHOT_FORMAT_VERSION:
            raise ValueError("unsupported sampler snapshot format version")
        mmap_mode = "r" if mmap else None

        def load_array(name: str) -> np.ndarray:
            return np.load(path / f"{name}.npy", mmap_mode=mmap_mode, allow_pickle=False)

        return cls(
            path=path,
            metadata=metadata,
            external_node_ids=load_array("external_node_ids"),
            node_type_ids=load_array("node_type_ids"),
            external_edge_ids=load_array("external_edge_ids"),
            external_relation_ids=load_array("external_relation_ids"),
            relation_src_type_ids=load_array("relation_src_type_ids"),
            relation_dst_type_ids=load_array("relation_dst_type_ids"),
            src_int=load_array("src_int"),
            dst_int=load_array("dst_int"),
            rel_int=load_array("rel_int"),
            out=CSRAdjacency(load_array("out_indptr"), load_array("out_edge_indices")),
            in_=CSRAdjacency(load_array("in_indptr"), load_array("in_edge_indices")),
            incident=CSRAdjacency(load_array("incident_indptr"), load_array("incident_edge_indices")),
            relation_out=CSRAdjacency(load_array("relation_out_indptr"), load_array("relation_out_edge_indices")),
            relation_in=CSRAdjacency(load_array("relation_in_indptr"), load_array("relation_in_edge_indices")),
            positive_triples=load_array("positive_triples"),
        )

    @staticmethod
    def _validate_edge_arrays(external_node_ids, external_edge_ids, external_relation_ids, src_int, dst_int, rel_int) -> None:
        if not (src_int.size == dst_int.size == rel_int.size == external_edge_ids.size):
            raise ValueError("src_int, dst_int, rel_int, and external_edge_ids must have the same length")
        if src_int.size == 0:
            return
        if src_int.min() < 0 or dst_int.min() < 0 or rel_int.min() < 0:
            raise ValueError("compact IDs must be non-negative")
        if src_int.max() >= external_node_ids.size or dst_int.max() >= external_node_ids.size:
            raise ValueError("src_int and dst_int must reference external_node_ids")
        if rel_int.max() >= external_relation_ids.size:
            raise ValueError("rel_int must reference external_relation_ids")

    @staticmethod
    def _derive_relation_endpoint_types(node_type_ids, src_int, dst_int, rel_int, relation_count, *, node_type_mapping=None, edge_src_type_values=None, edge_dst_type_values=None):
        relation_src_type_ids = np.full(relation_count, -1, dtype=np.int64)
        relation_dst_type_ids = np.full(relation_count, -1, dtype=np.int64)
        src_edge_types = _encode_with_mapping(edge_src_type_values, node_type_mapping) if edge_src_type_values is not None and node_type_mapping is not None else None
        dst_edge_types = _encode_with_mapping(edge_dst_type_values, node_type_mapping) if edge_dst_type_values is not None and node_type_mapping is not None else None
        for rel_idx in range(relation_count):
            rel_mask = rel_int == rel_idx
            if src_edge_types is not None:
                src_types = set(src_edge_types[rel_mask].astype(np.int64).tolist())
            else:
                src_types = set(node_type_ids[src_int[rel_mask]].astype(np.int64).tolist()) if src_int.size else set()
            if dst_edge_types is not None:
                dst_types = set(dst_edge_types[rel_mask].astype(np.int64).tolist())
            else:
                dst_types = set(node_type_ids[dst_int[rel_mask]].astype(np.int64).tolist()) if dst_int.size else set()
            relation_src_type_ids[rel_idx] = src_types.pop() if len(src_types) == 1 else -1
            relation_dst_type_ids[rel_idx] = dst_types.pop() if len(dst_types) == 1 else -1
        return relation_src_type_ids, relation_dst_type_ids

    @staticmethod
    def _write(path: Path, metadata: dict, node_ids, node_type_ids, edge_ids, relations, relation_src_type_ids, relation_dst_type_ids, src_int, dst_int, rel_int, out, in_, incident, relation_out, relation_in, positive_triples) -> None:
        with (path / "metadata.json").open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, indent=2, sort_keys=True)
        arrays = {
            "external_node_ids": np.asarray(node_ids, dtype=str),
            "node_type_ids": node_type_ids,
            "external_edge_ids": np.asarray(edge_ids, dtype=str),
            "external_relation_ids": np.asarray(relations, dtype=str),
            "relation_src_type_ids": relation_src_type_ids,
            "relation_dst_type_ids": relation_dst_type_ids,
            "src_int": src_int,
            "dst_int": dst_int,
            "rel_int": rel_int,
            "out_indptr": out.indptr,
            "out_edge_indices": out.edge_indices,
            "in_indptr": in_.indptr,
            "in_edge_indices": in_.edge_indices,
            "incident_indptr": incident.indptr,
            "incident_edge_indices": incident.edge_indices,
            "relation_out_indptr": relation_out.indptr,
            "relation_out_edge_indices": relation_out.edge_indices,
            "relation_in_indptr": relation_in.indptr,
            "relation_in_edge_indices": relation_in.edge_indices,
            "positive_triples": positive_triples,
        }
        for name, array in arrays.items():
            np.save(path / f"{name}.npy", array)


def _encode_type_values(values, *, expected_size: int) -> tuple[np.ndarray, dict[str, int]]:
    values = list(values)
    if len(values) != expected_size:
        raise ValueError("node_type_values length must match external_node_ids")
    labels = sorted({str(value) for value in values if value is not None})
    mapping = {label: idx for idx, label in enumerate(labels)}
    encoded = np.asarray([mapping.get(str(value), -1) if value is not None else -1 for value in values], dtype=np.int64)
    return encoded, mapping


def _encode_with_mapping(values, mapping: dict[str, int]) -> np.ndarray:
    return np.asarray([mapping.get(str(value), -1) if value is not None else -1 for value in values], dtype=np.int64)
