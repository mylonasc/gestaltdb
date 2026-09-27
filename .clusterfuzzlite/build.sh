#!/bin/bash -eu
# ClusterFuzzLite fuzzer build script. Compiles the atheris fuzz targets in
# tests/fuzz/ into $OUT using the OSS-Fuzz base-builder-python helpers.
#
# The target stages a minimal gestaltdb package so serializers can import the
# temporal value module without loading the full package __init__.
pip install atheris "msgpack>=1.2.1"
WORK="$SRC/fuzzbuild"
mkdir -p "$WORK"
cp "$SRC/gestaltdb/tests/fuzz/fuzz_serializers.py" "$WORK/"
mkdir -p "$WORK/gestaltdb"
touch "$WORK/gestaltdb/__init__.py"
cp "$SRC/gestaltdb/src/gestaltdb/serializers.py" "$WORK/gestaltdb/"
cp "$SRC/gestaltdb/src/gestaltdb/temporal.py" "$WORK/gestaltdb/"
cd "$WORK"
compile_python_fuzzer fuzz_serializers.py
