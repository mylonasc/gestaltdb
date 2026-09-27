#!/usr/bin/env bash

set -uo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REPORT_DIR=${REPORT_DIR:-"${ROOT_DIR}/security-reports"}
UV_IMAGE=${UV_IMAGE:-"ghcr.io/astral-sh/uv:0.12.19"}
TRIVY_IMAGE=${TRIVY_IMAGE:-"aquasec/trivy:0.74.0"}
REPORT_SEVERITY=${REPORT_SEVERITY:-"UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL"}
FAIL_ON_SEVERITY=${FAIL_ON_SEVERITY:-""}
TRIVY_CACHE_DIR=${TRIVY_CACHE_DIR:-"${XDG_CACHE_HOME:-${HOME}/.cache}/gestaltdb-trivy"}
# Cartesian product of installable KV backends and serializers. `json`
# represents the builtin JSON/Pickle serializers, which need no extra.
BACKENDS=(leveldb lmdb rocksdb)
SERIALIZERS=(json msgpack protobuf)

if ! command -v docker >/dev/null 2>&1; then
    printf 'docker is required to run the backend vulnerability scan.\n' >&2
    exit 2
fi

mkdir -p "${REPORT_DIR}" "${TRIVY_CACHE_DIR}"
# Clean current cartesian reports plus legacy backend-only reports.
rm -f "${REPORT_DIR}"/leveldb-{json,msgpack,protobuf}.{json,txt} \
    "${REPORT_DIR}"/lmdb-{json,msgpack,protobuf}.{json,txt} \
    "${REPORT_DIR}"/rocksdb-{json,msgpack,protobuf}.{json,txt} \
    "${REPORT_DIR}"/{leveldb,lmdb,rocksdb}.{json,txt}
WORK_DIR=$(mktemp -d)
trap 'rm -rf "${WORK_DIR}"' EXIT
mkdir -p "${WORK_DIR}/uv-cache"

docker_user=(--user "$(id -u):$(id -g)")
scan_status=0

for backend in "${BACKENDS[@]}"; do
    for serializer in "${SERIALIZERS[@]}"; do
        combo="${backend}-${serializer}"
        combo_dir="${WORK_DIR}/${combo}"
        mkdir -p "${combo_dir}"

        extra_args=(--extra "${backend}")
        if [[ "${serializer}" != "json" ]]; then
            extra_args+=(--extra "${serializer}")
        fi

        printf '\nExporting locked %s runtime dependencies (extras: %s)...\n' \
            "${combo}" "${extra_args[*]#--extra }"
        if ! docker run --rm "${docker_user[@]}" \
            --env UV_CACHE_DIR=/uv-cache \
            --volume "${ROOT_DIR}:/workspace:ro" \
            --volume "${combo_dir}:/output" \
            --volume "${WORK_DIR}/uv-cache:/uv-cache" \
            --workdir /workspace \
            "${UV_IMAGE}" \
            export --quiet --frozen --no-dev --no-editable --no-emit-project --no-hashes \
            "${extra_args[@]}" --output-file /output/requirements.txt; then
            printf 'Failed to export dependencies for %s.\n' "${combo}" >&2
            scan_status=1
            continue
        fi

        printf 'Scanning %s dependencies with Trivy...\n' "${combo}"
        if ! docker run --rm "${docker_user[@]}" \
            --volume "${combo_dir}:/scan:ro" \
            --volume "${REPORT_DIR}:/reports" \
            --volume "${TRIVY_CACHE_DIR}:/cache" \
            "${TRIVY_IMAGE}" fs --scanners vuln --pkg-types library \
            --cache-dir /cache --severity "${REPORT_SEVERITY}" \
            --format json --output "/reports/${combo}.json" /scan; then
            scan_status=1
            continue
        fi

        if ! docker run --rm "${docker_user[@]}" \
            --volume "${combo_dir}:/scan:ro" \
            --volume "${REPORT_DIR}:/reports" \
            --volume "${TRIVY_CACHE_DIR}:/cache" \
            "${TRIVY_IMAGE}" fs --scanners vuln --pkg-types library \
            --cache-dir /cache --severity "${REPORT_SEVERITY}" \
            --format table --output "/reports/${combo}.txt" /scan; then
            scan_status=1
            continue
        fi

        if [[ -n "${FAIL_ON_SEVERITY}" ]]; then
            if ! docker run --rm "${docker_user[@]}" \
                --volume "${combo_dir}:/scan:ro" \
                --volume "${TRIVY_CACHE_DIR}:/cache" \
                "${TRIVY_IMAGE}" fs --scanners vuln --pkg-types library \
                --cache-dir /cache --severity "${FAIL_ON_SEVERITY}" \
                --exit-code 1 --format table --output /dev/null /scan; then
                printf '%s has vulnerabilities at policy severity %s.\n' \
                    "${combo}" "${FAIL_ON_SEVERITY}" >&2
                scan_status=1
            fi
        fi
    done
done

printf '\nReports written to %s\n' "${REPORT_DIR}"
exit "${scan_status}"
