#!/usr/bin/env bash

set -uo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REPORT_DIR=${REPORT_DIR:-"${ROOT_DIR}/security-reports"}
UV_IMAGE=${UV_IMAGE:-"ghcr.io/astral-sh/uv:0.12.19"}
TRIVY_IMAGE=${TRIVY_IMAGE:-"aquasec/trivy:0.74.0"}
REPORT_SEVERITY=${REPORT_SEVERITY:-"UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL"}
FAIL_ON_SEVERITY=${FAIL_ON_SEVERITY:-""}
TRIVY_CACHE_DIR=${TRIVY_CACHE_DIR:-"${XDG_CACHE_HOME:-${HOME}/.cache}/gestaltdb-trivy"}
BACKENDS=(leveldb lmdb rocksdb)

if ! command -v docker >/dev/null 2>&1; then
    printf 'docker is required to run the backend vulnerability scan.\n' >&2
    exit 2
fi

mkdir -p "${REPORT_DIR}" "${TRIVY_CACHE_DIR}"
rm -f "${REPORT_DIR}"/{leveldb,lmdb,rocksdb}.{json,txt}
WORK_DIR=$(mktemp -d)
trap 'rm -rf "${WORK_DIR}"' EXIT
mkdir -p "${WORK_DIR}/uv-cache"

docker_user=(--user "$(id -u):$(id -g)")
scan_status=0

for backend in "${BACKENDS[@]}"; do
    backend_dir="${WORK_DIR}/${backend}"
    mkdir -p "${backend_dir}"

    printf '\nExporting locked %s runtime dependencies...\n' "${backend}"
    if ! docker run --rm "${docker_user[@]}" \
        --env UV_CACHE_DIR=/uv-cache \
        --volume "${ROOT_DIR}:/workspace:ro" \
        --volume "${backend_dir}:/output" \
        --volume "${WORK_DIR}/uv-cache:/uv-cache" \
        --workdir /workspace \
        "${UV_IMAGE}" \
        export --quiet --frozen --no-dev --no-editable --no-emit-project --no-hashes \
        --extra "${backend}" --output-file /output/requirements.txt; then
        printf 'Failed to export dependencies for %s.\n' "${backend}" >&2
        scan_status=1
        continue
    fi

    printf 'Scanning %s dependencies with Trivy...\n' "${backend}"
    if ! docker run --rm "${docker_user[@]}" \
        --volume "${backend_dir}:/scan:ro" \
        --volume "${REPORT_DIR}:/reports" \
        --volume "${TRIVY_CACHE_DIR}:/cache" \
        "${TRIVY_IMAGE}" fs --scanners vuln --pkg-types library \
        --cache-dir /cache --severity "${REPORT_SEVERITY}" \
        --format json --output "/reports/${backend}.json" /scan; then
        scan_status=1
        continue
    fi

    if ! docker run --rm "${docker_user[@]}" \
        --volume "${backend_dir}:/scan:ro" \
        --volume "${REPORT_DIR}:/reports" \
        --volume "${TRIVY_CACHE_DIR}:/cache" \
        "${TRIVY_IMAGE}" fs --scanners vuln --pkg-types library \
        --cache-dir /cache --severity "${REPORT_SEVERITY}" \
        --format table --output "/reports/${backend}.txt" /scan; then
        scan_status=1
        continue
    fi

    if [[ -n "${FAIL_ON_SEVERITY}" ]]; then
        if ! docker run --rm "${docker_user[@]}" \
            --volume "${backend_dir}:/scan:ro" \
            --volume "${TRIVY_CACHE_DIR}:/cache" \
            "${TRIVY_IMAGE}" fs --scanners vuln --pkg-types library \
            --cache-dir /cache --severity "${FAIL_ON_SEVERITY}" \
            --exit-code 1 --format table --output /dev/null /scan; then
            printf '%s has vulnerabilities at policy severity %s.\n' \
                "${backend}" "${FAIL_ON_SEVERITY}" >&2
            scan_status=1
        fi
    fi
done

printf '\nReports written to %s\n' "${REPORT_DIR}"
exit "${scan_status}"
