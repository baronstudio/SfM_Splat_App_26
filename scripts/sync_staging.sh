#!/usr/bin/env bash
# Push changed files from this dev machine to the staging server.
#
# Staging is \Ws_tech4art_jbb\travail\DEV\SfM_Splat_App_26 — the PC that runs
# the reconstructions and serves the app on the LAN (start.bat, CLAUDE.md §12,
# 2026-08-30). It is never developed on, so a handful of files over there are
# staging-local — the tool paths and the API port — and must survive every push.
#
#   ./scripts/sync_staging.sh            # dry run: report what would change
#   ./scripts/sync_staging.sh --apply    # do it
#
# Only git-tracked files are considered, so projects/, .venv/, node_modules/
# and tools/ are never touched. Nothing is ever deleted on the staging side.
#
# The staging copy is itself a clone, but this script never runs git over there
# and never touches its .git: the share reports a different owner, so every git
# command needs a safe.directory exception, and a pull would have to reconcile
# the staging-local files below on a machine nobody is sitting at. What the
# sync leaves instead is .version_stamp.json, which
# backend/api/routes/version.py prefers over that clone's stale HEAD.
set -u

REMOTE="${STAGING_PATH:-//Ws_tech4art_jbb/travail/DEV/SfM_Splat_App_26}"
STAGING_IP="${STAGING_HOST:-192.168.1.201}"
STAGING_UI_PORT="${STAGING_UI_PORT:-5173}"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

# Never copied, in either direction. Machine-specific state that happens to be
# tracked. (pipeline.db, projects/ and tools/ are gitignored and therefore
# never considered in the first place.)
PERMANENT="
sfm-splat-app/config.json
"

# Staging owns these: the API port moved to 8001 there on 2026-08-31 because
# Manager.exe holds 0.0.0.0:8000 on that workstation and a bind answers
# WinError 10013. Only UI_PORT has to be reachable, so the whole cost is these
# four files. If one differs here, the script stops and asks instead of
# overwriting — a push that silently put the port back would take the server
# down and say nothing.
PROTECTED="
sfm-splat-app/backend/main.py
sfm-splat-app/frontend/vite.config.ts
sfm-splat-app/start.bat
sfm-splat-app/start.sh
"

in_list() { echo "$2" | grep -qxF "$1"; }

# Line endings are not a change. This worktree holds LF and the staging clone
# was checked out with autocrlf, so a byte comparison calls a third of the tree
# modified for ever and the push is noise.
differs() { ! diff -q --strip-trailing-cr "$1" "$2" >/dev/null 2>&1; }

[ -d "$REMOTE" ] || { echo "!! staging share unreachable: $REMOTE"; exit 1; }

copy=(); alert=(); newf=()
while IFS= read -r f; do
    in_list "$f" "$PERMANENT" && continue
    [ -f "$f" ] || continue
    if [ ! -f "$REMOTE/$f" ]; then
        newf+=("$f"); continue
    fi
    differs "$f" "$REMOTE/$f" || continue
    if in_list "$f" "$PROTECTED"; then alert+=("$f"); else copy+=("$f"); fi
done < <(git ls-files)

# Everything the sync governs, changed or not: staging's copy of these files is
# this worktree's copy, which is what the version stamp below describes.
governed=(); while IFS= read -r f; do
    in_list "$f" "$PERMANENT" && continue
    in_list "$f" "$PROTECTED" && continue
    [ -f "$f" ] && governed+=("$f")
done < <(git ls-files)

