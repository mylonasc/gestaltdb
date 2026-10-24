#!/usr/bin/env bash
# Build the library documentation. The tutorial uses build_tutorial.sh.
#
# Usage:
#   scripts/build_docs.sh [--examples] [--output DIR]
#
# Steps:
#   1. check_docs.py (API/docs drift gate)
#   2. sphinx -b html -W docs <output> (warnings are errors, like CI plus -W)
#   3. optional --examples: modular doc examples
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${OUTPUT:-$ROOT/docs/_build/html}"
RUN_EXAMPLES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --examples) RUN_EXAMPLES=1; shift ;;
    --output)
      if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "--output requires a directory" >&2
        exit 2
      fi
      OUTPUT="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: scripts/build_docs.sh [--examples] [--output DIR]";
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

cd "$ROOT"

echo "==> check_docs.py (drift gate)"
uv run python .opencode/skills/gestaltdb-docs-maintainer/scripts/check_docs.py

echo "==> sphinx build docs -> $OUTPUT"
uv run --with sphinx --with sphinx-rtd-theme --with myst-parser \
  python -m sphinx -b html -W docs "$OUTPUT"

if [[ "$RUN_EXAMPLES" -eq 1 ]]; then
  echo "==> doc_tool.py test-examples"
  uv run --extra lmdb --extra leveldb --extra polars \
    python .opencode/skills/gestaltdb-docs-maintainer/scripts/doc_tool.py test-examples
fi

echo "OK: docs built at $OUTPUT"
