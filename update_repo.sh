#!/bin/bash

set -Eeuo pipefail

REPO="$HOME/apps/boursicotage"
SERVICE="boursicotage.service"
SYSTEMCTL="/usr/bin/systemctl"

cd "$REPO" || {
    echo "ERROR: Cannot access repository: $REPO"
    exit 1
}

echo "Checking repository: $REPO"

# Check repository integrity and local modifications
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "ERROR: Not a valid Git repository."
    exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
    echo "ERROR: Working tree is not clean. Update aborted."
    git status --short
    exit 1
fi

# Fetch latest remote information
echo "Fetching remote changes..."

if ! git fetch --prune; then
    echo "ERROR: Git fetch failed."
    exit 1
fi

# Check that the current branch has an upstream
if ! UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null); then
    echo "ERROR: Current branch has no upstream configured."
    exit 1
fi

echo "Upstream: $UPSTREAM"

# Compare local and remote commits
read -r AHEAD BEHIND < <(
    git rev-list --left-right --count "HEAD...@{u}"
)

echo "Local commits ahead: $AHEAD"
echo "Remote commits ahead: $BEHIND"

if [[ "$BEHIND" -eq 0 ]]; then
    if [[ "$AHEAD" -eq 0 ]]; then
        echo "NO_UPDATE: Repository is already up to date."
    else
        echo "NO_UPDATE: Local branch is ahead of its remote."
    fi
    exit 0
fi

if [[ "$AHEAD" -ne 0 ]]; then
    echo "ERROR: Local and remote branches have diverged. Manual intervention required."
    exit 1
fi

OLD_COMMIT=$(git rev-parse --short HEAD)

echo "Updates found. Pulling changes..."

if ! git pull --ff-only; then
    echo "ERROR: Git pull failed. Service was not restarted."
    exit 1
fi

NEW_COMMIT=$(git rev-parse --short HEAD)

echo "PULL_SUCCESS: $OLD_COMMIT -> $NEW_COMMIT"

# Restart the application
echo "Restarting $SERVICE..."

if ! sudo -n "$SYSTEMCTL" restart "$SERVICE"; then
    echo "ERROR: Git update succeeded, but service restart failed."
    exit 2
fi

# Verify service health
if ! sudo -n "$SYSTEMCTL" is-active --quiet "$SERVICE"; then
    echo "ERROR: Git update succeeded, but service is not active."
    exit 2
fi

echo "SUCCESS: Update completed and service is running."
echo "Previous commit: $OLD_COMMIT"
echo "Current commit: $NEW_COMMIT"
