from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("KERAS_BACKEND", "tensorflow")

import tensorflow as tf

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from example.gnn_kg.config import make_config
    from example.gnn_kg.losses import contrastive_infonce_loss
    from example.gnn_kg.model import KGContrastiveModel
    from example.gnn_kg.sampler import KGNeighborhoodSampler, make_sampler
    from example.gnn_kg.tfgnns_batch import output_signature, sampled_batch_to_tensor_dict
else:
    from .config import make_config
    from .losses import contrastive_infonce_loss
    from .model import KGContrastiveModel
    from .sampler import KGNeighborhoodSampler, make_sampler
    from .tfgnns_batch import output_signature, sampled_batch_to_tensor_dict


def validate_gpu() -> None:
    gpus = tf.config.list_physical_devices("GPU")
    if not gpus:
        raise RuntimeError("TensorFlow cannot see a GPU. Check TensorFlow/CUDA installation before training.")
    for gpu in gpus:
        tf.config.experimental.set_memory_growth(gpu, True)
    print("TensorFlow GPUs:", gpus)


def open_graphdb(cfg):
    src_path = cfg.paths.project_root / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    from gestaltdb.graphdb import GraphDB

    return GraphDB.open(cfg.paths.db_path)


def dataset_from_sampler(sampler: KGNeighborhoodSampler, prefetch: int):
    def gen():
        while True:
            yield sampled_batch_to_tensor_dict(sampler.sample_batch())

    return tf.data.Dataset.from_generator(
        gen,
        output_signature=output_signature(sampler.cfg.sampler.negatives_per_positive),
    ).prefetch(prefetch)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--allow-cpu", action="store_true")
    parser.add_argument("--steps", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--log-every", type=int, default=None)
    parser.add_argument("--graphdb-sampler", action="store_true")
    args = parser.parse_args()
    cfg = make_config(smoke_test=args.smoke_test)
    if args.steps is not None:
        cfg.training.steps = args.steps
    if args.batch_size is not None:
        cfg.sampler.batch_size = args.batch_size
    if args.log_every is not None:
        cfg.training.log_every = args.log_every
    if args.graphdb_sampler:
        cfg.sampler.use_array_backend = False
    if not args.allow_cpu:
        validate_gpu()
    graphdb = None if cfg.sampler.use_array_backend else open_graphdb(cfg)
    try:
        sampler = make_sampler(cfg, graphdb)
        ds = dataset_from_sampler(sampler, cfg.training.prefetch)
        model = KGContrastiveModel(
            cfg.model,
            num_nodes=sampler.num_nodes,
            num_node_types=sampler.num_node_types,
            num_relations=sampler.num_relations,
        )
        opt = tf.keras.optimizers.Adam(learning_rate=cfg.training.learning_rate)

        train_step_signature = output_signature(cfg.sampler.negatives_per_positive)

        @tf.function(input_signature=train_step_signature, reduce_retracing=True)
        def train_step(graph, labels):
            with tf.GradientTape() as tape:
                pos, neg = model(graph, labels, training=True)
                loss = contrastive_infonce_loss(pos, neg, cfg.model.temperature)
            grads = tape.gradient(loss, model.trainable_variables)
            grads_and_vars = [(grad, var) for grad, var in zip(grads, model.trainable_variables) if grad is not None]
            opt.apply_gradients(grads_and_vars)
            return loss, tf.shape(graph["nodes"])[0], tf.shape(graph["edges"])[0]

        t0 = time.perf_counter()
        for step, (graph, labels) in enumerate(ds.take(cfg.training.steps), start=1):
            loss, n_nodes, n_edges = train_step(graph, labels)
            if step == 1 or step % cfg.training.log_every == 0:
                elapsed = max(time.perf_counter() - t0, 1e-6)
                print(
                    f"step={step} loss={float(loss):.4f} nodes={int(n_nodes)} "
                    f"edges={int(n_edges)} steps_per_s={step / elapsed:.2f}"
                )
    finally:
        if graphdb is not None:
            graphdb.close()


if __name__ == "__main__":
    main()