# The staging clone's .git is never touched, so `git log` over there answers for
# whenever somebody last ran git on that PC — behind the files beside it, and
# the app reads its version number from exactly that. The stamp is what the sync
# knows and git there cannot: which commit these files came from.
write_stamp() {
    local sha short date count version iso branch remote gh url dirty stamp
    sha=$(git rev-parse HEAD 2>/dev/null) || return 0
    short=$(git rev-parse --short=8 HEAD)
    date=$(git log -1 --date=format:%Y.%m.%d --format=%cd)
    count=$(git rev-list --count HEAD 2>/dev/null)
    # YYYY.MM.DD.N, the count included, or two builds of one day read alike —
    # which is the whole of the 2026-08-30 decision this file has to match.
    version="$date"
    [ -n "$count" ] && version="$date.$count"
    iso=$(git log -1 --format=%cI)
    branch=$(git rev-parse --abbrev-ref HEAD)
    remote=$(git config --get remote.origin.url)
    url=""
    case "$remote" in
        *github.com*)
            gh=$(echo "$remote" | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git/?$##')
            url="https://github.com/$gh/commit/$sha" ;;
    esac
    # What is pushed is the working tree, not the commit — so if any governed
    # file differs from HEAD here, the stamp names a commit whose content is
    # not quite what is running. Saying so is the whole point.
    dirty=false
    if [ "${#governed[@]}" -gt 0 ] && ! git diff --quiet HEAD -- "${governed[@]}"; then
        dirty=true
    fi
    stamp="$REMOTE/sfm-splat-app/.version_stamp.json"
    cat > "$stamp" <<JSON
{
  "commit": "$sha",
  "commit_short": "$short",
  "commit_count": ${count:-null},
  "version": "$version",
  "commit_date": "$iso",
  "branch": "$branch",
  "commit_url": "$url",
  "dirty": $dirty,
  "synced_at": "$(date -Iseconds)",
  "synced_from": "${HOSTNAME:-${COMPUTERNAME:-unknown}}"
}
JSON
    echo "-- version stamp: $version ($short, dirty=$dirty)"
}

echo "=== staging: $REMOTE"
printf '=== %d new, %d changed, %d protected-differ\n\n' \
    "${#newf[@]}" "${#copy[@]}" "${#alert[@]}"

if [ "${#alert[@]}" -gt 0 ]; then
    echo "!! PROTECTED FILES DIFFER — not copied, JB decides:"
    printf '     %s\n' "${alert[@]}"
    echo "   (diff one with:  diff \"\$REMOTE/<file>\" <file> )"
    echo
fi

todo=("${newf[@]:-}" "${copy[@]:-}")
todo=($(printf '%s\n' "${todo[@]}" | grep -v '^$'))
if [ "${#todo[@]}" -eq 0 ]; then
    echo "Nothing to push."
    [ "$APPLY" -eq 1 ] && write_stamp
    exit 0
fi
echo "Would copy:"
printf '     %s\n' "${todo[@]}"
echo

# uvicorn runs with --reload over there: writing any .py restarts the backend,
# which kills a running step AND orphans the spirula child on the GPU
# (core/proc.py holds the kill registry in memory).
if printf '%s\n' "${todo[@]}" | grep -q '\.py$'; then
    status=$(curl -s -m 8 "http://$STAGING_IP:$STAGING_UI_PORT/api/pipeline/status" 2>/dev/null)
    if echo "$status" | grep -q '"running":true'; then
        echo "!! A JOB IS RUNNING on staging: $status"
        echo "!! The push includes .py files and --reload would kill it. Aborting."
        exit 2
    fi
    if [ -z "$status" ]; then
        echo "-- staging server not answering on :$STAGING_UI_PORT (stopped, or the"
        echo "   firewall holds the port). Nothing can be running, so .py is safe."
    else
        echo "-- no job running, .py files safe to write"
    fi
fi

[ "$APPLY" -eq 1 ] || { echo; echo "(dry run — re-run with --apply)"; exit 0; }

for f in "${todo[@]}"; do
    mkdir -p "$REMOTE/$(dirname "$f")"
    cp -p "$f" "$REMOTE/$f" && echo "  -> $f" || echo "  !! FAILED $f"
done
write_stamp
echo
echo "Done. Restart the staging server to pick up backend changes."
