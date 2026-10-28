"""Sampler snapshot build/load support."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Literal, Sequence, cast

import numpy as np
from ._arrays import Array, BoolArray, FloatArray, IntArray, StringArray
from .adjacency import CSRAdjacency, build_csr, build_sparse_csr, validate_csr
from .positive_membership import ARTIFACT as MEMBERSHIP_ARTIFACT, ENCODING as MEMBERSHIP_ENCODING, build_membership, has_membership_encoding, validate_membership
from .positive_membership import build_positive_history_index as _build_positive_history_index
from .relation_adjacency import ENCODING, NAMES, TEMPORAL_NAMES, load_relation_adjacency, sparse_arrays, sparse_encoding
from ..hierarchy import LabelHierarchy, RelationHierarchy
from ..readview import CurrentReadProvenance, ReadViewProvenance, parse_read_provenance
from ..versioning import EdgeVersion, NodeVersion, VersionOperation, normalize_temporal_instant


SNAPSHOT_FORMAT_VERSION = 2
LEGACY_SNAPSHOT_FORMAT_VERSION = 1
_COMPLETION_FILENAME = "completion.json"
_TIME_BUCKET_US = {"none": None, "hour": 3_600_000_000, "day": 86_400_000_000}


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
        edge_weights: Non-negative sampling weight for each edge.
        out: Source-to-edge CSR adjacency.
        in_: Target-to-edge CSR adjacency.
        incident: Undirected incident edge CSR adjacency.
        relation_out: Source/relation adjacency keyed by
            ``node_id * num_relations + relation_id``; use ``edge_range``.
        relation_in: Target/relation adjacency, with sparse populated-row
            directories on new snapshots and dense directories on legacy ones.
        positive_triples: Array of ``(src, rel, dst)`` positives.
        positive_membership_triples: Sorted unique static membership keys;
            temporal snapshots reuse ``positive_history_triples`` instead.
        edge_version_ids: Temporal edge-version UUIDs aligned with edge arrays,
            or ``None`` for non-temporal and legacy v1 snapshots.
        valid_from_us: Inclusive valid-time starts in UTC epoch microseconds.
        valid_to_us: Exclusive valid-time ends; open ends use zero together
            with ``valid_to_open``.
        temporal_out: Source/relation CSR ordered by valid start.
        temporal_in: Target/relation CSR ordered by valid start.
        positive_history_triples: Unique temporal positive triples.
        positive_history_indptr: Group offsets into positive history edge rows.
        positive_history_edge_indices: Edge rows ordered by triple and valid start.
        node_history_indptr: Node offsets into availability intervals.
        node_valid_from_us: Node availability interval starts.
        node_valid_to_us: Node availability interval ends.
        node_valid_to_open: Node availability open-end masks.
        node_lookup_order: Compact node IDs sorted by external node ID.
        edge_lookup_order: Compact edge IDs sorted by external edge ID.
        relation_lookup_order: Compact relation IDs sorted by external relation ID.
        node_lookup_ids: External node IDs in sorted lookup order.
        edge_lookup_ids: External edge IDs in sorted lookup order.
        relation_lookup_ids: External relation IDs in sorted lookup order.

    Examples:
        Persist and reload compact arrays without an embedded backend::

            >>> from tempfile import TemporaryDirectory
            >>> from gestaltdb.sampling import SamplerSnapshot
            >>> with TemporaryDirectory() as root:
            ...     snapshot = SamplerSnapshot.from_edge_arrays(
            ...         root + "/snapshot", external_node_ids=["drug-1", "protein-1"],
            ...         external_edge_ids=["edge-1"], external_relation_ids=["binds"],
            ...         src_int=[0], dst_int=[1], rel_int=[0],
            ...         node_type_values=["drug", "protein"])
            ...     loaded = SamplerSnapshot.load(snapshot.path)
            ...     assert loaded.external_node_id(loaded.node_int("drug-1")) == "drug-1"
            ...     assert loaded.num_edges == 1

        Use a fresh output directory for each immutable snapshot. The
        ``SamplerEngine`` class example continues from construction to sampling.
    """

    path: Path
    metadata: dict
    external_node_ids: StringArray
    node_type_ids: IntArray
    external_edge_ids: StringArray
    external_relation_ids: StringArray
    relation_src_type_ids: IntArray
    relation_dst_type_ids: IntArray
    src_int: IntArray
    dst_int: IntArray
    rel_int: IntArray
    out: CSRAdjacency
    in_: CSRAdjacency
    incident: CSRAdjacency
    relation_out: CSRAdjacency
    relation_in: CSRAdjacency
    positive_triples: IntArray
    edge_version_ids: StringArray | None = None
    valid_from_us: IntArray | None = None
    valid_to_us: IntArray | None = None
    valid_to_open: BoolArray | None = None
    edge_system_time_us: IntArray | None = None
    edge_commit_ids: IntArray | None = None
    temporal_out: CSRAdjacency | None = None
    temporal_in: CSRAdjacency | None = None
    positive_history_triples: IntArray | None = None
    positive_history_indptr: IntArray | None = None
    positive_history_edge_indices: IntArray | None = None
    node_history_indptr: IntArray | None = None
    node_valid_from_us: IntArray | None = None
    node_valid_to_us: IntArray | None = None
    node_valid_to_open: BoolArray | None = None
    edge_weights: FloatArray = field(default_factory=lambda: np.empty(0, dtype=np.float64))
    node_lookup_order: IntArray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    edge_lookup_order: IntArray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    relation_lookup_order: IntArray = field(default_factory=lambda: np.empty(0, dtype=np.int64))
    node_lookup_ids: StringArray = field(default_factory=lambda: np.empty(0, dtype=str))
    edge_lookup_ids: StringArray = field(default_factory=lambda: np.empty(0, dtype=str))
    relation_lookup_ids: StringArray = field(default_factory=lambda: np.empty(0, dtype=str))
    positive_membership_triples: IntArray | None = None

    @property
    def num_nodes(self) -> int:
        return int(self.external_node_ids.size)

    @property
    def num_edges(self) -> int:
        return int(self.src_int.size)

    @property
    def num_relations(self) -> int:
        return int(self.external_relation_ids.size)

    @property
    def temporal(self) -> bool:
        """Return whether this snapshot carries validated temporal arrays."""
        return self.edge_version_ids is not None

    def temporal_candidates(self, node: int, relation: int, valid_time, *, direction: str = "out") -> IntArray:
        """Return a start-time-pruned superset for a temporal point query.

        No edge valid at ``valid_time`` is omitted, while edges starting after
        the selected bucket are pruned. ``SamplerEngine`` applies the exact
        half-open point or window predicate before random selection.
        """
        if not self.temporal or self.temporal_out is None or self.temporal_in is None:
            raise ValueError("snapshot does not contain temporal indexes")
        if direction not in {"out", "in"}:
            raise ValueError("direction must be 'out' or 'in'")
        if not 0 <= int(node) < self.num_nodes or not 0 <= int(relation) < self.num_relations:
            raise ValueError("node or relation compact ID is out of range")
        instant = normalize_temporal_instant(valid_time).epoch_microseconds
        width = self.metadata["temporal_encoding"]["time_bucket_us"]
        upper = instant if width is None else (instant // width + 1) * width - 1
        adjacency = self.temporal_out if direction == "out" else self.temporal_in
        key = int(node) * self.num_relations + int(relation)
        candidates = adjacency.edge_range(key)
        starts = cast(IntArray, self.valid_from_us)[candidates]
        return candidates[: int(np.searchsorted(starts, upper, side="right"))]

    @classmethod
    def build(
        cls,
        graph,
        output_path: str | Path,
        *,
        node_filter: Callable[[object], bool] | None = None,
        edge_filter: Callable[[object], bool] | None = None,
        edge_type_property: str = "type",
        edge_weight_property: str | None = None,
        node_type_property: str = "kind",
        directed: bool = True,
        include_reverse: bool = True,
        reverse_relation_policy: str = "adjacency_only",
        storage: str = "npy",
        layout: str = "csr",
        source_db: dict | None = None,
        source_artifacts: dict | None = None,
        source_provenance: dict | None = None,
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
            edge_weight_property: Optional edge property containing a finite,
                non-negative sampling weight. Missing values default to ``1.0``.
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
        edge_weights: list[float] = []
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
            weight = 1.0 if edge_weight_property is None else edge.properties.get(edge_weight_property, 1.0)
            edge_weights.append(_validate_weight(weight))

        relations = sorted(set(rel_values))
        rel_to_int = {relation: idx for idx, relation in enumerate(relations)}
        src_int = np.asarray(src_values, dtype=np.int64)
        dst_int = np.asarray(dst_values, dtype=np.int64)
        rel_int = np.asarray([rel_to_int[relation] for relation in rel_values], dtype=np.int64)
        edge_weights_array = np.asarray(edge_weights, dtype=np.float64)
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
        relation_out = build_sparse_csr(src_int * max(1, len(relations)) + rel_int, edge_indices, relation_size)
        relation_in = build_sparse_csr(dst_int * max(1, len(relations)) + rel_int, edge_indices, relation_size)
        positive_triples = np.stack([src_int, rel_int, dst_int], axis=1) if src_int.size else np.empty((0, 3), dtype=np.int64)

        metadata = {
            "format_version": SNAPSHOT_FORMAT_VERSION,
            "node_count": len(node_ids),
            "edge_count": int(src_int.size),
            "relation_count": len(relations),
            "node_type_count": len(type_values),
            "edge_type_property": edge_type_property,
            "edge_weight_property": edge_weight_property,
            "node_type_property": node_type_property,
            "directed": directed,
            "include_reverse": include_reverse,
            "reverse_relation_policy": reverse_relation_policy,
            "storage": storage,
            "layout": layout,
            "dtypes": {"ids": "str", "indices": "int64"},
            "filtered": node_filter is not None or edge_filter is not None,
        }
        if source_db is not None:
            metadata["source_db"] = cls._normalize_source_reference(source_db, path)
        if source_artifacts is not None:
            metadata["source_artifacts"] = cls._normalize_artifact_references(source_artifacts, path)
        if source_provenance is not None:
            metadata["source_provenance"] = parse_read_provenance(source_provenance).to_dict()
        metadata["snapshot_id"] = uuid.uuid4().hex
        metadata["source_version"] = cls._source_version_of(graph)
        metadata["schema_hash"] = _consistency_schema_hash({
            "edge_type_property": edge_type_property,
            "edge_weight_property": edge_weight_property,
            "node_type_property": node_type_property,
            "directed": directed,
            "include_reverse": include_reverse,
            "reverse_relation_policy": reverse_relation_policy,
            "layout": layout,
            "storage": storage,
        })
        metadata["append_generation"] = 0
        metadata["compactions"] = 0

        cls._write(path, metadata, node_ids, node_type_ids, edge_ids, relations, relation_src_type_ids, relation_dst_type_ids, src_int, dst_int, rel_int, edge_weights_array, out, in_, incident, relation_out, relation_in, positive_triples)
        return cls.load(path)

    @classmethod
    def build_temporal(
        cls,
        read_view,
        output_path: str | Path,
        *,
        node_filter: Callable[[object], bool] | None = None,
        edge_filter: Callable[[object], bool] | None = None,
        edge_type_property: str = "type",
        edge_weight_property: str | None = None,
        node_type_property: str = "kind",
        time_bucket: str | None = "day",
        source_db: dict | None = None,
        **metadata_options,
    ) -> "SamplerSnapshot":
        """Build a deterministic temporal-history snapshot from one read view."""
        reserved_metadata = {"source_provenance", "temporal_encoding"}.intersection(metadata_options)
        if reserved_metadata:
            names = ", ".join(sorted(reserved_metadata))
            raise ValueError(f"temporal snapshot metadata cannot override: {names}")
        bucket = "none" if time_bucket is None else time_bucket
        if bucket not in _TIME_BUCKET_US:
            raise ValueError("time_bucket must be None, 'none', 'hour', or 'day'")
        reverse_policy = metadata_options.pop("reverse_relation_policy", "adjacency_only")
        if reverse_policy not in {"adjacency_only", "synthetic_relations", "none"}:
            raise ValueError("reverse_relation_policy must be 'adjacency_only', 'synthetic_relations', or 'none'")
        layout = metadata_options.pop("layout", "csr")
        if layout != "csr":
            raise ValueError("only layout='csr' is currently supported")

        node_payloads: dict[str, NodeVersion] = {}
        node_versions: dict[str, list[NodeVersion]] = {}
        for version in read_view.iter_node_versions():
            node_versions.setdefault(version.logical_id, []).append(version)
            if version.operation is not VersionOperation.RETRACT and version.node is not None:
                current = node_payloads.get(version.logical_id)
                if current is None or (version.commit_id, version.commit_ordinal) > (current.commit_id, current.commit_ordinal):
                    node_payloads[version.logical_id] = version

        records = []
        by_logical_id: dict[str, list[EdgeVersion]] = {}
        for version in read_view.iter_edge_versions():
            by_logical_id.setdefault(version.logical_id, []).append(version)
        for logical_id in sorted(by_logical_id):
            covered: list[tuple[int, int | None]] = []
            versions = sorted(
                by_logical_id[logical_id],
                key=lambda item: (item.commit_id, item.commit_ordinal, item.version_id),
                reverse=True,
            )
            for version in versions:
                start = version.valid.start.epoch_microseconds
                end = None if version.valid.end is None else version.valid.end.epoch_microseconds
                pieces = _subtract_intervals(start, end, covered)
                if version.operation is not VersionOperation.RETRACT and version.edge is not None:
                    if edge_filter is None or edge_filter(version.edge):
                        relation = version.edge.properties.get(edge_type_property)
                        if relation is not None:
                            for piece_start, piece_end in pieces:
                                records.append((
                                    logical_id, version.version_id, version.edge, str(relation),
                                    piece_start, piece_end, version.system_time.epoch_microseconds,
                                    version.commit_id,
                                ))
                covered = _merge_intervals([*covered, (start, end)])

        endpoint_ids = {str(record[2].source) for record in records} | {str(record[2].target) for record in records}
        node_ids = sorted(set(node_payloads) | endpoint_ids)
        if node_filter is not None:
            node_ids = [
                node_id for node_id in node_ids
                if node_id not in node_payloads or node_filter(node_payloads[node_id].node)
            ]
        node_to_int = {node_id: index for index, node_id in enumerate(node_ids)}
        records = [
            record for record in records
            if str(record[2].source) in node_to_int and str(record[2].target) in node_to_int
        ]
        node_intervals = []
        for node_id in node_ids:
            covered = []
            available = []
            node_history = sorted(
                node_versions.get(node_id, []),
                key=lambda item: (item.commit_id, item.commit_ordinal, item.version_id),
                reverse=True,
            )
            for version in node_history:
                start = version.valid.start.epoch_microseconds
                end = None if version.valid.end is None else version.valid.end.epoch_microseconds
                pieces = _subtract_intervals(start, end, covered)
                if version.operation is not VersionOperation.RETRACT and version.node is not None:
                    available.extend(pieces)
                covered = _merge_intervals([*covered, (start, end)])
            if not node_history:
                available = [
                    (record[4], record[5]) for record in records
                    if str(record[2].source) == node_id or str(record[2].target) == node_id
                ]
            node_intervals.append(_merge_intervals(available))
        node_type_values = [
            None
            if (node := node_payloads[node_id].node if node_id in node_payloads else None) is None
            else node.properties.get(node_type_property)
            for node_id in node_ids
        ]
        node_type_ids, type_mapping = _encode_type_values(node_type_values, expected_size=len(node_ids))
        records.sort(key=lambda item: (item[0], item[1], item[4], item[5] is None, item[5] or 0))
        relations = sorted({record[3] for record in records})
        relation_to_int = {relation: index for index, relation in enumerate(relations)}

        snapshot_metadata = {
            "temporal": True,
            "edge_type_property": edge_type_property,
            "edge_weight_property": edge_weight_property,
            "node_type_property": node_type_property,
            "directed": metadata_options.pop("directed", True),
            "include_reverse": metadata_options.pop("include_reverse", True),
            "reverse_relation_policy": reverse_policy,
            "layout": layout,
            "temporal_encoding": {
                "instant": "signed_utc_epoch_microseconds",
                "interval": "half_open",
                "open_end": "valid_to_open_mask",
                "time_bucket": bucket,
                "time_bucket_us": _TIME_BUCKET_US[bucket],
                "index_order": ["endpoint", "relation", "valid_from_us", "edge_index"],
            },
            "source_provenance": read_view.provenance.to_dict(),
            "node_type_values": {str(index): value for value, index in type_mapping.items()},
            **metadata_options,
        }
        return cls.from_arrays(
            output_path,
            external_node_ids=node_ids,
            node_type_ids=node_type_ids,
            external_edge_ids=[record[0] for record in records],
            external_relation_ids=relations,
            src_int=[node_to_int[str(record[2].source)] for record in records],
            dst_int=[node_to_int[str(record[2].target)] for record in records],
            rel_int=[relation_to_int[record[3]] for record in records],
            edge_weights=[
                1.0 if edge_weight_property is None else _validate_weight(record[2].properties.get(edge_weight_property, 1.0))
                for record in records
            ],
            edge_version_ids=[record[1] for record in records],
            valid_from_us=[record[4] for record in records],
            valid_to_us=[0 if record[5] is None else record[5] for record in records],
            valid_to_open=[record[5] is None for record in records],
            edge_system_time_us=[record[6] for record in records],
            edge_commit_ids=[record[7] for record in records],
            node_intervals=node_intervals,
            source_db=source_db,
            metadata=snapshot_metadata,
        )

    @classmethod
    def from_edge_arrays(
        cls,
        output_path: str | Path,
        *,
        external_node_ids: Sequence[object] | Array,
        external_edge_ids: Sequence[object] | Array,
        external_relation_ids: Sequence[object] | Array,
        src_int: Sequence[int] | Array,
        dst_int: Sequence[int] | Array,
        rel_int: Sequence[int] | Array,
        edge_weights: Sequence[float] | Array | None = None,
        node_type_values: Sequence[object] | None = None,
        node_type_ids: Sequence[int] | Array | None = None,
        edge_src_type_values: Sequence[object] | None = None,
        edge_dst_type_values: Sequence[object] | None = None,
        relation_src_type_ids: Sequence[int] | Array | None = None,
        relation_dst_type_ids: Sequence[int] | Array | None = None,
        source_db: dict | None = None,
        source_artifacts: dict | None = None,
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
            edge_weights: Optional finite, non-negative sampling weight per edge.
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
        external_node_ids = cast(StringArray, np.asarray(external_node_ids, dtype=str))
        external_edge_ids = cast(StringArray, np.asarray(external_edge_ids, dtype=str))
        external_relation_ids = cast(StringArray, np.asarray(external_relation_ids, dtype=str))
        src_int = _integer_id_array(src_int, "src_int")
        dst_int = _integer_id_array(dst_int, "dst_int")
        rel_int = _integer_id_array(rel_int, "rel_int")
        cls._validate_edge_arrays(external_node_ids, external_edge_ids, external_relation_ids, src_int, dst_int, rel_int)
        edge_weights = _normalize_edge_weights(edge_weights, src_int.size)

        type_mapping = None
        if node_type_values is not None:
            node_type_ids, type_mapping = _encode_type_values(node_type_values, expected_size=external_node_ids.size)
        elif node_type_ids is None:
            node_type_ids = cast(IntArray, np.full(external_node_ids.size, -1, dtype=np.int64))
        else:
            node_type_ids = _integer_id_array(node_type_ids, "node_type_ids")
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
        path = Path(output_path)
        if source_db is not None:
            snapshot_metadata["source_db"] = cls._normalize_source_reference(source_db, path)
        if source_artifacts is not None:
            snapshot_metadata["source_artifacts"] = cls._normalize_artifact_references(source_artifacts, path)
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
            edge_weights=edge_weights,
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
        edge_weights=None,
        relation_src_type_ids=None,
        relation_dst_type_ids=None,
        edge_version_ids=None,
        valid_from_us=None,
        valid_to_us=None,
        valid_to_open=None,
        edge_system_time_us=None,
        edge_commit_ids=None,
        node_intervals=None,
        source_db: dict | None = None,
        source_artifacts: dict | None = None,
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
            edge_weights: Optional finite, non-negative sampling weight per edge.
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
        external_node_ids = np.asarray(external_node_ids, dtype=str)
        node_type_ids = _integer_id_array(node_type_ids, "node_type_ids")
        external_edge_ids = np.asarray(external_edge_ids, dtype=str)
        external_relation_ids = np.asarray(external_relation_ids, dtype=str)
        src_int = _integer_id_array(src_int, "src_int")
        dst_int = _integer_id_array(dst_int, "dst_int")
        rel_int = _integer_id_array(rel_int, "rel_int")
        cls._validate_edge_arrays(external_node_ids, external_edge_ids, external_relation_ids, src_int, dst_int, rel_int)
        if node_type_ids.size != external_node_ids.size:
            raise ValueError("node_type_ids length must match external_node_ids")
        edge_weights = _normalize_edge_weights(edge_weights, src_int.size)

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
            relation_src_type_ids = _integer_id_array(relation_src_type_ids, "relation_src_type_ids")
            relation_dst_type_ids = _integer_id_array(relation_dst_type_ids, "relation_dst_type_ids")
            if relation_src_type_ids.size != relation_count or relation_dst_type_ids.size != relation_count:
                raise ValueError("sampler snapshot relation type arrays must match external_relation_ids")

        out, in_, incident, relation_out, relation_in, positive_triples = _build_sampling_indexes(
            int(external_node_ids.size), relation_count, src_int, dst_int, rel_int
        )
        temporal_values = None
        supplied_temporal = (
            edge_version_ids, valid_from_us, valid_to_us, valid_to_open,
            edge_system_time_us, edge_commit_ids,
        )
        if any(value is not None for value in supplied_temporal):
            if any(value is None for value in supplied_temporal):
                raise ValueError("all temporal edge arrays must be provided together")
            temporal_values = (
                np.asarray(edge_version_ids, dtype=str),
                np.asarray(valid_from_us, dtype=np.int64),
                np.asarray(valid_to_us, dtype=np.int64),
                np.asarray(valid_to_open, dtype=np.bool_),
                np.asarray(edge_system_time_us, dtype=np.int64),
                np.asarray(edge_commit_ids, dtype=np.int64),
            )
            if any(array.size != src_int.size for array in temporal_values):
                raise ValueError("temporal edge arrays must align with edge arrays")
            if node_intervals is None:
                node_intervals = []
                for node in range(external_node_ids.size):
                    intervals = [
                        (int(valid_from_us[edge]), None if valid_to_open[edge] else int(valid_to_us[edge]))
                        for edge in range(src_int.size)
                        if src_int[edge] == node or dst_int[edge] == node
                    ]
                    node_intervals.append(_merge_intervals(intervals))
            elif len(node_intervals) != external_node_ids.size:
                raise ValueError("node availability intervals must align with nodes")
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
        snapshot_metadata.update({
            "format_version": SNAPSHOT_FORMAT_VERSION,
            "node_count": int(external_node_ids.size),
            "edge_count": int(src_int.size),
            "relation_count": relation_count,
            "storage": "npy",
            "layout": "csr",
            "temporal": temporal_values is not None,
        })
        if temporal_values is not None:
            if "source_provenance" not in snapshot_metadata or "temporal_encoding" not in snapshot_metadata:
                raise ValueError("temporal arrays require source provenance and temporal encoding metadata")
        if source_db is not None:
            snapshot_metadata["source_db"] = cls._normalize_source_reference(source_db, path)
        if source_artifacts is not None:
            snapshot_metadata["source_artifacts"] = cls._normalize_artifact_references(source_artifacts, path)
        snapshot_metadata.setdefault("snapshot_id", uuid.uuid4().hex)
        snapshot_metadata.setdefault("source_version", None)
        snapshot_metadata.setdefault("schema_hash", _consistency_schema_hash({
            "layout": "csr",
            "storage": "npy",
            "temporal": temporal_values is not None,
        }))
        snapshot_metadata.setdefault("append_generation", 0)
        snapshot_metadata.setdefault("compactions", 0)
        cls._write(
            path, snapshot_metadata, external_node_ids, node_type_ids, external_edge_ids,
            external_relation_ids, relation_src_type_ids, relation_dst_type_ids, src_int,
            dst_int, rel_int, edge_weights, out, in_, incident, relation_out, relation_in,
            positive_triples, temporal_values=temporal_values,
            node_intervals=node_intervals,
        )
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
        try:
            metadata_bytes = (path / "metadata.json").read_bytes()
            metadata = json.loads(metadata_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid sampler snapshot metadata") from exc
        if not isinstance(metadata, dict):
            raise ValueError("invalid sampler snapshot metadata")
        version = metadata.get("format_version")
        if version not in {LEGACY_SNAPSHOT_FORMAT_VERSION, SNAPSHOT_FORMAT_VERSION}:
            raise ValueError("unsupported sampler snapshot format version")
        membership = has_membership_encoding(metadata)
        sparse = sparse_encoding(metadata)
        if version == SNAPSHOT_FORMAT_VERSION:
            cls._validate_v2_files(path, metadata, metadata_bytes)
        temporal = version == SNAPSHOT_FORMAT_VERSION and metadata.get("temporal") is True
        temporal_history_indexes = temporal and metadata.get("temporal_history_indexes") is True
        mmap_mode: Literal["r"] | None = "r" if mmap else None

        def load_array(name: str) -> np.ndarray:
            return np.load(path / f"{name}.npy", mmap_mode=mmap_mode, allow_pickle=False)

        def has_array(name: str) -> bool:
            if version == SNAPSHOT_FORMAT_VERSION:
                return name in metadata["artifacts"]
            return (path / f"{name}.npy").is_file()

        edge_weights = load_array("edge_weights") if has_array("edge_weights") else np.ones(int(metadata["edge_count"]), dtype=np.float64)
        edge_weights = _normalize_edge_weights(edge_weights, int(metadata["edge_count"]))
        external_node_ids = load_array("external_node_ids")
        external_edge_ids = load_array("external_edge_ids")
        external_relation_ids = load_array("external_relation_ids")

        def load_lookup(entity: str, external_ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
            has_ids = has_array(f"{entity}_lookup_ids")
            has_order = has_array(f"{entity}_lookup_order")
            if not has_ids and not has_order:
                order = _build_lookup_order(external_ids, entity, require_unique=False)
                return external_ids[order], order
            if not has_ids or not has_order:
                raise ValueError(f"{entity} lookup requires both ID and order arrays")
            lookup_ids = load_array(f"{entity}_lookup_ids")
            order = load_array(f"{entity}_lookup_order")
            if lookup_ids.ndim != 1 or lookup_ids.size != external_ids.size:
                raise ValueError(f"{entity}_lookup_ids must contain one entry per external ID")
            if not np.issubdtype(lookup_ids.dtype, np.str_):
                raise ValueError(f"{entity}_lookup_ids must contain strings")
            if order.ndim != 1 or order.size != external_ids.size:
                raise ValueError(f"{entity}_lookup_order must contain one entry per external ID")
            if order.size and (np.issubdtype(order.dtype, np.bool_) or not np.issubdtype(order.dtype, np.integer)):
                raise ValueError(f"{entity}_lookup_order must contain integer compact IDs")
            if order.size and (cast(Callable[[], np.integer], order.min)() < 0 or cast(Callable[[], np.integer], order.max)() >= external_ids.size):
                raise ValueError(f"{entity}_lookup_order contains invalid compact IDs")
            if lookup_ids.size > 1 and np.any(lookup_ids[1:] < lookup_ids[:-1]):
                raise ValueError(f"{entity}_lookup_ids must be sorted")
            order = order.astype(np.int64, copy=False)
            seen = np.zeros(order.size, dtype=bool)
            chunk_size = 65_536
            for start in range(0, order.size, chunk_size):
                indices = order[start : start + chunk_size]
                if np.unique(indices).size != indices.size or np.any(seen[indices]):
                    raise ValueError(f"{entity}_lookup_order must be a permutation of compact IDs")
                seen[indices] = True
                if np.any(external_ids[indices] != lookup_ids[start : start + chunk_size]):
                    raise ValueError(f"{entity} lookup arrays do not match external IDs")
            return lookup_ids, order

        node_lookup_ids, node_lookup_order = load_lookup("node", external_node_ids)
        edge_lookup_ids, edge_lookup_order = load_lookup("edge", external_edge_ids)
        relation_lookup_ids, relation_lookup_order = load_lookup("relation", external_relation_ids)

        snapshot = cls(
            path=path,
            metadata=metadata,
            external_node_ids=external_node_ids,
            node_type_ids=load_array("node_type_ids"),
            external_edge_ids=external_edge_ids,
            external_relation_ids=external_relation_ids,
            relation_src_type_ids=load_array("relation_src_type_ids"),
            relation_dst_type_ids=load_array("relation_dst_type_ids"),
            src_int=load_array("src_int"),
            dst_int=load_array("dst_int"),
            rel_int=load_array("rel_int"),
            edge_weights=edge_weights,
            out=CSRAdjacency(load_array("out_indptr"), load_array("out_edge_indices")),
            in_=CSRAdjacency(load_array("in_indptr"), load_array("in_edge_indices")),
            incident=CSRAdjacency(load_array("incident_indptr"), load_array("incident_edge_indices")),
            relation_out=load_relation_adjacency("relation_out", load_array, sparse=sparse),
            relation_in=load_relation_adjacency("relation_in", load_array, sparse=sparse),
            positive_triples=load_array("positive_triples"),
            positive_membership_triples=load_array(MEMBERSHIP_ARTIFACT) if membership and not temporal else None,
            edge_version_ids=load_array("edge_version_ids") if temporal else None,
            valid_from_us=load_array("valid_from_us") if temporal else None,
            valid_to_us=load_array("valid_to_us") if temporal else None,
            valid_to_open=load_array("valid_to_open") if temporal else None,
            edge_system_time_us=load_array("edge_system_time_us") if temporal else None,
            edge_commit_ids=load_array("edge_commit_ids") if temporal else None,
            temporal_out=(
                load_relation_adjacency("temporal_out", load_array, sparse=sparse)
                if temporal else None
            ),
            temporal_in=(
                load_relation_adjacency("temporal_in", load_array, sparse=sparse)
                if temporal else None
            ),
            positive_history_triples=load_array("positive_history_triples") if temporal_history_indexes else None,
            positive_history_indptr=load_array("positive_history_indptr") if temporal_history_indexes else None,
            positive_history_edge_indices=load_array("positive_history_edge_indices") if temporal_history_indexes else None,
            node_history_indptr=load_array("node_history_indptr") if temporal_history_indexes else None,
            node_valid_from_us=load_array("node_valid_from_us") if temporal_history_indexes else None,
            node_valid_to_us=load_array("node_valid_to_us") if temporal_history_indexes else None,
            node_valid_to_open=load_array("node_valid_to_open") if temporal_history_indexes else None,
            node_lookup_order=node_lookup_order,
            edge_lookup_order=edge_lookup_order,
            relation_lookup_order=relation_lookup_order,
            node_lookup_ids=node_lookup_ids,
            edge_lookup_ids=edge_lookup_ids,
            relation_lookup_ids=relation_lookup_ids,
        )
        if temporal and not temporal_history_indexes:
            snapshot._derive_temporal_history_indexes()
        if version == SNAPSHOT_FORMAT_VERSION:
            snapshot._validate_v2_arrays()
        return snapshot

    def source_graph_exists(self, *, base_path=None) -> bool:
        """Return whether the referenced source graph path exists without opening it."""
        try:
            return self._resolve_source_graph_path(base_path=base_path).exists()
        except ValueError:
            return False

    def open_source_graph(self, *, base_path=None, backend_options=None):
        """Open the ``GraphDB`` referenced by this snapshot's source metadata."""
        source_path = self._resolve_source_graph_path(base_path=base_path)
        if not source_path.exists():
            raise ValueError(f"SamplerSnapshot source DB path does not exist: {source_path}")
        from gestaltdb.graphdb import GraphDB

        graph = GraphDB.open(source_path, backend_options=backend_options)
        try:
            self.verify_source(graph)
        except Exception:
            graph.close()
            raise
        return graph

    def verify_source(self, graph) -> None:
        """Verify provenance-bearing snapshots against a source graph."""
        value = self.metadata.get("source_provenance")
        if value is None:
            return
        parse_read_provenance(value).verify_source(graph)

    @staticmethod
    def _source_version_of(graph) -> dict | None:
        """Extract the best observable source-version token from a graph.

        Prefers an authenticated current-read-view token (LMDB, transactional
        PyRex). Graphs without verifiable read identity report ``None``:
        plain mutations on such backends leave no observable counter, so no
        version is manufactured. Duck-typed for graph-like test doubles.
        """
        view_factory = getattr(graph, "current_read_view", None)
        if callable(view_factory):
            try:
                with view_factory() as view:
                    to_dict = getattr(getattr(view, "provenance", None), "to_dict", None)
                    if callable(to_dict):
                        payload = to_dict()
                        token = {
                            "kind": "current-read-view",
                            "database_id": payload.get("database_id"),
                            "snapshot_sequence": payload.get("snapshot_sequence"),
                            "state_sha256": payload.get("state_sha256"),
                            "token": payload.get("token"),
                        }
                        if all(isinstance(token[key], str) for key in (
                                "database_id", "state_sha256", "token")) and isinstance(
                                token["snapshot_sequence"], int):
                            return token
            except Exception:
                pass
        return None

    def is_stale(self, graph) -> bool:
        """Return whether the source graph moved past the recorded version.

        Snapshots without a recorded source version (legacy builds, graphs
        without verifiable read identity) always report stale: freshness
        cannot be proven, so it is never assumed. Temporal-only counters are
        deliberately not consulted — they are blind to plain mutations.
        """
        recorded = self.metadata.get("source_version")
        if not isinstance(recorded, dict):
            return True
        return self._source_version_of(graph) != recorded

    def check_fresh(self, graph) -> dict:
        """Return the recorded source version if the graph still matches it.

        Raises ``ValueError`` naming the recorded and observed versions when
        the source moved or when either side is unverifiable. Call this
        before training runs that must not silently reuse invalidated data.
        """
        recorded = self.metadata.get("source_version")
        if not isinstance(recorded, dict):
            raise ValueError("snapshot records no verifiable source version; rebuild or re-append explicitly")
        current = self._source_version_of(graph)
        if current != recorded:
            raise ValueError(
                f"source graph moved past the snapshot version (recorded={recorded!r}, current={current!r}); "
                "rebuild, append, or compact explicitly"
            )
        return recorded

    def append_from_graph(self, graph, edge_keys, *, edge_type_property: str = "type",
                          edge_weight_property: str | None = None,
                          node_type_property: str = "kind") -> dict:
        """Append graph edges to this snapshot without a full rebuild.

        Node and relation maps grow append-only: existing compact IDs never
        change, so stored batches stay valid. Adjacency indexes are rebuilt
        from the extended rows (documented ``O(E)`` cost — measure before
        assuming append beats rebuild at scale). The snapshot identity is
        preserved while ``append_generation`` advances and the source version
        is restamped from the graph.

        Only snapshots built by ``build`` carry the recorded interpretation
        (type properties) required to encode new rows; array-built snapshots
        are rejected. Temporal snapshots are rejected. Duplicate external
        edge IDs fail closed. No live readers may hold this snapshot path
        (including memmap loads) across the call.
        """
        if self.metadata.get("format_version") != SNAPSHOT_FORMAT_VERSION:
            raise ValueError("append requires a current-format snapshot; rebuild legacy snapshots first")
        if self.metadata.get("temporal") is True:
            raise ValueError("temporal snapshots do not support append; rebuild explicitly")
        if not isinstance(edge_type_property, str) or not edge_type_property:
            raise ValueError("edge_type_property must be a non-empty string")
        if edge_weight_property is not None and (
                not isinstance(edge_weight_property, str) or not edge_weight_property):
            raise ValueError("edge_weight_property must be a non-empty string or None")
        if not isinstance(node_type_property, str) or not node_type_property:
            raise ValueError("node_type_property must be a non-empty string")
        expected_schema = _consistency_schema_hash({
            "edge_type_property": self.metadata.get("edge_type_property"),
            "edge_weight_property": self.metadata.get("edge_weight_property"),
            "node_type_property": self.metadata.get("node_type_property"),
            "directed": self.metadata.get("directed"),
            "include_reverse": self.metadata.get("include_reverse"),
            "reverse_relation_policy": self.metadata.get("reverse_relation_policy"),
            "layout": self.metadata.get("layout"),
            "storage": self.metadata.get("storage"),
        })
        if "edge_type_property" not in self.metadata or self.metadata.get("schema_hash") != expected_schema:
            raise ValueError("snapshot lacks recorded build interpretation; extend array-built snapshots via from_arrays")
        candidate = {
            "edge_type_property": edge_type_property,
            "edge_weight_property": edge_weight_property,
            "node_type_property": node_type_property,
            "directed": self.metadata.get("directed"),
            "include_reverse": self.metadata.get("include_reverse"),
            "reverse_relation_policy": self.metadata.get("reverse_relation_policy"),
            "layout": self.metadata.get("layout"),
            "storage": self.metadata.get("storage"),
        }
        if _consistency_schema_hash(candidate) != self.metadata.get("schema_hash"):
            raise ValueError("append interpretation does not match the snapshot schema hash")
        try:
            keys = list(edge_keys)
        except TypeError as exc:
            raise ValueError("edge_keys must be iterable") from exc
        if not keys:
            raise ValueError("edge_keys must be non-empty")
        existing_edge_ids = set(self.external_edge_ids.tolist())
        node_ids = self.external_node_ids.tolist()
        node_to_int = {node_id: idx for idx, node_id in enumerate(node_ids)}
        relations = self.external_relation_ids.tolist()
        rel_to_int = {relation: idx for idx, relation in enumerate(relations)}
        type_by_index = {int(index): value for index, value in (self.metadata.get("node_type_values") or {}).items()}
        index_by_type = {value: index for index, value in type_by_index.items()}
        next_type_index = max(type_by_index, default=-1) + 1
        node_type_ids = self.node_type_ids.tolist()
        new_edge_ids: list[str] = []
        new_src: list[int] = []
        new_dst: list[int] = []
        new_rel: list[int] = []
        new_weights: list[float] = []
        seen_batch: set[str] = set()
        for key in keys:
            edge = graph.get_edge(key)
            if edge is None:
                raise ValueError(f"append edge key does not resolve: {key!r}")
            external_edge_id = str(edge.get_id)
            if external_edge_id in existing_edge_ids or external_edge_id in seen_batch:
                raise ValueError(f"duplicate external edge ID on append: {external_edge_id!r}")
            seen_batch.add(external_edge_id)
            src = graph.key_to_string(edge.source)
            dst = graph.key_to_string(edge.target)
            for endpoint in (src, dst):
                if endpoint not in node_to_int:
                    node = graph.get_node(endpoint)
                    node_type = None if node is None else node.properties.get(node_type_property)
                    if node_type is None:
                        node_type_ids.append(-1)
                    elif str(node_type) in index_by_type:
                        node_type_ids.append(index_by_type[str(node_type)])
                    elif type_by_index:
                        index_by_type[str(node_type)] = next_type_index
                        type_by_index[next_type_index] = str(node_type)
                        node_type_ids.append(next_type_index)
                        next_type_index += 1
                    else:
                        node_type_ids.append(-1)
                    node_to_int[endpoint] = len(node_ids)
                    node_ids.append(endpoint)
            relation = edge.properties.get(edge_type_property)
            if relation is None:
                raise ValueError(f"append edge lacks the relation property: {external_edge_id!r}")
            relation = str(relation)
            if relation not in rel_to_int:
                rel_to_int[relation] = len(relations)
                relations.append(relation)
            weight = 1.0 if edge_weight_property is None else edge.properties.get(edge_weight_property, 1.0)
            new_edge_ids.append(external_edge_id)
            new_src.append(node_to_int[src])
            new_dst.append(node_to_int[dst])
            new_rel.append(rel_to_int[relation])
            new_weights.append(_validate_weight(weight))
        pre_node_count = len(self.external_node_ids)
        pre_relation_count = len(self.external_relation_ids)
        external_edge_ids = self.external_edge_ids.tolist() + new_edge_ids
        src_int = np.concatenate([self.src_int, np.asarray(new_src, dtype=np.int64)])
        dst_int = np.concatenate([self.dst_int, np.asarray(new_dst, dtype=np.int64)])
        rel_int = np.concatenate([self.rel_int, np.asarray(new_rel, dtype=np.int64)])
        edge_weights = np.concatenate([self.edge_weights, np.asarray(new_weights, dtype=np.float64)])
        relation_src_type_ids, relation_dst_type_ids = self._derive_relation_endpoint_types(
            np.asarray(node_type_ids, dtype=np.int64), src_int, dst_int, rel_int, len(relations))
        out, in_, incident, relation_out, relation_in, positive_triples = _build_sampling_indexes(
            len(node_ids), len(relations), src_int, dst_int, rel_int)
        metadata = dict(self.metadata)
        metadata.update({
            "node_count": len(node_ids),
            "edge_count": int(src_int.size),
            "relation_count": len(relations),
            "schema_hash": _consistency_schema_hash(candidate),
            "source_version": self._source_version_of(graph),
            "append_generation": int(self.metadata.get("append_generation", 0)) + 1,
        })
        if type_by_index:
            metadata["node_type_values"] = {str(index): value for index, value in sorted(type_by_index.items())}
        self._replace(
            self.path, metadata, node_ids, np.asarray(node_type_ids, dtype=np.int64),
            external_edge_ids, relations, relation_src_type_ids, relation_dst_type_ids,
            src_int, dst_int, rel_int, edge_weights, out, in_, incident,
            relation_out, relation_in, positive_triples)
        self._refresh_from_disk()
        return {
            "appended_edges": len(new_edge_ids),
            "appended_nodes": len(node_ids) - pre_node_count,
            "appended_relations": len(relations) - pre_relation_count,
            "append_generation": int(metadata["append_generation"]),
            "snapshot_id": metadata["snapshot_id"],
        }

    def compact(self, graph=None) -> dict:
        """Canonically rebuild this snapshot in place and verify integrity.

        Rebuilds every adjacency index from the current edge rows, rewrites
        all arrays atomically, and re-authenticates the result on load.
        Compact IDs for nodes and relations are never reordered: map
        extension stays append-only across compactions.

        With ``graph`` (unfiltered snapshots only), edge rows whose external
        ID no longer resolves in the source are pruned first — the
        incremental answer to source deletions. Pruned edge rows disappear,
        so edge compact IDs after pruned rows shift; node and relation IDs
        do not. Filtered snapshots reject graph pruning: recorded filters
        are callables that cannot be re-applied, so pruning would silently
        drop intentionally excluded edges. No live readers may hold this
        snapshot path across the call.
        """
        if self.metadata.get("format_version") != SNAPSHOT_FORMAT_VERSION:
            raise ValueError("compact requires a current-format snapshot; rebuild legacy snapshots first")
        if self.metadata.get("temporal") is True:
            raise ValueError("temporal snapshots do not support compact; rebuild explicitly")
        pruned = 0
        if graph is not None:
            if self.metadata.get("filtered") is True:
                raise ValueError("cannot prune filtered snapshots; rebuild explicitly")
            live: set[str] = set()
            for edge_key in graph.store.get_edge_keys_generator():
                edge = graph.get_edge(edge_key)
                if edge is not None:
                    live.add(str(edge.get_id))
            keep = np.array([edge_id in live for edge_id in self.external_edge_ids.tolist()], dtype=bool)
            pruned = int((~keep).sum())
            external_edge_ids = self.external_edge_ids[keep].tolist()
            src_int = self.src_int[keep]
            dst_int = self.dst_int[keep]
            rel_int = self.rel_int[keep]
            edge_weights = self.edge_weights[keep]
        else:
            external_edge_ids = self.external_edge_ids.tolist()
            src_int = self.src_int
            dst_int = self.dst_int
            rel_int = self.rel_int
            edge_weights = self.edge_weights
        node_ids = self.external_node_ids.tolist()
        relations = self.external_relation_ids.tolist()
        out, in_, incident, relation_out, relation_in, positive_triples = _build_sampling_indexes(
            len(node_ids), len(relations), src_int, dst_int, rel_int)
        metadata = dict(self.metadata)
        metadata.update({
            "node_count": len(node_ids),
            "edge_count": int(src_int.size),
            "relation_count": len(relations),
            "compactions": int(self.metadata.get("compactions", 0)) + 1,
        })
        if graph is not None:
            metadata["source_version"] = self._source_version_of(graph)
        self._replace(
            self.path, metadata, node_ids, self.node_type_ids,
            external_edge_ids, relations, self.relation_src_type_ids, self.relation_dst_type_ids,
            src_int, dst_int, rel_int, edge_weights, out, in_, incident,
            relation_out, relation_in, positive_triples)
        self._refresh_from_disk()
        return {
            "pruned_edges": pruned,
            "edge_count": int(src_int.size),
            "compactions": int(metadata["compactions"]),
            "snapshot_id": metadata["snapshot_id"],
        }

    def set_edge_weights(self, weights, *, source: str) -> dict:
        """Persist caller-derived per-edge sampling weights in place.

        Validates length, finiteness and non-negativity, rewrites the
        snapshot atomically (staging directory, authenticated load,
        backup-swap with restore), then refreshes this object. The
        snapshot identity, compact IDs, adjacency and source version are
        untouched — weights are a derived view, not new data — while
        ``edge_weight_source`` records their provenance (for example
        ``"policy:degree"`` or ``"manual:confidence-v3"``). No live readers
        may hold this snapshot path across the call.
        """
        if self.metadata.get("format_version") != SNAPSHOT_FORMAT_VERSION:
            raise ValueError("set_edge_weights requires a current-format snapshot; rebuild legacy snapshots first")
        if self.metadata.get("temporal") is True:
            raise ValueError("temporal snapshots do not support set_edge_weights; rebuild explicitly")
        if not isinstance(source, str) or not source:
            raise ValueError("source must be a non-empty provenance label")
        edge_weights = _normalize_edge_weights(weights, int(self.src_int.size))
        metadata = dict(self.metadata)
        metadata["edge_weight_source"] = source
        self._replace(
            self.path, metadata, self.external_node_ids.tolist(), self.node_type_ids,
            self.external_edge_ids.tolist(), self.external_relation_ids.tolist(),
            self.relation_src_type_ids, self.relation_dst_type_ids,
            self.src_int, self.dst_int, self.rel_int, edge_weights, self.out, self.in_,
            self.incident, self.relation_out, self.relation_in, self.positive_triples)
        self._refresh_from_disk()
        return {
            "edge_weight_source": source,
            "weight_mean": float(edge_weights.mean()) if edge_weights.size else 1.0,
            "weight_min": float(cast(Callable[[], np.float64], edge_weights.min)()) if edge_weights.size else 1.0,
            "weight_max": float(cast(Callable[[], np.float64], edge_weights.max)()) if edge_weights.size else 1.0,
            "snapshot_id": metadata["snapshot_id"],
        }

    def _refresh_from_disk(self) -> None:
        """Reload persisted state into this object after an in-place rewrite."""
        fresh = type(self).load(self.path)
        for name in self.__dataclass_fields__:
            setattr(self, name, getattr(fresh, name))

    @classmethod
    def _replace(cls, path: Path, metadata: dict, node_ids, node_type_ids, edge_ids, relations, relation_src_type_ids, relation_dst_type_ids, src_int, dst_int, rel_int, edge_weights, out, in_, incident, relation_out, relation_in, positive_triples) -> None:
        """Atomically replace the snapshot at an existing path.

        Writes and authenticates a staging directory exactly like a fresh
        build, then swaps it over the live directory with backup/restore so
        a failed swap never leaves a half-written snapshot. Callers must
        guarantee no live readers (including memmap loads) during the swap.
        """
        path = Path(path)
        if not (path / "metadata.json").is_file():
            raise ValueError(f"no snapshot to replace at: {path}")
        staging = Path(tempfile.mkdtemp(prefix=f".{path.name}.", dir=path.parent))
        backup = path.parent / f".{path.name}.backup"
        try:
            cls._write(staging / "snapshot", metadata, node_ids, node_type_ids, edge_ids, relations, relation_src_type_ids, relation_dst_type_ids, src_int, dst_int, rel_int, edge_weights, out, in_, incident, relation_out, relation_in, positive_triples)
            staged = staging / "snapshot"
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(path, backup)
            try:
                os.replace(staged, path)
            except Exception:
                os.replace(backup, path)
                raise
            shutil.rmtree(backup, ignore_errors=True)
            shutil.rmtree(staging, ignore_errors=True)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def external_node_id(self, node_int) -> str:
        return str(self.external_node_ids[int(node_int)])

    def external_edge_id(self, edge_int) -> str:
        return str(self.external_edge_ids[int(edge_int)])

    def external_relation_id(self, rel_int) -> str:
        return str(self.external_relation_ids[int(rel_int)])

    def node_int(self, external_node_id) -> int:
        """Resolve one external node ID to its compact integer ID."""
        return int(self.node_ints([external_node_id])[0])

    def node_ints(self, external_node_ids: Sequence[object]) -> IntArray:
        """Resolve external node IDs to compact integer IDs."""
        lookup_ids, lookup_order = self._lookup_arrays("node")
        return self._resolve_external_ids(external_node_ids, self.external_node_ids, lookup_ids, lookup_order, "node")

    def edge_int(self, external_edge_id) -> int:
        """Resolve one external edge ID to its compact integer ID."""
        return int(self.edge_ints([external_edge_id])[0])

    def edge_ints(self, external_edge_ids: Sequence[object]) -> IntArray:
        """Resolve external edge IDs to compact integer IDs."""
        lookup_ids, lookup_order = self._lookup_arrays("edge")
        return self._resolve_external_ids(external_edge_ids, self.external_edge_ids, lookup_ids, lookup_order, "edge")

    def relation_int(self, external_relation_id) -> int:
        """Resolve one external relation ID to its compact integer ID."""
        return int(self.relation_ints([external_relation_id])[0])

    def relation_ints(self, external_relation_ids: Sequence[object]) -> IntArray:
        """Resolve external relation IDs to compact integer IDs."""
        lookup_ids, lookup_order = self._lookup_arrays("relation")
        return self._resolve_external_ids(external_relation_ids, self.external_relation_ids, lookup_ids, lookup_order, "relation")

    def expand_relations(self, relations: Sequence[str], hierarchy: RelationHierarchy) -> IntArray:
        """Expand parent relation names through a sub-property hierarchy.

        Each name resolves to itself plus all transitive sub-properties,
        then to compact IDs usable by every engine method. Unknown snapshot
        relations fail closed; hierarchy-undefined names expand to
        themselves. Fanout caps in downstream sampling apply to the union,
        not per expanded relation.
        """
        if not isinstance(hierarchy, RelationHierarchy):
            raise ValueError("hierarchy must be a RelationHierarchy")
        expanded = hierarchy.expand(relations)
        known = set(self.external_relation_ids.tolist())
        unknown = [name for name in expanded if name not in known]
        if unknown:
            raise ValueError(f"unknown snapshot relations: {unknown!r}")
        return self.relation_ints(list(expanded))

    def expand_node_types(
        self, node_types: Sequence[str], hierarchy: LabelHierarchy
    ) -> IntArray:
        """Expand semantic node-type names through a sub-type hierarchy.

        Each name resolves to itself plus all transitive sub-types, then to the
        compact node-type IDs recorded in snapshot metadata. Unknown snapshot
        types fail closed; hierarchy-undefined names expand to themselves.
        Snapshots built from raw ``node_type_ids`` without recorded
        ``node_type_values`` cannot be expanded safely and fail closed.
        """
        if not isinstance(hierarchy, LabelHierarchy):
            raise ValueError("hierarchy must be a LabelHierarchy")
        expanded = hierarchy.expand(node_types)
        mapping = {
            str(value): int(index)
            for index, value in (self.metadata.get("node_type_values") or {}).items()
        }
        if not mapping:
            raise ValueError(
                "snapshot has no recorded node type values; build with "
                "node_type_values to expand a hierarchy"
            )
        unknown = [name for name in expanded if name not in mapping]
        if unknown:
            raise ValueError(f"unknown snapshot node types: {unknown!r}")
        return np.asarray(sorted({mapping[name] for name in expanded}), dtype=np.int64)

    def global_triple_to_external(self, triple) -> tuple[str, str, str]:
        src, rel, dst = np.asarray(triple, dtype=np.int64).tolist()
        return (self.external_node_id(src), self.external_relation_id(rel), self.external_node_id(dst))

    def local_triple_to_external(self, batch, triple) -> tuple[str, str, str]:
        src, rel, dst = np.asarray(triple, dtype=np.int64).tolist()
        return (
            self.external_node_id(batch.node_ids_global[int(src)]),
            self.external_relation_id(rel),
            self.external_node_id(batch.node_ids_global[int(dst)]),
        )

    def get_node(self, graph, node_int):
        provenance = self.metadata.get("source_provenance")
        if provenance is not None:
            source = parse_read_provenance(provenance)
            if isinstance(source, CurrentReadProvenance):
                source.verify_source(graph)
                with graph.current_read_view(capture_provenance=False) as view:
                    source.verify_snapshot_store(view)
                    return view.get_node(self.external_node_id(node_int).encode("utf-8"))
            source.verify_source(graph)
            version = graph.temporal.get_node_as_of(
                self.external_node_id(node_int),
                valid_time=source.valid_time_us,
                through_commit=source.commit_horizon,
            )
            return None if version is None else version.node
        return graph.get_node(self.external_node_id(node_int).encode("utf-8"))

    def get_edge(self, graph, edge_int):
        provenance = self.metadata.get("source_provenance")
        if provenance is not None:
            source = parse_read_provenance(provenance)
            if isinstance(source, CurrentReadProvenance):
                source.verify_source(graph)
                with graph.current_read_view(capture_provenance=False) as view:
                    source.verify_snapshot_store(view)
                    return view.get_edge(self.external_edge_id(edge_int).encode("utf-8"))
            source.verify_source(graph)
            if self.temporal:
                version = graph.temporal.get_edge_version(
                    str(self.edge_version_ids[int(edge_int)]),
                    through_commit=source.commit_horizon,
                )
            else:
                version = graph.temporal.get_edge_as_of(
                    self.external_edge_id(edge_int),
                    valid_time=source.valid_time_us,
                    through_commit=source.commit_horizon,
                )
            return None if version is None else version.edge
        return graph.get_edge(self.external_edge_id(edge_int).encode("utf-8"))

    def _lookup_arrays(self, entity: str) -> tuple[StringArray, IntArray]:
        external_ids = getattr(self, f"external_{entity}_ids")
        lookup_ids = getattr(self, f"{entity}_lookup_ids")
        lookup_order = getattr(self, f"{entity}_lookup_order")
        if lookup_ids.size != external_ids.size or lookup_order.size != external_ids.size:
            lookup_order = _build_lookup_order(external_ids, entity, require_unique=False)
            lookup_ids = external_ids[lookup_order]
            setattr(self, f"{entity}_lookup_ids", lookup_ids)
            setattr(self, f"{entity}_lookup_order", lookup_order)
        return lookup_ids, lookup_order

    @staticmethod
    def _resolve_external_ids(values: Sequence[object], external_ids: StringArray, lookup_ids: StringArray, lookup_order: IntArray, entity: str) -> IntArray:
        if isinstance(values, (str, bytes)):
            raise TypeError(f"external {entity} IDs must be a sequence, not a scalar string")
        targets_list = np.asarray(list(values), dtype=str).tolist()
        if not targets_list:
            return np.empty(0, dtype=np.int64)
        targets = np.asarray(targets_list, dtype=str)
        left = np.searchsorted(lookup_ids, targets, side="left")
        right = np.searchsorted(lookup_ids, targets, side="right")
        matched = left < lookup_ids.size
        matched_values = np.zeros(targets.size, dtype=bool)
        if np.any(matched):
            matched_values[matched] = lookup_ids[left[matched]] == targets[matched]
        missing = [targets_list[idx] for idx in np.flatnonzero(~matched_values).tolist()]
        if missing:
            raise KeyError(f"unknown external {entity} IDs: {list(dict.fromkeys(missing))!r}")
        ambiguous = [targets_list[idx] for idx in np.flatnonzero((right - left) > 1).tolist()]
        if ambiguous:
            raise ValueError(f"ambiguous external {entity} IDs: {list(dict.fromkeys(ambiguous))!r}")
        compact_ids = lookup_order[left].astype(np.int64, copy=False)
        if np.any(external_ids[compact_ids] != targets):
            raise ValueError(f"{entity} lookup arrays do not match external IDs")
        return compact_ids

    def _resolve_source_graph_path(self, *, base_path=None) -> Path:
        source_db = self.metadata.get("source_db")
        if not source_db:
            raise ValueError("SamplerSnapshot has no source_db metadata. Rebuild it with source_db=... or pass a GraphDB handle explicitly.")
        raw_path = source_db.get("path")
        if not raw_path:
            raise ValueError("SamplerSnapshot source_db metadata is missing a path")
        path_type = source_db.get("path_type", "relative_to_snapshot")
        raw_path = Path(raw_path)
        if path_type == "absolute":
            return raw_path
        if path_type == "relative_to_snapshot":
            return (self.path / raw_path).resolve()
        if path_type == "relative_to_project":
            if base_path is None:
                raise ValueError("base_path is required to resolve source_db path_type='relative_to_project'")
            return (Path(base_path) / raw_path).resolve()
        raise ValueError(f"unknown source_db path_type: {path_type}")

    @staticmethod
    def _validate_edge_arrays(external_node_ids: StringArray, external_edge_ids: StringArray, external_relation_ids: StringArray, src_int: IntArray, dst_int: IntArray, rel_int: IntArray) -> None:
        if not (src_int.size == dst_int.size == rel_int.size == external_edge_ids.size):
            raise ValueError("src_int, dst_int, rel_int, and external_edge_ids must have the same length")
        if src_int.size == 0:
            return
        if cast(Callable[[], np.int64], src_int.min)() < 0 or cast(Callable[[], np.int64], dst_int.min)() < 0 or cast(Callable[[], np.int64], rel_int.min)() < 0:
            raise ValueError("compact IDs must be non-negative")
        if cast(Callable[[], np.int64], src_int.max)() >= external_node_ids.size or cast(Callable[[], np.int64], dst_int.max)() >= external_node_ids.size:
            raise ValueError("src_int and dst_int must reference external_node_ids")
        if cast(Callable[[], np.int64], rel_int.max)() >= external_relation_ids.size:
            raise ValueError("rel_int must reference external_relation_ids")

    @classmethod
    def _validate_v2_files(cls, path: Path, metadata: dict, metadata_bytes: bytes) -> None:
        try:
            completion = json.loads((path / _COMPLETION_FILENAME).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("sampler snapshot is incomplete") from exc
        if not isinstance(completion, dict):
            raise ValueError("invalid sampler snapshot completion manifest")
        if completion.get("format_version") != SNAPSHOT_FORMAT_VERSION or completion.get("complete") is not True:
            raise ValueError("invalid sampler snapshot completion manifest")
        if set(completion) != {"format_version", "complete", "metadata_sha256", "artifact_catalog_sha256"}:
            raise ValueError("invalid sampler snapshot completion manifest")
        if completion.get("metadata_sha256") != hashlib.sha256(metadata_bytes).hexdigest():
            raise ValueError("sampler snapshot metadata checksum mismatch")
        catalog = metadata.get("artifacts")
        if not isinstance(catalog, dict) or completion.get("artifact_catalog_sha256") != hashlib.sha256(
            _canonical_json_bytes(catalog)
        ).hexdigest():
            raise ValueError("invalid sampler snapshot artifact catalog")
        if "temporal" in metadata and not isinstance(metadata["temporal"], bool):
            raise ValueError("invalid sampler snapshot temporal flag")
        expected = {
            "external_node_ids", "node_type_ids", "external_edge_ids", "external_relation_ids",
            "relation_src_type_ids", "relation_dst_type_ids", "src_int", "dst_int", "rel_int",
            "out_indptr", "out_edge_indices", "in_indptr", "in_edge_indices", "incident_indptr",
            "incident_edge_indices", "relation_out_indptr", "relation_out_edge_indices",
            "relation_in_indptr", "relation_in_edge_indices", "positive_triples",
        }
        if metadata.get("sampling_policy_arrays") is True:
            expected.add("edge_weights")
            for entity in ("node", "edge", "relation"):
                expected.update({f"{entity}_lookup_ids", f"{entity}_lookup_order"})
        elif metadata.get("sampling_policy_arrays") not in {None, False}:
            raise ValueError("invalid sampling policy array flag")
        if metadata.get("temporal"):
            expected.update({
                "edge_version_ids", "valid_from_us", "valid_to_us", "valid_to_open",
                "edge_system_time_us", "edge_commit_ids", "temporal_out_indptr",
                "temporal_out_edge_indices", "temporal_in_indptr", "temporal_in_edge_indices",
            })
        if sparse_encoding(metadata):
            expected.update(f"{name}_row_keys" for name in NAMES + (TEMPORAL_NAMES if metadata.get("temporal") else ()))
        if metadata.get("temporal_history_indexes") is True:
            expected.update({
                "positive_history_triples", "positive_history_indptr",
                "positive_history_edge_indices", "node_history_indptr",
                "node_valid_from_us", "node_valid_to_us", "node_valid_to_open",
            })
        elif metadata.get("temporal_history_indexes") not in {None, False}:
            raise ValueError("invalid temporal history index flag")
        if has_membership_encoding(metadata) and not metadata.get("temporal"):
            expected.add(MEMBERSHIP_ARTIFACT)
        if set(catalog) != expected:
            raise ValueError("sampler snapshot artifact catalog is incomplete or contains unknown arrays")
        for name, record in catalog.items():
            if not isinstance(record, dict) or set(record) != {"sha256", "dtype", "shape"}:
                raise ValueError("invalid sampler snapshot artifact record")
            checksum = record.get("sha256")
            if not isinstance(checksum, str) or len(checksum) != 64 or any(character not in "0123456789abcdef" for character in checksum):
                raise ValueError("invalid sampler snapshot artifact checksum")
            artifact = path / f"{name}.npy"
            if not artifact.is_file() or _sha256_file(artifact) != checksum:
                raise ValueError(f"sampler snapshot artifact checksum mismatch: {name}")
            try:
                array = np.load(artifact, mmap_mode="r", allow_pickle=False)
            except (OSError, ValueError) as exc:
                raise ValueError(f"invalid sampler snapshot artifact: {name}") from exc
            if str(array.dtype) != record.get("dtype") or list(array.shape) != record.get("shape"):
                raise ValueError(f"sampler snapshot artifact schema mismatch: {name}")
        if metadata.get("temporal"):
            if "source_provenance" not in metadata:
                raise ValueError("temporal sampler snapshot lacks source provenance")
            ReadViewProvenance.from_dict(metadata["source_provenance"])
            encoding = metadata.get("temporal_encoding")
            bucket = None if not isinstance(encoding, dict) else encoding.get("time_bucket")
            if (
                not isinstance(encoding, dict)
                or encoding.get("instant") != "signed_utc_epoch_microseconds"
                or encoding.get("interval") != "half_open"
                or encoding.get("open_end") != "valid_to_open_mask"
                or bucket not in _TIME_BUCKET_US
                or encoding.get("time_bucket_us") != _TIME_BUCKET_US[bucket]
                or encoding.get("index_order") != ["endpoint", "relation", "valid_from_us", "edge_index"]
            ):
                raise ValueError("invalid temporal snapshot encoding")
        elif "source_provenance" in metadata:
            parse_read_provenance(metadata["source_provenance"])

    def _validate_v2_arrays(self) -> None:
        node_count = self.num_nodes
        edge_count = self.num_edges
        relation_count = self.num_relations
        if self.metadata.get("node_count") != node_count or self.metadata.get("edge_count") != edge_count or self.metadata.get("relation_count") != relation_count:
            raise ValueError("sampler snapshot counts do not match arrays")
        if self.node_type_ids.shape != (node_count,) or self.external_edge_ids.shape != (edge_count,):
            raise ValueError("sampler snapshot entity arrays are misaligned")
        integer_arrays = (
            self.node_type_ids, self.relation_src_type_ids, self.relation_dst_type_ids,
            self.src_int, self.dst_int, self.rel_int, self.positive_triples,
        )
        if any(array.dtype != np.dtype("int64") for array in integer_arrays):
            raise ValueError("sampler snapshot integer arrays must use int64")
        if self.external_node_ids.dtype.kind != "U" or self.external_edge_ids.dtype.kind != "U" or self.external_relation_ids.dtype.kind != "U":
            raise ValueError("sampler snapshot external ID arrays must use Unicode strings")
        if self.relation_src_type_ids.shape != (relation_count,) or self.relation_dst_type_ids.shape != (relation_count,):
            raise ValueError("sampler snapshot relation type arrays are misaligned")
        self._validate_edge_arrays(
            self.external_node_ids, self.external_edge_ids, self.external_relation_ids,
            self.src_int, self.dst_int, self.rel_int,
        )
        if self.positive_triples.shape != (edge_count, 3) or not np.array_equal(
            self.positive_triples,
            np.stack([self.src_int, self.rel_int, self.dst_int], axis=1) if edge_count else np.empty((0, 3), dtype=np.int64),
        ):
            raise ValueError("sampler snapshot positive triples are inconsistent")
        if has_membership_encoding(self.metadata) and not self.temporal:
            validate_membership(self.positive_membership_triples, self.positive_triples)
        relation_size = max(1, node_count * max(1, relation_count))
        edge_indices = np.arange(edge_count, dtype=np.int64)
        relation_multiplier = max(1, relation_count)
        self._validate_csr("out", self.out, node_count, edge_count, expected_keys=self.src_int)
        self._validate_csr("in", self.in_, node_count, edge_count, expected_keys=self.dst_int)
        self._validate_csr(
            "incident",
            self.incident,
            node_count,
            edge_count,
            expected_keys=np.concatenate([self.src_int, self.dst_int]),
            expected_edge_indices=np.concatenate([edge_indices, edge_indices]),
        )
        relation_out_keys = self.src_int * relation_multiplier + self.rel_int
        relation_in_keys = self.dst_int * relation_multiplier + self.rel_int
        self._validate_csr(
            "relation_out", self.relation_out, relation_size, edge_count,
            expected_keys=relation_out_keys,
        )
        self._validate_csr(
            "relation_in", self.relation_in, relation_size, edge_count,
            expected_keys=relation_in_keys,
        )
        if self.temporal:
            # Narrow optional temporal arrays (all present when temporal holds).
            edge_version_ids, valid_from_us, valid_to_us, valid_to_open, edge_system_time_us, edge_commit_ids = cast(StringArray, self.edge_version_ids), cast(IntArray, self.valid_from_us), cast(IntArray, self.valid_to_us), cast(BoolArray, self.valid_to_open), cast(IntArray, self.edge_system_time_us), cast(IntArray, self.edge_commit_ids)
            temporal_arrays = (
                edge_version_ids, valid_from_us, valid_to_us, valid_to_open,
                edge_system_time_us, edge_commit_ids,
            )
            if any(array.shape != (edge_count,) for array in temporal_arrays):
                raise ValueError("temporal snapshot arrays are misaligned")
            if edge_version_ids.dtype.kind != "U" or valid_to_open.dtype != np.dtype("bool"):
                raise ValueError("temporal snapshot arrays use invalid dtypes")
            if any(array.dtype != np.dtype("int64") for array in (
                valid_from_us, valid_to_us, edge_system_time_us, edge_commit_ids
            )):
                raise ValueError("temporal snapshot integer arrays must use int64")
            finite = ~valid_to_open
            if np.any(valid_to_us[finite] <= valid_from_us[finite]):
                raise ValueError("temporal snapshot contains an empty or reversed interval")
            if np.any(valid_to_us[valid_to_open] != 0):
                raise ValueError("open temporal intervals must use a zero valid-to sentinel")
            if np.any(edge_commit_ids <= 0):
                raise ValueError("temporal snapshot contains an invalid commit ID")
            provenance = ReadViewProvenance.from_dict(self.metadata["source_provenance"])
            if np.any(edge_commit_ids > provenance.commit_horizon):
                raise ValueError("temporal snapshot contains an edge beyond its source horizon")
            if provenance.commit_system_time_us is None:
                if edge_count:
                    raise ValueError("empty source history cannot contain temporal edges")
            elif np.any(edge_system_time_us > provenance.commit_system_time_us):
                raise ValueError("temporal snapshot contains an edge beyond its source system time")
            for value in edge_version_ids.tolist():
                try:
                    if str(uuid.UUID(value)) != value:
                        raise ValueError
                except (AttributeError, TypeError, ValueError) as exc:
                    raise ValueError("temporal snapshot contains an invalid edge version ID") from exc
            self._validate_csr(
                "temporal_out", self.temporal_out, relation_size, edge_count,
                expected_keys=relation_out_keys,
            )
            self._validate_csr(
                "temporal_in", self.temporal_in, relation_size, edge_count,
                expected_keys=relation_in_keys,
            )
            for adjacency in (cast(CSRAdjacency, self.temporal_out), cast(CSRAdjacency, self.temporal_in)):
                for start, end in zip(adjacency.indptr[:-1], adjacency.indptr[1:]):
                    candidates = adjacency.edge_indices[int(start):int(end)]
                    if np.any(valid_from_us[candidates][1:] < valid_from_us[candidates][:-1]):
                        raise ValueError("temporal snapshot index is not ordered by valid start")
            history_triples, history_indptr, history_edge_indices = cast(IntArray, self.positive_history_triples), cast(IntArray, self.positive_history_indptr), cast(IntArray, self.positive_history_edge_indices)
            node_indptr, node_from_us, node_to_us, node_to_open = cast(IntArray, self.node_history_indptr), cast(IntArray, self.node_valid_from_us), cast(IntArray, self.node_valid_to_us), cast(BoolArray, self.node_valid_to_open)
            history_count = history_triples.shape[0]
            if history_triples.ndim != 2 or history_triples.shape[1] != 3:
                raise ValueError("temporal positive-history triples are invalid")
            if history_triples.dtype != np.dtype("int64"):
                raise ValueError("temporal positive-history triples must use int64")
            if history_indptr.shape != (history_count + 1,) or history_edge_indices.shape != (edge_count,):
                raise ValueError("temporal positive-history index is misaligned")
            expected_triples, expected_indptr, expected_edges = _build_positive_history_index(
                self.src_int, self.rel_int, self.dst_int, valid_from_us
            )
            if not (
                np.array_equal(history_triples, expected_triples)
                and np.array_equal(history_indptr, expected_indptr)
                and np.array_equal(history_edge_indices, expected_edges)
            ):
                raise ValueError("temporal positive-history index is inconsistent")
            node_interval_count = int(node_from_us.size)
            if node_indptr.shape != (node_count + 1,) or not (
                node_to_us.shape == (node_interval_count,)
                and node_to_open.shape == (node_interval_count,)
                and node_indptr[0] == 0
                and node_indptr[-1] == node_interval_count
                and np.all(node_indptr[1:] >= node_indptr[:-1])
            ):
                raise ValueError("temporal node-history index is invalid")
            if any(array.dtype != np.dtype("int64") for array in (
                history_indptr, history_edge_indices,
                node_indptr, node_from_us, node_to_us,
            )) or node_to_open.dtype != np.dtype("bool"):
                raise ValueError("temporal history indexes use invalid dtypes")
            finite_nodes = ~node_to_open
            if np.any(node_to_us[finite_nodes] <= node_from_us[finite_nodes]):
                raise ValueError("temporal node history contains an invalid interval")
            if np.any(node_to_us[node_to_open] != 0):
                raise ValueError("open node intervals must use a zero valid-to sentinel")
            for node in range(node_count):
                start = int(node_indptr[node])
                end = int(node_indptr[node + 1])
                starts = node_from_us[start:end]
                ends = node_to_us[start:end]
                open_ends = node_to_open[start:end]
                if np.any(starts[1:] < starts[:-1]):
                    raise ValueError("temporal node-history index is not ordered")
                if len(starts) > 1:
                    previous_ends = ends[:-1]
                    if np.any(open_ends[:-1]) or np.any(previous_ends >= starts[1:]):
                        raise ValueError("temporal node-history intervals overlap")

    def _derive_temporal_history_indexes(self) -> None:
        valid_from_us, valid_to_open, valid_to_us = cast(IntArray, self.valid_from_us), cast(BoolArray, self.valid_to_open), cast(IntArray, self.valid_to_us)
        triples, indptr, edges = _build_positive_history_index(
            self.src_int, self.rel_int, self.dst_int, valid_from_us
        )
        node_indptr = [0]
        node_starts = []
        node_ends = []
        node_open = []
        for node in range(self.num_nodes):
            intervals = _merge_intervals([
                (
                    int(valid_from_us[edge]),
                    None if valid_to_open[edge] else int(valid_to_us[edge]),
                )
                for edge in range(self.num_edges)
                if self.src_int[edge] == node or self.dst_int[edge] == node
            ])
            for start, end in intervals:
                node_starts.append(start)
                node_ends.append(0 if end is None else end)
                node_open.append(end is None)
            node_indptr.append(len(node_starts))
        self.positive_history_triples = triples
        self.positive_history_indptr = indptr
        self.positive_history_edge_indices = edges
        self.node_history_indptr = np.asarray(node_indptr, dtype=np.int64)
        self.node_valid_from_us = np.asarray(node_starts, dtype=np.int64)
        self.node_valid_to_us = np.asarray(node_ends, dtype=np.int64)
        self.node_valid_to_open = np.asarray(node_open, dtype=np.bool_)

    @staticmethod
    def _validate_csr(
        name,
        adjacency,
        size,
        edge_count,
        *,
        expected_keys,
        expected_edge_indices=None,
    ) -> None:
        validate_csr(name, adjacency, size, edge_count, expected_keys=expected_keys,
                     expected_edge_indices=expected_edge_indices)

    @staticmethod
    def _normalize_source_reference(source_db: dict, snapshot_path: Path) -> dict:
        source = dict(source_db)
        if "path" not in source:
            raise ValueError("source_db must include a path")
        path = Path(source["path"])
        path_type = source.get("path_type")
        if path_type is None:
            path_type = "absolute" if path.is_absolute() else "relative_to_snapshot"
        if path_type == "relative_to_snapshot" and path.is_absolute():
            path = Path(os.path.relpath(path, start=snapshot_path))
        elif path_type == "absolute":
            path = path.resolve()
        elif path_type not in {"relative_to_snapshot", "relative_to_project"}:
            raise ValueError("source_db path_type must be 'relative_to_snapshot', 'relative_to_project', or 'absolute'")
        source["path"] = str(path)
        source["path_type"] = path_type
        if "manifest_path" not in source and path_type != "relative_to_project":
            source["manifest_path"] = str(path / "gestaltdb_manifest.json")
        source.setdefault("graph_fingerprint", None)
        return source

    @staticmethod
    def _normalize_artifact_references(source_artifacts: dict, snapshot_path: Path) -> dict:
        artifacts = {}
        for name, raw_path in source_artifacts.items():
            path = Path(raw_path)
            artifacts[name] = str(Path(os.path.relpath(path, start=snapshot_path)) if path.is_absolute() else path)
        return artifacts

    @staticmethod
    def _derive_relation_endpoint_types(node_type_ids: IntArray, src_int: IntArray, dst_int: IntArray, rel_int: IntArray, relation_count: int, *, node_type_mapping: dict[str, int] | None = None, edge_src_type_values: Sequence[object] | None = None, edge_dst_type_values: Sequence[object] | None = None) -> tuple[IntArray, IntArray]:
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

    @classmethod
    def _write(cls, path: Path, metadata: dict, node_ids, node_type_ids, edge_ids, relations, relation_src_type_ids, relation_dst_type_ids, src_int, dst_int, rel_int, edge_weights, out, in_, incident, relation_out, relation_in, positive_triples, *, temporal_values=None, node_intervals=None) -> None:
        if path.exists():
            raise ValueError(f"snapshot output already exists: {path}")
        external_node_ids = np.asarray(node_ids, dtype=str)
        external_edge_ids = np.asarray(edge_ids, dtype=str)
        external_relation_ids = np.asarray(relations, dtype=str)
        node_lookup_order = _build_lookup_order(external_node_ids, "node")
        edge_lookup_order = _build_lookup_order(external_edge_ids, "edge", require_unique=temporal_values is None)
        relation_lookup_order = _build_lookup_order(external_relation_ids, "relation")
        node_lookup_ids = external_node_ids[node_lookup_order]
        edge_lookup_ids = external_edge_ids[edge_lookup_order]
        relation_lookup_ids = external_relation_ids[relation_lookup_order]
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{path.name}.", dir=path.parent))
        arrays = {
            "external_node_ids": external_node_ids,
            "node_type_ids": node_type_ids,
            "external_edge_ids": external_edge_ids,
            "external_relation_ids": external_relation_ids,
            "relation_src_type_ids": relation_src_type_ids,
            "relation_dst_type_ids": relation_dst_type_ids,
            "src_int": src_int,
            "dst_int": dst_int,
            "rel_int": rel_int,
            "edge_weights": edge_weights,
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
            "node_lookup_order": node_lookup_order,
            "edge_lookup_order": edge_lookup_order,
            "relation_lookup_order": relation_lookup_order,
            "node_lookup_ids": node_lookup_ids,
            "edge_lookup_ids": edge_lookup_ids,
            "relation_lookup_ids": relation_lookup_ids,
        }
        if temporal_values is None:
            arrays[MEMBERSHIP_ARTIFACT] = build_membership(positive_triples)
        arrays.update(sparse_arrays("relation_out", relation_out))
        arrays.update(sparse_arrays("relation_in", relation_in))
        if temporal_values is not None:
            edge_version_ids, valid_from_us, valid_to_us, valid_to_open, edge_system_time_us, edge_commit_ids = temporal_values
            edge_indices = np.arange(src_int.size, dtype=np.int64)
            relation_count = len(relations)
            relation_size = max(1, len(node_ids) * max(1, relation_count))
            temporal_out = _build_temporal_csr(
                src_int * max(1, relation_count) + rel_int, valid_from_us, edge_indices, relation_size
            )
            temporal_in = _build_temporal_csr(
                dst_int * max(1, relation_count) + rel_int, valid_from_us, edge_indices, relation_size
            )
            arrays.update({
                "edge_version_ids": edge_version_ids,
                "valid_from_us": valid_from_us,
                "valid_to_us": valid_to_us,
                "valid_to_open": valid_to_open,
                "edge_system_time_us": edge_system_time_us,
                "edge_commit_ids": edge_commit_ids,
                "temporal_out_indptr": temporal_out.indptr,
                "temporal_out_edge_indices": temporal_out.edge_indices,
                "temporal_in_indptr": temporal_in.indptr,
                "temporal_in_edge_indices": temporal_in.edge_indices,
            })
            arrays.update(sparse_arrays("temporal_out", temporal_out))
            arrays.update(sparse_arrays("temporal_in", temporal_in))
            history_triples, history_indptr, history_edges = _build_positive_history_index(
                src_int, rel_int, dst_int, valid_from_us
            )
            node_history_indptr = [0]
            node_starts = []
            node_ends = []
            node_open = []
            for intervals in node_intervals:
                for start, end in intervals:
                    node_starts.append(start)
                    node_ends.append(0 if end is None else end)
                    node_open.append(end is None)
                node_history_indptr.append(len(node_starts))
            arrays.update({
                "positive_history_triples": history_triples,
                "positive_history_indptr": history_indptr,
                "positive_history_edge_indices": history_edges,
                "node_history_indptr": np.asarray(node_history_indptr, dtype=np.int64),
                "node_valid_from_us": np.asarray(node_starts, dtype=np.int64),
                "node_valid_to_us": np.asarray(node_ends, dtype=np.int64),
                "node_valid_to_open": np.asarray(node_open, dtype=np.bool_),
            })
        try:
            catalog = {}
            for name, value in arrays.items():
                array = np.asarray(value)
                artifact = staging / f"{name}.npy"
                np.save(artifact, array, allow_pickle=False)
                catalog[name] = {
                    "sha256": _sha256_file(artifact),
                    "dtype": str(array.dtype),
                    "shape": list(array.shape),
                }
            metadata = dict(metadata)
            metadata["positive_membership_encoding"] = MEMBERSHIP_ENCODING
            metadata["relation_adjacency_encoding"] = ENCODING
            metadata["sampling_policy_arrays"] = True
            if temporal_values is not None:
                metadata["temporal_history_indexes"] = True
            metadata["format_version"] = SNAPSHOT_FORMAT_VERSION
            metadata["artifacts"] = catalog
            metadata_bytes = _canonical_json_bytes(metadata)
            (staging / "metadata.json").write_bytes(metadata_bytes)
            completion = {
                "format_version": SNAPSHOT_FORMAT_VERSION,
                "complete": True,
                "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
                "artifact_catalog_sha256": hashlib.sha256(_canonical_json_bytes(catalog)).hexdigest(),
            }
            (staging / _COMPLETION_FILENAME).write_bytes(_canonical_json_bytes(completion))
            cls.load(staging)
            os.replace(staging, path)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise


def _consistency_schema_hash(params: dict) -> str:
    """Hash the snapshot interpretation contract (never the data vocab)."""
    return hashlib.sha256(_canonical_json_bytes(params)).hexdigest()


def _build_sampling_indexes(node_count: int, relation_count: int, src_int: IntArray, dst_int: IntArray, rel_int: IntArray) -> tuple[CSRAdjacency, CSRAdjacency, CSRAdjacency, CSRAdjacency, CSRAdjacency, IntArray]:
    """Rebuild every CSR adjacency plus positive triples from edge rows."""
    src_int = np.asarray(src_int, dtype=np.int64)
    dst_int = np.asarray(dst_int, dtype=np.int64)
    rel_int = np.asarray(rel_int, dtype=np.int64)
    edge_indices = np.arange(src_int.size, dtype=np.int64)
    out = build_csr(src_int, edge_indices, node_count)
    in_ = build_csr(dst_int, edge_indices, node_count)
    incident = build_csr(
        np.concatenate([src_int, dst_int]) if src_int.size else np.empty(0, dtype=np.int64),
        np.concatenate([edge_indices, edge_indices]) if edge_indices.size else np.empty(0, dtype=np.int64),
        node_count,
    )
    relation_size = max(1, node_count * max(1, relation_count))
    relation_out = build_sparse_csr(src_int * max(1, relation_count) + rel_int, edge_indices, relation_size)
    relation_in = build_sparse_csr(dst_int * max(1, relation_count) + rel_int, edge_indices, relation_size)
    positive_triples = np.stack([src_int, rel_int, dst_int], axis=1) if src_int.size else np.empty((0, 3), dtype=np.int64)
    return out, in_, incident, relation_out, relation_in, positive_triples


def _encode_type_values(values: Sequence[object], *, expected_size: int) -> tuple[IntArray, dict[str, int]]:
    values = list(values)
    if len(values) != expected_size:
        raise ValueError("node_type_values length must match external_node_ids")
    labels = sorted({str(value) for value in values if value is not None})
    mapping = {label: idx for idx, label in enumerate(labels)}
    encoded = np.asarray([mapping.get(str(value), -1) if value is not None else -1 for value in values], dtype=np.int64)
    return encoded, mapping


def _encode_with_mapping(values: Iterable[object], mapping: dict[str, int]) -> IntArray:
    return np.asarray([mapping.get(str(value), -1) if value is not None else -1 for value in values], dtype=np.int64)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _build_temporal_csr(keys: IntArray, valid_from_us: IntArray, edge_indices: IntArray, size: int) -> CSRAdjacency:
    return build_sparse_csr(keys, edge_indices, size, starts=valid_from_us)


def _subtract_intervals(start: int, end: int | None, covered: Iterable[tuple[int, int | None]]) -> list[tuple[int, int | None]]:
    pieces = [(start, end)]
    for cover_start, cover_end in covered:
        next_pieces = []
        for piece_start, piece_end in pieces:
            if (cover_end is not None and cover_end <= piece_start) or (piece_end is not None and cover_start >= piece_end):
                next_pieces.append((piece_start, piece_end))
                continue
            if cover_start > piece_start:
                next_pieces.append((piece_start, min(cover_start, piece_end) if piece_end is not None else cover_start))
            if cover_end is not None and (piece_end is None or cover_end < piece_end):
                next_pieces.append((max(piece_start, cover_end), piece_end))
        pieces = next_pieces
        if not pieces:
            break
    return pieces


def _merge_intervals(intervals: Iterable[tuple[int, int | None]]) -> list[tuple[int, int | None]]:
    merged: list[tuple[int, int | None]] = []
    for start, end in sorted(intervals, key=lambda item: item[0]):
        if not merged:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        if previous_end is None or start <= previous_end:
            merged[-1] = (
                previous_start,
                None if previous_end is None or end is None else max(previous_end, end),
            )
        else:
            merged.append((start, end))
    return merged


def _validate_weight(value) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("edge weights must be numeric") from exc
    if not np.isfinite(weight) or weight < 0:
        raise ValueError("edge weights must be finite and non-negative")
    return weight


def _normalize_edge_weights(edge_weights: Sequence[float] | Array | None, edge_count: int) -> FloatArray:
    if edge_weights is None:
        return np.ones(edge_count, dtype=np.float64)
    weights = np.asarray(edge_weights, dtype=np.float64)
    if weights.ndim != 1 or weights.size != edge_count:
        raise ValueError("edge_weights length must match the number of edges")
    if not np.all(np.isfinite(weights)) or np.any(weights < 0):
        raise ValueError("edge_weights must be finite and non-negative")
    return weights


def _integer_id_array(values: Sequence[int] | Array, name: str) -> IntArray:
    ids = np.asarray(values)
    if ids.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if ids.size and (np.issubdtype(ids.dtype, np.bool_) or not np.issubdtype(ids.dtype, np.integer)):
        raise ValueError(f"{name} must contain integer IDs")
    return ids.astype(np.int64, copy=False)


def _build_lookup_order(external_ids: StringArray, entity: str, *, require_unique: bool = True) -> IntArray:
    order = np.argsort(external_ids, kind="stable").astype(np.int64, copy=False)
    if require_unique and order.size > 1:
        sorted_ids = external_ids[order]
        duplicate_positions = np.flatnonzero(sorted_ids[1:] == sorted_ids[:-1])
        if duplicate_positions.size:
            duplicate = str(sorted_ids[int(duplicate_positions[0])])
            raise ValueError(f"external {entity} IDs must be unique; duplicate {duplicate!r}")
    return order
