#!/usr/bin/env bash

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"

# Pin the exact Linux/Python resolver environment already used by the
# accepted dependency-parity gates.
PYTHON_IMAGE='python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea'
PIP_TOOLS_VERSION='7.6.1'

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if ! docker info >/dev/null 2>&1; then
  echo 'ERROR: Docker is not running'
  exit 1
fi

if [ ! -f "$ROOT/requirements.in" ]; then
  echo 'ERROR: requirements.in is missing'
  exit 1
fi

cp "$ROOT/requirements.in" "$TMP_DIR/requirements.in"

echo 'Compiling requirements.txt'
echo "python_image=$PYTHON_IMAGE"
echo "pip_tools=$PIP_TOOLS_VERSION"

docker run --rm \
  --platform linux/amd64 \
  -e PIP_DEFAULT_TIMEOUT=120 \
  -e PIP_RETRIES=8 \
  -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
  -v "$TMP_DIR:/work" \
  -w /work \
  "$PYTHON_IMAGE" \
  sh -lc '
    retry() {
      max="$1"
      shift
      attempt=1

      while true; do
        "$@" && return 0

        rc=$?

        if [ "$attempt" -ge "$max" ]; then
          echo "ERROR: command failed after $attempt attempts rc=$rc" >&2
          return "$rc"
        fi

        echo "WARN: attempt $attempt failed; retrying..." >&2
        sleep $((attempt * 5))
        attempt=$((attempt + 1))
      done
    }

    retry 3 \
      python -m pip install \
        --quiet \
        "pip-tools==7.6.1"

    retry 3 \
      pip-compile \
        --quiet \
        --resolver=backtracking \
        --strip-extras \
        --output-file=requirements.txt \
        requirements.in
  '

RC=$?

if [ "$RC" -ne 0 ]; then
  echo "ERROR: lock compilation failed rc=$RC"
  exit "$RC"
fi

if [ ! -s "$TMP_DIR/requirements.txt" ]; then
  echo 'ERROR: compiler produced an empty lock file'
  exit 1
fi

cp "$TMP_DIR/requirements.txt" "$ROOT/requirements.txt"

echo
echo 'requirements.txt regenerated successfully'

printf 'locked_packages='
grep -Ec '^[A-Za-z0-9_.-]+==' "$ROOT/requirements.txt"

printf 'sha256='
shasum -a 256 "$ROOT/requirements.txt" | awk '{print $1}'
