#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

LOCK_FILE="${TMPDIR:-/tmp}/masyg-extractor-deploy.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "ERROR: Another Masyg Extractor deployment is already running."
  exit 1
fi

echo
echo "=============================================="
echo " MASYG EXTRACTOR PRODUCTION DEPLOY"
echo "=============================================="
echo "Directory: $APP_DIR"
echo "Started:   $(date)"
echo

fail() {
  EXIT_CODE=$?

  echo
  echo "=============================================="
  echo " ❌ DEPLOY FAILED"
  echo "=============================================="

  echo
  echo "=== CONTAINER STATUS ==="
  docker compose ps 2>/dev/null || true

  echo
  echo "=== RECENT LOGS ==="
  docker compose logs --tail=150 2>/dev/null || true

  exit "$EXIT_CODE"
}

trap fail ERR

echo "=== PREFLIGHT ==="

command -v git >/dev/null || {
  echo "ERROR: git is not installed."
  exit 1
}

command -v docker >/dev/null || {
  echo "ERROR: docker is not installed."
  exit 1
}

command -v flock >/dev/null || {
  echo "ERROR: flock is not installed (normally provided by util-linux)."
  exit 1
}

command -v curl >/dev/null || {
  echo "ERROR: curl is not installed."
  exit 1
}

docker compose version

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERROR: $APP_DIR is not a Git repository."
  exit 1
fi

BRANCH="$(git branch --show-current)"

if [[ -z "$BRANCH" ]]; then
  echo "ERROR: Repository is in detached HEAD state."
  exit 1
fi

echo "Branch:         $BRANCH"
echo "Current commit: $(git rev-parse --short HEAD)"

echo
echo "=== WORKTREE CHECK ==="

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: Production repository contains local Git changes."
  echo
  git status --short
  echo
  echo "Production source should match GitHub."
  echo "Keep secrets in .env, not tracked source files."
  exit 1
fi

echo "Worktree clean."

echo
echo "=== DISK SPACE CHECK ==="

AVAILABLE_KB="$(df -Pk "$APP_DIR" | awk 'NR==2 {print $4}')"
MIN_FREE_KB=$((5 * 1024 * 1024))

if (( AVAILABLE_KB < MIN_FREE_KB )); then
  echo "ERROR: Less than 5 GiB of free disk space is available for deployment."
  df -h "$APP_DIR"
  exit 1
fi

df -h "$APP_DIR"

echo
echo "=== UPDATE SOURCE ==="

OLD_HEAD="$(git rev-parse HEAD)"

git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"

NEW_HEAD="$(git rev-parse HEAD)"

echo
echo "Previous commit: ${OLD_HEAD:0:12}"
echo "Current commit:  ${NEW_HEAD:0:12}"

if [[ "$OLD_HEAD" == "$NEW_HEAD" ]]; then
  echo "Already on latest commit."
else
  echo
  echo "=== COMMITS BEING DEPLOYED ==="
  git --no-pager log \
    --oneline \
    --no-decorate \
    "$OLD_HEAD..$NEW_HEAD"
fi

echo
echo "=== ENVIRONMENT CHECK ==="

ENV_FILE_PATH="${ENV_FILE:-.env}"

if [[ ! -f "$ENV_FILE_PATH" ]]; then
  if [[ -f .env ]]; then
    ENV_FILE_PATH=".env"
  elif [[ -f masyg_extractor/.env ]]; then
    ENV_FILE_PATH="masyg_extractor/.env"
  else
    echo "ERROR: Production environment file was not found."
    echo "Expected .env or masyg_extractor/.env"
    exit 1
  fi
fi

echo "Environment file found: $ENV_FILE_PATH"

echo
echo "=== VALIDATE DOCKER COMPOSE ==="

docker compose config -q

echo "Services:"
docker compose config --services

echo
echo "=== BUILD ==="

docker compose build --pull

echo
echo "=== START / UPDATE ==="

if docker compose up --help 2>&1 | grep -q -- '--wait'; then
  docker compose up \
    -d \
    --remove-orphans \
    --wait \
    --wait-timeout 120
else
  docker compose up \
    -d \
    --remove-orphans

  echo
  echo "Compose --wait is unavailable."
  echo "Waiting 15 seconds for application startup..."
  sleep 15
fi

echo
echo "=== CONTAINER STATUS ==="

docker compose ps

echo
echo "=== HEALTH ==="

CONTAINER_ID="$(docker compose ps -q masyg-extractor-app)"

if [[ -z "$CONTAINER_ID" ]]; then
  echo "ERROR: masyg-extractor-app container was not created."
  exit 1
fi

HEALTH="$(
  docker inspect \
    --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
    "$CONTAINER_ID"
)"

echo "masyg-extractor-app: $HEALTH"

if [[ "$HEALTH" != "healthy" ]]; then
  echo "ERROR: Application is not healthy."
  docker compose logs --tail=150 masyg-extractor-app
  exit 1
fi

echo
echo "=== LOCAL HTTP SMOKE ==="

curl \
  --fail \
  --silent \
  --show-error \
  --max-time 10 \
  http://127.0.0.1:5000/health >/dev/null

echo "HTTP /health: PASS"

echo
echo "=== DOCKER DISK USAGE ==="

docker system df

echo
echo "=== RECENT APPLICATION LOGS ==="

docker compose logs \
  --tail=100 \
  masyg-extractor-app

echo
echo "=============================================="
echo " ✅ MASYG EXTRACTOR DEPLOY COMPLETE"
echo "=============================================="
echo "Commit: $(git rev-parse --short HEAD)"
echo "Health: $HEALTH"
echo "Time:   $(date)"
echo

trap - ERR
