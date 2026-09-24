#!/usr/bin/env bash
# Pull-based deploy for a single workstation, run by openjarvis-deploy.timer.
# Pulls $OPENJARVIS_IMAGE, and when its digest changed: backs up SQLite state,
# starts the new digest, waits for /health, and rolls back to the last good
# digest if it never gets healthy.
set -euo pipefail

ROOT=${OPENJARVIS_ROOT:-$HOME/openjarvis}
REPO=$ROOT/repo
ENV_FILE=$ROOT/prod/.env
STATE=$ROOT/prod/last-good-image
BACKUPS=$ROOT/backups
PROJECT=openjarvis-prod
KEEP_BACKUPS=14

exec 9>"$ROOT/prod/.deploy.lock"
flock -n 9 || { echo "another deploy is running"; exit 0; }

git -C "$REPO" pull --ff-only --quiet

set -a
# shellcheck source=/dev/null
. "$ENV_FILE"
set +a

compose=(docker compose -p "$PROJECT" -f "$REPO/deploy/docker/docker-compose.yml" --env-file "$ENV_FILE")
health_url="http://${OPENJARVIS_BIND:-127.0.0.1}:8000/health"

notify() {
  echo "$1"
  if [ -n "${NOTIFY_URL:-}" ]; then
    curl -fsS -m 10 -d "openjarvis@$(hostname): $1" "$NOTIFY_URL" >/dev/null || true
  fi
}

healthy() {
  for _ in $(seq 1 90); do
    curl -fsS -m 5 "$health_url" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

backup_sqlite() {
  local image=$1 out
  out="$BACKUPS/$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$out"
  # WAL-mode stores can't be copied as files; use SQLite's online backup API.
  docker run --rm --user 0 --entrypoint python3 \
    -e OWNER="$(id -u):$(id -g)" \
    -v "${PROJECT}_jarvis-data:/data" -v "$out:/out:z" "$image" -c '
import os, pathlib, sqlite3
uid, gid = map(int, os.environ["OWNER"].split(":"))
for db in pathlib.Path("/data").rglob("*.db"):
    dst = pathlib.Path("/out") / db.relative_to("/data")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src, tgt = sqlite3.connect(db), sqlite3.connect(dst)
    src.backup(tgt)
    src.close(); tgt.close()
for p in pathlib.Path("/out").rglob("*"):
    os.chown(p, uid, gid)
'
  find "$BACKUPS" -mindepth 1 -maxdepth 1 -type d | sort -r | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -rf
  echo "backup: $out"
}

docker pull --quiet "$OPENJARVIS_IMAGE" >/dev/null
new=$(docker image inspect --format '{{index .RepoDigests 0}}' "$OPENJARVIS_IMAGE")
current=$(cat "$STATE" 2>/dev/null || true)
running=$("${compose[@]}" ps --status running -q jarvis)

if [ "$new" = "$current" ] && [ -n "$running" ]; then
  exit 0
fi

if [ -n "$current" ]; then
  backup_sqlite "$current"
fi

echo "deploying $new"
OPENJARVIS_IMAGE=$new "${compose[@]}" up -d --no-build --remove-orphans

if healthy; then
  echo "$new" >"$STATE"
  [ "$new" = "$current" ] || notify "deployed $new"
  exit 0
fi

"${compose[@]}" logs --tail 50 jarvis || true
if [ -n "$current" ] && [ "$current" != "$new" ]; then
  OPENJARVIS_IMAGE=$current "${compose[@]}" up -d --no-build --remove-orphans
  notify "deploy of $new failed health check; rolled back to $current"
else
  notify "deploy of $new failed health check; no previous version to roll back to"
fi
exit 1
