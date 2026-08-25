# TarKG GNN Sampler With GestaltDB and tf_gnns

This example prepares a large TarKG subset, ingests it into GestaltDB, samples hard contrastive GNN batches, and trains a Keras 3 TensorFlow-backend model with `mylonasc/tf_gnns`.

## Setup

```bash
pip install -r example/gnn_kg/requirements.txt
```

The scripts set `KERAS_BACKEND=tensorflow` before importing Keras/TensorFlow/tf_gnns.

## Prepare Data

Inspect the automatically selected high-count relation subset:

```bash
python example/gnn_kg/prepare_tarkg.py --dry-run
```

Build artifacts and ingest the selected subset into GestaltDB:

```bash
python example/gnn_kg/prepare_tarkg.py --reset-db
```

By default the selector uses the largest TarKG node-source sub-KGs, then targets about 14M edges by taking the largest relation groups after endpoint filtering. Data is written under `data/tarkg_gnn/`.

## Train

Validate TensorFlow, `tf_gnns`, and GPU visibility through the same CUDA library path used for training:

```bash
bash example/gnn_kg/run_training.sh --validate-runtime
```

```bash
bash example/gnn_kg/run_training.sh
```

For a small training smoke test after data preparation:

```bash
bash example/gnn_kg/run_training.sh --smoke-test
```

Inspect materialized subset stats:

```bash
python example/gnn_kg/inspect_subset.py
```

Use `--allow-cpu` only for import/path debugging. Normal training fails fast if TensorFlow cannot see a GPU.

The default training hot path uses an array-backed CSR sampler derived from the GestaltDB-prepared edge artifacts. This avoids thousands of random typed-adjacency KV lookups per batch. Use `--graphdb-sampler` only for debugging the direct GestaltDB lookup path.

Measured on an RTX 4090 with the 14.08M-edge subset:

- Direct GestaltDB lookup sampler: `0.155` batches/s at batch size 16.
- Array-backed sampler: `9.764` batches/s at batch size 16.
- End-to-end training: reached `9.91` cumulative steps/s by step 100 at batch size 16.
- End-to-end training: reached `3.88` cumulative steps/s by step 60 at batch size 64, which is higher positive-triple throughput.

## Batch Format

The training path does not use TensorFlow-GNN `GraphTensor`. It builds `tf_gnns` tensor dictionaries directly:

- `nodes`: compact node feature table
- `edges`: relation feature table
- `senders`, `receivers`: edge connectivity
- `n_nodes`, `n_edges`, `n_graphs`: packed graph metadata

The sampler returns NumPy arrays, then `tf.data.Dataset.from_generator` converts them to tensors with an explicit output signature.
