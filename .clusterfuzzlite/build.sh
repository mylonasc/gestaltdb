#!/bin/bash -eu
# ClusterFuzzLite fuzzer build script. Compiles the atheris fuzz targets in
# tests/fuzz/ into $OUT using the OSS-Fuzz base-builder-python helpers.
#
# The target fuzzes src/gestaltdb/serializers.py standalone: the module is
# staged next to the target so the packaged fuzzer does not import the
# gestaltdb package __init__ (which pulls numpy in via sampling and breaks
# the PyInstaller bundle).
pip install atheris "msgpack>=1.2.1"
WORK="$SRC/fuzzbuild"
mkdir -p "$WORK"
cp "$SRC/gestaltdb/tests/fuzz/fuzz_serializers.py" "$WORK/"
cp "$SRC/gestaltdb/src/gestaltdb/serializers.py" "$WORK/"
cd "$WORK"
compile_python_fuzzer fuzz_serializers.py
