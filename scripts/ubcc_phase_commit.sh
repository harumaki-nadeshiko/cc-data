#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <phase> <commit message>" >&2
  exit 1
fi

PHASE=$1
shift
MESSAGE=$*

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
SSH_KEY=${UBCC_SSH_KEY:-/mnt/data2/$USER/.ssh/id_rsa_hm}
GIT_NAME=${UBCC_GIT_NAME:-$(git -C "$REPO_ROOT" config user.name || true)}
GIT_EMAIL=${UBCC_GIT_EMAIL:-$(git -C "$REPO_ROOT" config user.email || true)}
BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)

"$REPO_ROOT/scripts/ubcc_git_preflight.sh" >/dev/null

git -C "$REPO_ROOT" add -A

if git -C "$REPO_ROOT" diff --cached --quiet; then
  echo "no changes to commit for $PHASE"
  exit 0
fi

git -C "$REPO_ROOT" -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" commit -m "$MESSAGE"

GIT_SSH_COMMAND="ssh -i $SSH_KEY -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new" \
  git -C "$REPO_ROOT" push origin "HEAD:refs/heads/$BRANCH"

echo "pushed $PHASE changes to origin/$BRANCH"
