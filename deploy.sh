#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

echo
echo "========================================"
echo " MASYG EXTRACTOR PRODUCTION DEPLOY"
echo "========================================"
echo "Directory: $APP_DIR"
echo "Started:   $(date)"
echo

fail() {
  EXIT_CODE=$?

  echo
  echo "========================================"
  echo " ❌ DEPLOY FAILED"
  echo "========================================"
  echo

  echo "=== CONTAINER STATUS ==="
  docker compose ps 2>/dev/null || true

  echo
  echo "=== RECENT LOGS ==="
  docker compose logs --tail=120 2>/dev/null || true

  exit "$EXIT_CODE"
}

trap fail ERR

echo "=== PREFLIGHT ==="

command -v git >/dev/null
command -v docker >/dev/null
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

echo "Branch: $BRANCH"
echo "Current commit: $(git rev-parse --short HEAD)"

echo
echo "=== CHECK WORKTREE ==="

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: Production repository contains local changes."
  echo
  git status --short
  echo
  echo "Resolve the production changes before deploying."
  exit 1
fi

echo "Worktree clean."

echo
echo "=== UPDATE SOURCE ==="

OLD_HEAD="$(git rev-parse HEAD)"

git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"

NEW_HEAD="$(git rev-parse HEAD)"

echo "Previous: ${OLD_HEAD:0:12}"
echo "Current:  ${NEW_HEAD:0:12}"

if [[ "$OLD_HEAD" == "$NEW_HEAD" ]]; then
  echo "Already on latest commit."
else
  echo
  echo "Changes being deployed:"
  git --no-pager log --oneline --no-decorate "$OLD_HEAD..$NEW_HEAD"
fi

echo
echo "=== VALIDATE COMPOSE ==="

docker compose config -q

echo "Services:"
docker compose config --services

echo
echo "=== BUILD ==="

docker compose build --pull

echo
echo "=== DEPLOY ==="

if docker compose up --help 2>&1 | grep -q -- '--wait'; then
  docker compose up \
    -d \
    --remove-orphans \
    --wait \
    --wait-timeout 120
else
  docker compose up -d --remove-orphans

  echo
  echo "Compose --wait unavailable. Waiting for startup..."
  sleep 10
fi

echo
echo "=== STATUS ==="

docker compose ps

echo
echo "=== RECENT LOGS ==="

docker compose logs --tail=80

echo
echo "========================================"
echo " ✅ MASYG DEPLOY COMPLETE"
echo " Commit: $(git rev-parse --short HEAD)"
echo " Time:   $(date)"
echo "========================================"
echo

trap - ERR
