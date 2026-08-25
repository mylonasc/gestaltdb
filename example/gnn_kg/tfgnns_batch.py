from __future__ import annotations

import numpy as np
import tensorflow as tf

from .sampler import SampledBatch


def sampled_batch_to_tensor_dict(batch: SampledBatch) -> tuple[dict[str, tf.Tensor], dict[str, tf.Tensor]]:
    graph = {
        "nodes": tf.convert_to_tensor(np.stack([batch.node_global_ids, batch.node_type_ids], axis=1), dtype=tf.float32),
        "edges": tf.convert_to_tensor(batch.edge_rel_ids[:, None], dtype=tf.float32),
        "senders": tf.convert_to_tensor(batch.senders, dtype=tf.int64),
        "receivers": tf.convert_to_tensor(batch.receivers, dtype=tf.int64),
        "n_nodes": tf.convert_to_tensor([len(batch.node_ids)], dtype=tf.int64),
        "n_edges": tf.convert_to_tensor([len(batch.edge_rel_ids)], dtype=tf.int64),
        "n_graphs": tf.convert_to_tensor(1, dtype=tf.int64),
    }
    labels = {
        "positive_src": tf.convert_to_tensor(batch.positive_src, dtype=tf.int64),
        "positive_dst": tf.convert_to_tensor(batch.positive_dst, dtype=tf.int64),
        "positive_rel": tf.convert_to_tensor(batch.positive_rel, dtype=tf.int64),
        "negative_src": tf.convert_to_tensor(batch.negative_src, dtype=tf.int64),
        "negative_dst": tf.convert_to_tensor(batch.negative_dst, dtype=tf.int64),
        "negative_rel": tf.convert_to_tensor(batch.negative_rel, dtype=tf.int64),
    }
    return graph, labels


def output_signature(negatives_per_positive: int):
    graph_sig = {
        "nodes": tf.TensorSpec(shape=(None, 2), dtype=tf.float32),
        "edges": tf.TensorSpec(shape=(None, 1), dtype=tf.float32),
        "senders": tf.TensorSpec(shape=(None,), dtype=tf.int64),
        "receivers": tf.TensorSpec(shape=(None,), dtype=tf.int64),
        "n_nodes": tf.TensorSpec(shape=(1,), dtype=tf.int64),
        "n_edges": tf.TensorSpec(shape=(1,), dtype=tf.int64),
        "n_graphs": tf.TensorSpec(shape=(), dtype=tf.int64),
    }
    labels_sig = {
        "positive_src": tf.TensorSpec(shape=(None,), dtype=tf.int64),
        "positive_dst": tf.TensorSpec(shape=(None,), dtype=tf.int64),
        "positive_rel": tf.TensorSpec(shape=(None,), dtype=tf.int64),
        "negative_src": tf.TensorSpec(shape=(None, negatives_per_positive), dtype=tf.int64),
        "negative_dst": tf.TensorSpec(shape=(None, negatives_per_positive), dtype=tf.int64),
        "negative_rel": tf.TensorSpec(shape=(None, negatives_per_positive), dtype=tf.int64),
    }
    return graph_sig, labels_sig
