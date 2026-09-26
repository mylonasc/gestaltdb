FROM gcr.io/oss-fuzz-base/base-builder-python:v1
RUN python3 -m pip install --upgrade pip
COPY . $SRC/gestaltdb
WORKDIR $SRC/gestaltdb
COPY .clusterfuzzlite/build_fuzzers.sh $SRC/build_fuzzers.sh
