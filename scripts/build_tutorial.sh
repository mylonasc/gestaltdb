#!/usr/bin/env bash
# Build only the standalone tutorial, never the library reference.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${OUTPUT:-$ROOT/docs/onto-tutorial/_build/html}"
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
      echo "Usage: scripts/build_tutorial.sh [--examples] [--pdf] [--output DIR]"
      echo "--pdf exports onto-tutorial.pdf alongside the standalone HTML site."
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done
cd "$ROOT"
uv run --with sphinx --with sphinx-rtd-theme --with myst-parser --with sphinx-copybutton --with sphinx-design \
  python -m sphinx -b html -W docs/onto-tutorial "$OUTPUT"
if [[ "$BUILD_PDF" -eq 1 ]]; then
  uv run --with sphinx --with sphinx-rtd-theme --with myst-parser --with sphinx-copybutton --with sphinx-design --with cairosvg \
    python scripts/build_tutorial_pdf.py --output "$OUTPUT"
fi
if [[ "$RUN_EXAMPLES" -eq 1 ]]; then
  uv run --extra lmdb --with sphinx --with sphinx-rtd-theme --with myst-parser --with sphinx-copybutton --with sphinx-design \
    pytest tests/test_ontology_tutorial.py -q
fi
echo "OK: standalone tutorial built at $OUTPUT"
