FROM gcr.io/oss-fuzz-base/base-builder-python:v1@sha256:aa1326f311d899ed3ca050a89b6c1e3528e2aa86be1ce5180aaddf3567547a99
RUN python3 -m pip install --upgrade pip
COPY . $SRC/gestaltdb
WORKDIR $SRC/gestaltdb
COPY .clusterfuzzlite/build.sh $SRC/build.sh
