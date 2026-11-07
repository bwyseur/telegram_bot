#!/bin/bash

set -Eeuo pipefail

SYSTEMCTL="/usr/bin/systemctl"
SYSTEMD_RUN="/usr/bin/systemd-run"

# ============================================================
# Repository configuration
# ============================================================

REPOS=(
    boursicotage
    telegram_bot
)

declare -A REPO_PATH=(
    [boursicotage]="$HOME/apps/boursicotage"
    [telegram_bot]="$HOME/apps/telegram_bot"
)

# Service associated with each repository
declare -A REPO_SERVICE=(
    [boursicotage]="boursicotage.service"
    [telegram_bot]="telegram-bot.service"
)

# Services and their restart policies
# immediate = restart synchronously
# deferred  = restart independently after a delay
SERVICES=(
    boursicotage.service
    telegram-bot.service
)

declare -A SERVICE_MODE=(
    [boursicotage.service]="immediate"
    [telegram-bot.service]="deferred"
)

# Services that need restarting
declare -A RESTART_SERVICES=()

FAILED=0


# ============================================================
# Repository update function
# ============================================================

update_repo() {
    local name="$1"
    local path="${REPO_PATH[$name]}"
    local service="${REPO_SERVICE[$name]}"

    local upstream
    local counts
    local ahead
    local behind
    local old_commit
    local new_commit
    local diff_status

    echo
    echo "============================================================"
    echo "Checking repository: $name"
    echo "Path: $path"
    echo "============================================================"

    # Check repository directory
    if [[ ! -d "$path" ]]; then
        echo "ERROR: Repository directory does not exist: $path"
        return 1
    fi

    # Check Git repository
    if ! git -C "$path" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "ERROR: Not a valid Git repository."
        return 1
    fi

    # Check for local modifications
    if [[ -n "$(git -C "$path" status --porcelain)" ]]; then
        echo "ERROR: Working tree is not clean. Update aborted."
        git -C "$path" status --short
        return 1
    fi

    # Fetch
    echo "Fetching remote changes..."

    if ! git -C "$path" fetch --prune; then
        echo "ERROR: Git fetch failed."
        return 1
    fi

    # Check upstream
    if ! upstream=$(git -C "$path" rev-parse \
        --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null); then

        echo "ERROR: No upstream configured."
        return 1
    fi

    echo "Upstream: $upstream"

    # Compare local and remote commits
    if ! counts=$(git -C "$path" rev-list \
        --left-right --count "HEAD...$upstream"); then

        echo "ERROR: Could not compare commits."
        return 1
    fi

    read -r ahead behind <<< "$counts"

    echo "Local commits ahead: $ahead"
    echo "Remote commits ahead: $behind"

    if [[ "$behind" -eq 0 ]]; then
        if [[ "$ahead" -eq 0 ]]; then
            echo "NO_UPDATE: Repository is already up to date."
        else
            echo "NO_UPDATE: Local branch is ahead of its remote."
        fi

        return 0
    fi

    if [[ "$ahead" -ne 0 ]]; then
        echo "ERROR: Local and remote branches have diverged."
        echo "Manual intervention required."
        return 1
    fi

    # Save old commit
    old_commit=$(git -C "$path" rev-parse HEAD)

    # Update repository
    echo "Updates found. Pulling changes..."

    if ! git -C "$path" merge --ff-only "$upstream"; then
        echo "ERROR: Fast-forward update failed."
        return 1
    fi

    new_commit=$(git -C "$path" rev-parse HEAD)

    echo "PULL_SUCCESS: $old_commit -> $new_commit"

    # Mark associated service for restart
    RESTART_SERVICES["$service"]=1

    # ========================================================
    # Exceptional dependency:
    #
    # Changes to boursicotage/bot_commands/ also require
    # restarting telegram-bot.service.
    # ========================================================

    if [[ "$name" == "boursicotage" ]]; then

        if git -C "$path" diff --quiet \
            "$old_commit" "$new_commit" -- bot_commands/; then

            echo "No changes in bot_commands/."

        else
            diff_status=$?

            if [[ "$diff_status" -eq 1 ]]; then
                echo "Changes detected in bot_commands/."
                echo "Telegram bot also needs restarting."

                RESTART_SERVICES["telegram-bot.service"]=1
            else
                echo "ERROR: Could not inspect bot_commands changes."
                return 1
            fi
        fi
    fi

    return 0
}


# ============================================================
# Process all repositories
# ============================================================

for repo in "${REPOS[@]}"; do

    if ! update_repo "$repo"; then
        FAILED=1
        echo "WARNING: Failed to update $repo."
        echo "Continuing with remaining repositories..."
    fi

done


# ============================================================
# Restart required services
# ============================================================

echo
echo "============================================================"
echo "Processing service restarts"
echo "============================================================"

for service in "${SERVICES[@]}"; do

    if [[ -z "${RESTART_SERVICES[$service]:-}" ]]; then
        echo "No restart needed: $service"
        continue
    fi

    mode="${SERVICE_MODE[$service]}"

    echo "Restart required: $service ($mode)"

    if [[ "$mode" == "immediate" ]]; then

        echo "Restarting $service..."

        if ! sudo -n "$SYSTEMCTL" restart "$service"; then
            echo "ERROR: Could not restart $service."
            FAILED=1
            continue
        fi

        if ! sudo -n "$SYSTEMCTL" is-active --quiet "$service"; then
            echo "ERROR: $service is not active."
            FAILED=1
            continue
        fi

        echo "SUCCESS: $service is running."

    elif [[ "$mode" == "deferred" ]]; then

        echo "Scheduling deferred restart of $service..."

        # A separate systemd timer ensures that the restart
        # happens outside the Telegram bot's service cgroup.
        #
        # The unique unit name allows multiple invocations.

        unit="telegram-bot-restart-$(date +%s)-$$"

        if ! sudo -n "$SYSTEMD_RUN" \
            --system \
            --quiet \
            --no-block \
            --on-active=5s \
            --unit="$unit" \
            "$SYSTEMCTL" restart "$service"; then

            echo "ERROR: Could not schedule restart of $service."
            FAILED=1
            continue
        fi

        echo "SUCCESS: Restart scheduled in 5 seconds."

    else
        echo "ERROR: Unknown restart mode: $mode"
        FAILED=1
    fi

done


# ============================================================
# Final result
# ============================================================

echo
echo "============================================================"

if [[ "$FAILED" -ne 0 ]]; then
    echo "WARNING: Update process completed with errors."
    exit 1
fi

echo "SUCCESS: All repositories checked and required services handled."

exit 0

