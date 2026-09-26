#!/bin/bash -eu
# ClusterFuzzLite fuzzer build script. Compiles the atheris fuzz targets in
# tests/fuzz/ into $OUT using the OSS-Fuzz base-builder-python helpers.
pip install atheris
pip install -e "$SRC/gestaltdb[msgpack]"
compile_python_fuzzer "$SRC/gestaltdb/tests/fuzz/fuzz_serializers.py"
