#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv-gnn-kg"
PYTHON="${VENV_DIR}/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
  echo "Missing ${PYTHON}. Create the venv and install requirements first:" >&2
  echo "  python3 -m venv .venv-gnn-kg" >&2
  echo "  .venv-gnn-kg/bin/python -m pip install -r example/gnn_kg/requirements.txt" >&2
  exit 1
fi

CUDA_LIB_PATH="$(${PYTHON} - <<'PY'
import pathlib
import site

root = pathlib.Path(site.getsitepackages()[0]) / "nvidia"
paths = [str(path / "lib") for path in root.iterdir() if (path / "lib").exists()] if root.exists() else []
print(":".join(paths))
PY
)"

export KERAS_BACKEND=tensorflow
if [[ -n "${CUDA_LIB_PATH}" ]]; then
  export LD_LIBRARY_PATH="${CUDA_LIB_PATH}:${LD_LIBRARY_PATH:-}"
fi

if [[ "${1:-}" == "--validate-runtime" ]]; then
  shift
  exec "${PYTHON}" "${ROOT_DIR}/example/gnn_kg/validate_runtime.py" "$@"
fi

exec "${PYTHON}" "${ROOT_DIR}/example/gnn_kg/train.py" "$@"
