from __future__ import annotations

import os

os.environ.setdefault("KERAS_BACKEND", "tensorflow")

import tensorflow as tf
import tf_gnns


def main() -> None:
    print("tensorflow", tf.__version__)
    print("tf_gnns", tf_gnns.__version__)
    print("keras backend", os.environ.get("KERAS_BACKEND"))
    gpus = tf.config.list_physical_devices("GPU")
    print("gpus", gpus)
    if not gpus:
        raise RuntimeError(
            "TensorFlow cannot see a GPU. Launch through example/gnn_kg/run_training.sh "
            "or install/select the CUDA Jupyter kernel from example/gnn_kg/install_cuda_kernel.py."
        )
    for gpu in gpus:
        tf.config.experimental.set_memory_growth(gpu, True)
    with tf.device("/GPU:0"):
        a = tf.random.uniform((1024, 1024))
        b = tf.linalg.matmul(a, a)
    print("gpu matmul device", b.device)
    if "GPU" not in b.device.upper():
        raise RuntimeError(f"TensorFlow listed a GPU but executed matmul on {b.device}")


if __name__ == "__main__":
    main()
