#!/usr/bin/env bash
# Build the Sphinx docs, including the ontology tutorial.
#
# Usage:
#   scripts/build_docs.sh [--examples] [--pdf] [--output DIR]
#
# Steps:
#   1. check_docs.py (API/docs drift gate)
#   2. sphinx -b html -W docs <output> (warnings are errors, like CI plus -W)
#   3. verify onto-tutorial pages exist in the output
#   4. optional --examples: tutorial cells + modular doc examples
#   5. optional --pdf: tutorial-only PDF, vector diagrams and typeset math
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${OUTPUT:-$ROOT/docs/_build/html}"
RUN_EXAMPLES=0
BUILD_PDF=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --examples) RUN_EXAMPLES=1; shift ;;
    --pdf) BUILD_PDF=1; shift ;;
    --output)
      if [[ $# -lt 2 || -z "$2" || "$2" == --* ]]; then
        echo "--output requires a directory" >&2
        exit 2
      fi
      OUTPUT="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: scripts/build_docs.sh [--examples] [--pdf] [--output DIR]";
      echo "--pdf also exports onto-tutorial/onto-tutorial.pdf (requires XeLaTeX).";
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

echo "==> verify onto-tutorial pages"
for page in index.html ch01-property-graph-vs-semantic.html ch08-gaps.html toolkit/index.html; do
  if [[ ! -f "$OUTPUT/onto-tutorial/$page" ]]; then
    echo "MISSING: onto-tutorial/$page" >&2
    exit 1
  fi
done
echo "onto-tutorial pages present."

if [[ "$BUILD_PDF" -eq 1 ]]; then
  echo "==> tutorial PDF (XeLaTeX, vector figures)"
  uv run --with sphinx --with sphinx-rtd-theme --with myst-parser --with cairosvg \
    python scripts/build_tutorial_pdf.py --output "$OUTPUT/onto-tutorial"
fi

if [[ "$RUN_EXAMPLES" -eq 1 ]]; then
  echo "==> tutorial chapter and notebook examples (LMDB)"
  uv run --extra lmdb pytest tests/test_ontology_tutorial.py -q
  echo "==> doc_tool.py test-examples"
  uv run --extra lmdb --extra leveldb --extra polars \
    python .opencode/skills/gestaltdb-docs-maintainer/scripts/doc_tool.py test-examples
fi

echo "OK: docs built at $OUTPUT"
