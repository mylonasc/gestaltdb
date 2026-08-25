from __future__ import annotations

import json
import site
import sys
from pathlib import Path


def cuda_library_path() -> str:
    """Return CUDA wheel library paths required by TensorFlow.

    TensorFlow's pip wheels load CUDA libraries with ``dlopen``. On this system,
    setting ``LD_LIBRARY_PATH`` inside an already-running Python process is too
    late; the Jupyter kernel process must start with the NVIDIA wheel ``lib``
    directories in its environment.
    """
    root = Path(site.getsitepackages()[0]) / "nvidia"
    if not root.exists():
        return ""
    return ":".join(str(path / "lib") for path in sorted(root.iterdir()) if (path / "lib").exists())


def main() -> None:
    project_root = Path(__file__).resolve().parents[2]
    kernel_name = "gestaltdb-gnn-kg-cuda"
    display_name = "GestaltDB GNN KG (CUDA)"
    python = Path(sys.executable)
    cuda_paths = cuda_library_path()
    pythonpath = f"{project_root}:{project_root / 'src'}"
    kernel_dir = Path.home() / ".local" / "share" / "jupyter" / "kernels" / kernel_name
    kernel_dir.mkdir(parents=True, exist_ok=True)
    spec = {
        "argv": [str(python), "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": display_name,
        "language": "python",
        "env": {
            "KERAS_BACKEND": "tensorflow",
            "LD_LIBRARY_PATH": f"{cuda_paths}:$LD_LIBRARY_PATH" if cuda_paths else "$LD_LIBRARY_PATH",
            "PYTHONPATH": f"{pythonpath}:$PYTHONPATH",
        },
    }
    (kernel_dir / "kernel.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    print(f"Installed Jupyter kernel: {display_name}")
    print(f"Kernel spec: {kernel_dir / 'kernel.json'}")
    print(f"Python: {python}")
    print(f"CUDA library path: {cuda_paths}")


if __name__ == "__main__":
    main()
