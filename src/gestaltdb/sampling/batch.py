"""Framework-neutral sampled batch containers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class SampledSubgraphBatch:
    """Framework-neutral sampled subgraph batch.

    Node IDs inside ``senders``, ``receivers``, ``positives``, and ``negatives``
    are local to this batch. ``node_ids_global`` maps each local node row back to
    the compact global node ID in the ``SamplerSnapshot``.

    Attributes:
        node_ids_global: Compact global node IDs included in this batch.
        node_type_ids: Node type IDs aligned with ``node_ids_global``.
        senders: Local source node IDs for sampled edges.
        receivers: Local target node IDs for sampled edges.
        edge_ids_global: Compact global edge IDs included in this batch.
        edge_relation_ids: Relation IDs aligned with ``edge_ids_global``.
        positives: Local positive triples shaped ``(num_positives, 3)``.
        negatives: Local negative triples, usually shaped ``(num_positives,
            negatives_per_positive, 3)``.
        graph_node_offsets: Optional graph component offsets for packed batches.
        graph_edge_offsets: Optional edge component offsets for packed batches.
        edge_version_ids: Edge-version IDs aligned with ``edge_ids_global`` for
            temporal snapshots, otherwise ``None``.
        valid_from_us: Inclusive valid starts aligned with sampled edges.
        valid_to_us: Exclusive valid ends, with zero for open intervals.
        valid_to_open: Open-end masks aligned with sampled edges.

    Examples:
        Convert to plain NumPy arrays::

            arrays = batch.to_numpy()
            edge_index = np.stack([arrays["senders"], arrays["receivers"]])
    """

    node_ids_global: np.ndarray
    node_type_ids: np.ndarray
    senders: np.ndarray
    receivers: np.ndarray
    edge_ids_global: np.ndarray
    edge_relation_ids: np.ndarray
    positives: np.ndarray
    negatives: np.ndarray
    graph_node_offsets: np.ndarray | None = None
    graph_edge_offsets: np.ndarray | None = None
    edge_version_ids: np.ndarray | None = None
    valid_from_us: np.ndarray | None = None
    valid_to_us: np.ndarray | None = None
    valid_to_open: np.ndarray | None = None

    @property
    def n_nodes(self) -> int:
        return int(self.node_ids_global.size)

    @property
    def n_edges(self) -> int:
        return int(self.edge_ids_global.size)

    def to_numpy(self) -> dict[str, np.ndarray]:
        """Return arrays without framework-specific conversion.

        Returns:
            Dictionary containing NumPy arrays for nodes, edges, positives, and
            negatives. Optional graph offsets are included when present.
        """
        result = {
            "node_ids_global": self.node_ids_global,
            "node_type_ids": self.node_type_ids,
            "senders": self.senders,
            "receivers": self.receivers,
            "edge_ids_global": self.edge_ids_global,
            "edge_relation_ids": self.edge_relation_ids,
            "positives": self.positives,
            "negatives": self.negatives,
        }
        if self.edge_version_ids is not None:
            result.update({
                "edge_version_ids": self.edge_version_ids,
                "valid_from_us": self.valid_from_us,
                "valid_to_us": self.valid_to_us,
                "valid_to_open": self.valid_to_open,
            })
        if self.graph_node_offsets is not None:
            result["graph_node_offsets"] = self.graph_node_offsets
        if self.graph_edge_offsets is not None:
            result["graph_edge_offsets"] = self.graph_edge_offsets
        return result

    def to_arrow(self):
        """Return a PyArrow table for nodes and edges plus triple arrays.

        Returns:
            Dictionary with Arrow tables for ``nodes``, ``edges``, ``positives``,
            and ``negatives``. If negatives are grouped, a
            ``negative_group_offsets`` Arrow array is included.

        Raises:
            ImportError: If ``pyarrow`` is not installed.

        Examples:
            Export node and edge tables::

                arrow_batch = batch.to_arrow()
                node_table = arrow_batch["nodes"]
        """
        try:
            import pyarrow as pa
        except ImportError as exc:
            raise ImportError("Missing optional dependency 'pyarrow' required for SampledSubgraphBatch.to_arrow") from exc
        positives = self.positives.reshape(-1, 3) if self.positives.size else np.empty((0, 3), dtype=np.int64)
        negatives = self.negatives.reshape(-1, 3) if self.negatives.size else np.empty((0, 3), dtype=np.int64)
        edge_columns = {
            "edge_id_global": self.edge_ids_global,
            "sender": self.senders,
            "receiver": self.receivers,
            "relation_id": self.edge_relation_ids,
        }
        if self.edge_version_ids is not None:
            edge_columns.update({
                "edge_version_id": self.edge_version_ids,
                "valid_from_us": self.valid_from_us,
                "valid_to_us": self.valid_to_us,
                "valid_to_open": self.valid_to_open,
            })
        result = {
            "nodes": pa.table({"node_id_global": self.node_ids_global, "node_type_id": self.node_type_ids}),
            "edges": pa.table(edge_columns),
            "positives": pa.table({"src": positives[:, 0], "rel": positives[:, 1], "dst": positives[:, 2]}),
            "negatives": pa.table({"src": negatives[:, 0], "rel": negatives[:, 1], "dst": negatives[:, 2]}),
        }
        if self.negatives.ndim == 3:
            result["negative_group_offsets"] = pa.array(np.arange(self.negatives.shape[0] + 1) * self.negatives.shape[1])
        return result

    def to_pyg(self):
        """Return a PyTorch Geometric-style dictionary of tensors.

        Returns:
            Dictionary with ``edge_index``, node/edge type tensors, global ID
            tensors, positive/negative label tensors, and temporal edge-version
            IDs as string-array metadata when present.

        Raises:
            ImportError: If ``torch`` is not installed.
        """
        try:
            import torch
        except ImportError as exc:
            raise ImportError("Missing optional dependency 'torch' required for SampledSubgraphBatch.to_pyg") from exc
        result = {
            "num_nodes": self.n_nodes,
            "edge_index": torch.as_tensor(np.stack([self.senders, self.receivers]), dtype=torch.long),
            "node_type": torch.as_tensor(self.node_type_ids, dtype=torch.long),
            "edge_type": torch.as_tensor(self.edge_relation_ids, dtype=torch.long),
            "node_id_global": torch.as_tensor(self.node_ids_global, dtype=torch.long),
            "edge_id_global": torch.as_tensor(self.edge_ids_global, dtype=torch.long),
            "positives": torch.as_tensor(self.positives, dtype=torch.long),
            "negatives": torch.as_tensor(self.negatives, dtype=torch.long),
        }
        if self.edge_version_ids is not None:
            result.update({
                "edge_version_ids": self.edge_version_ids.copy(),
                "valid_from_us": torch.as_tensor(self.valid_from_us, dtype=torch.long),
                "valid_to_us": torch.as_tensor(self.valid_to_us, dtype=torch.long),
                "valid_to_open": torch.as_tensor(self.valid_to_open, dtype=torch.bool),
            })
        return result

    def to_tf_gnns(self):
        """Return a TensorFlow-friendly graph dictionary and labels.

        Returns:
            Tuple ``(graph, labels)`` where both entries contain TensorFlow
            tensors. The graph dictionary follows the lightweight tensor-dict
            format used by the TarKG example.

        Raises:
            ImportError: If ``tensorflow`` is not installed.
        """
        try:
            import tensorflow as tf
        except ImportError as exc:
            raise ImportError("Missing optional dependency 'tensorflow' required for SampledSubgraphBatch.to_tf_gnns") from exc
        graph = {
            "nodes": tf.convert_to_tensor(self.node_ids_global, dtype=tf.int64),
            "node_type_ids": tf.convert_to_tensor(self.node_type_ids, dtype=tf.int64),
            "edges": tf.convert_to_tensor(self.edge_ids_global, dtype=tf.int64),
            "edge_relation_ids": tf.convert_to_tensor(self.edge_relation_ids, dtype=tf.int64),
            "senders": tf.convert_to_tensor(self.senders, dtype=tf.int64),
            "receivers": tf.convert_to_tensor(self.receivers, dtype=tf.int64),
            "n_nodes": tf.convert_to_tensor([self.n_nodes], dtype=tf.int64),
            "n_edges": tf.convert_to_tensor([self.n_edges], dtype=tf.int64),
            "n_graphs": tf.convert_to_tensor([1], dtype=tf.int64),
        }
        if self.edge_version_ids is not None:
            graph.update({
                "edge_version_ids": tf.convert_to_tensor(self.edge_version_ids, dtype=tf.string),
                "valid_from_us": tf.convert_to_tensor(self.valid_from_us, dtype=tf.int64),
                "valid_to_us": tf.convert_to_tensor(self.valid_to_us, dtype=tf.int64),
                "valid_to_open": tf.convert_to_tensor(self.valid_to_open, dtype=tf.bool),
            })
        labels = {
            "positives": tf.convert_to_tensor(self.positives, dtype=tf.int64),
            "negatives": tf.convert_to_tensor(self.negatives, dtype=tf.int64),
        }
        return graph, labels

    def to_dgl(self):
        """Return a DGL graph with node/edge features and label tensors.

        Returns:
            Tuple ``(graph, labels)`` where ``graph`` is a DGL graph and labels
            contains positive and negative PyTorch tensors. Temporal graphs keep
            string edge-version IDs on ``graph.edge_version_ids``.

        Raises:
            ImportError: If ``dgl`` or ``torch`` is not installed.
        """
        try:
            import dgl
            import torch
        except ImportError as exc:
            raise ImportError("Missing optional dependencies 'dgl' and 'torch' required for SampledSubgraphBatch.to_dgl") from exc
        graph = dgl.graph((torch.as_tensor(self.senders, dtype=torch.long), torch.as_tensor(self.receivers, dtype=torch.long)), num_nodes=self.n_nodes)
        graph.ndata["type_id"] = torch.as_tensor(self.node_type_ids, dtype=torch.long)
        graph.ndata["global_id"] = torch.as_tensor(self.node_ids_global, dtype=torch.long)
        graph.edata["relation_id"] = torch.as_tensor(self.edge_relation_ids, dtype=torch.long)
        graph.edata["global_id"] = torch.as_tensor(self.edge_ids_global, dtype=torch.long)
        if self.edge_version_ids is not None:
            graph.edge_version_ids = self.edge_version_ids.copy()
            graph.edata["valid_from_us"] = torch.as_tensor(self.valid_from_us, dtype=torch.long)
            graph.edata["valid_to_us"] = torch.as_tensor(self.valid_to_us, dtype=torch.long)
            graph.edata["valid_to_open"] = torch.as_tensor(self.valid_to_open, dtype=torch.bool)
        return graph, {
            "positives": torch.as_tensor(self.positives, dtype=torch.long),
            "negatives": torch.as_tensor(self.negatives, dtype=torch.long),
        }
