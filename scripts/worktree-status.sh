#!/usr/bin/env bash
# Show status of all git worktrees: age, commits, dirty files, merge status,
# remote push status, and associated GitHub PR.
#
# Usage: ./scripts/worktree-status.sh
#
# Requires: git, gh (for PR lookup and squash-merge detection)
# Without gh: merge detection is limited to direct merges (ancestor check only).

set -euo pipefail

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
MAIN_BRANCH="main"

has_gh=false
if command -v gh &>/dev/null && gh auth status &>/dev/null 2>&1; then
    has_gh=true
fi

# Get remote owner/repo from origin URL
remote_url="$(git remote get-url origin 2>/dev/null || echo "")"
gh_repo=""
if [[ "$remote_url" =~ github\.com[:/]([^/]+)/([^/.]+)(\.git)?$ ]]; then
    gh_repo="${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
fi

printf "%-45s %-12s %-8s %-7s %-12s %-18s %s\n" \
    "WORKTREE" "LAST COMMIT" "AHEAD" "DIRTY" "MERGED" "REMOTE" "PR"
printf "%s\n" "$(printf '%.0s-' {1..145})"

git worktree list --porcelain | grep '^worktree ' | sed 's/^worktree //' | while read -r wt_path; do
    # Skip the main worktree
    if [[ "$wt_path" == "$PROJECT_ROOT" ]]; then
        continue
    fi

    # Skip worktrees outside this project
    if [[ "$wt_path" != "$PROJECT_ROOT"/* ]]; then
        continue
    fi

    wt_name="$(basename "$wt_path")"
    branch="$(git -C "$wt_path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")"

    # Age
    last_commit="$(git -C "$wt_path" log -1 --format='%cs' 2>/dev/null || echo "unknown")"

    # Ahead of main
    ahead="$(git rev-list --count "$MAIN_BRANCH..$branch" 2>/dev/null || echo "?")"

    # Dirty files
    dirty="$(git -C "$wt_path" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"

    # Remote status
    remote_sha="$(git ls-remote --heads origin "$branch" 2>/dev/null | awk '{print $1}')"
    local_sha="$(git rev-parse "$branch" 2>/dev/null || echo "")"
    if [[ -z "$remote_sha" ]]; then
        remote_status="LOCAL ONLY"
    elif [[ "$remote_sha" == "$local_sha" ]]; then
        remote_status="pushed"
    else
        remote_status="diverged"
    fi

    # Merge status + PR lookup (combined — PR is the authority for squash merges)
    merged="unknown"
    pr_info="-"

    if git merge-base --is-ancestor "$branch" "$MAIN_BRANCH" 2>/dev/null; then
        merged="YES"
    elif [[ "$ahead" == "0" ]]; then
        merged="YES"
    else
        merged="no"
    fi

    if $has_gh && [[ -n "$gh_repo" ]]; then
        pr_json="$(gh pr list --repo "$gh_repo" --head "$branch" --state all --limit 1 \
            --json number,state,mergedAt 2>/dev/null || echo "")"
        if [[ -n "$pr_json" && "$pr_json" != "[]" ]]; then
            pr_num="$(echo "$pr_json" | python3 -c "import json,sys; d=json.load(sys.stdin)[0]; print(d['number'])" 2>/dev/null || echo "")"
            pr_state="$(echo "$pr_json" | python3 -c "import json,sys; d=json.load(sys.stdin)[0]; print(d['state'].lower())" 2>/dev/null || echo "")"
            pr_merged_at="$(echo "$pr_json" | python3 -c "import json,sys; d=json.load(sys.stdin)[0]; print(d.get('mergedAt') or '')" 2>/dev/null || echo "")"

            if [[ -n "$pr_num" ]]; then
                if [[ -n "$pr_merged_at" ]]; then
                    pr_info="#${pr_num} merged"
                    # PR was merged (squash or regular) — trust GitHub over local git
                    if [[ "$merged" == "no" ]]; then
                        merged="squashed"
                    fi
                else
                    pr_info="#${pr_num} ${pr_state}"
                fi
            fi
        fi
    fi

    printf "%-45s %-12s +%-7s %-7s %-12s %-18s %s\n" \
        "$wt_name" "$last_commit" "$ahead" "$dirty" "$merged" "$remote_status" "$pr_info"
done
