#!/usr/bin/env bash
# Everyday commands for the job watcher on GitHub. Run from anywhere:
#
#   ./deploy.sh push "Commit title" ["Commit body"]   commit everything and push
#   ./deploy.sh budget 14.43                          set BUDGET_USD (console balance at start + top-ups)
#   ./deploy.sh minfit 60                             set the minimum fit score for emails
#   ./deploy.sh run [--dry] [--max 400]               start a run now (--max: catch-up for a backlog)
#   ./deploy.sh ship "Title" ["Body"] [--budget X] [--max N] [--dry]
#                                                     push, then optional budget, then a run
#   ./deploy.sh status                                secrets, variables, last runs
#   ./deploy.sh log                                   summary of the latest run
#   ./deploy.sh pause | resume                        stop / restart the hourly schedule
set -euo pipefail
cd "$(dirname "$0")"
WF=job-alerts.yml
PRIVATE='(^|/)(\.env|resume\.(pdf|docx|md|txt)|\.seen_jobs\.json|\.spend\.json)$'

die() { echo "Error: $*" >&2; exit 1; }

cmd_push() {
  local title="${1:-}" body="${2:-}"
  [ -n "$title" ] || die 'give a commit title: ./deploy.sh push "Title" ["Body"]'
  git add -A
  if git diff --cached --name-only | grep -Eq "$PRIVATE"; then
    git diff --cached --name-only | grep -E "$PRIVATE" >&2
    git reset -q
    die "the files above are private and must not be committed (check .gitignore). Nothing was committed."
  fi
  if git diff --cached --quiet; then
    echo "Nothing new to commit."
  else
    git diff --cached --stat
    if [ -n "$body" ]; then git commit -q -m "$title" -m "$body"; else git commit -q -m "$title"; fi
    echo "Committed: $title"
  fi
  if [ -n "$(git log '@{u}..' --oneline 2>/dev/null)" ]; then git push -q && echo "Pushed."; else echo "Already pushed."; fi
}

cmd_budget() {
  [[ "${1:-}" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die 'give an amount: ./deploy.sh budget 14.43'
  gh variable set BUDGET_USD --body "$1" && echo "BUDGET_USD = $1"
}

cmd_minfit() {
  [[ "${1:-}" =~ ^[0-9]+$ ]] || die 'give a score 0-100: ./deploy.sh minfit 60'
  gh variable set MIN_FIT --body "$1" && echo "MIN_FIT = $1"
}

cmd_run() {
  local dry=false max=30
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry) dry=true ;;
      --max) max="${2:?--max needs a number}"; shift ;;
      *) die "unknown option for run: $1" ;;
    esac
    shift
  done
  gh workflow run "$WF" -f dry_run="$dry" -f max_score="$max"
  echo "Started (dry_run=$dry, max_score=$max). Waiting for it to finish..."
  sleep 6
  local id; id=$(gh run list --workflow "$WF" -L 1 --json databaseId -q '.[0].databaseId')
  gh run watch "$id" --exit-status >/dev/null && echo "Finished OK." || echo "Run failed; see: ./deploy.sh log"
  cmd_log "$id"
}

cmd_log() {
  local id="${1:-$(gh run list --workflow "$WF" -L 1 --json databaseId -q '.[0].databaseId')}"
  gh run view "$id" --json displayTitle,event,status,conclusion,createdAt,url \
    -q '"\(.createdAt)  \(.event)  \(.status)/\(.conclusion // "-")\n\(.url)"'
  gh run view "$id" --log 2>/dev/null \
    | grep -E 'stats:|scored jobs|Claude spend|Emailed|No new matching|boards failed|Error|Traceback' \
    | sed -E 's/^[^\t]*\t[^\t]*\t[0-9T:.-]+Z //' || true
}

cmd_status() {
  echo "== Secrets";   gh secret list
  echo "== Variables"; gh variable list
  echo "== Schedule";  gh workflow list | grep -i 'job alerts' || true
  echo "== Last runs"; gh run list --workflow "$WF" -L 5
}

cmd_ship() {
  local title="${1:-}" body=""
  shift || true
  if [ $# -gt 0 ] && [[ "$1" != --* ]]; then body="$1"; shift; fi
  local budget="" run_args=() do_run=false
  while [ $# -gt 0 ]; do
    case "$1" in
      --budget) budget="${2:?--budget needs an amount}"; shift ;;
      --max) run_args+=(--max "${2:?--max needs a number}"); do_run=true; shift ;;
      --dry) run_args+=(--dry); do_run=true ;;
      --run) do_run=true ;;
      *) die "unknown option for ship: $1" ;;
    esac
    shift
  done
  cmd_push "$title" "$body"
  [ -z "$budget" ] || cmd_budget "$budget"
  if $do_run; then cmd_run "${run_args[@]}"; fi
}

case "${1:-}" in
  push)   shift; cmd_push "$@" ;;
  budget) shift; cmd_budget "$@" ;;
  minfit) shift; cmd_minfit "$@" ;;
  run)    shift; cmd_run "$@" ;;
  ship)   shift; cmd_ship "$@" ;;
  status) cmd_status ;;
  log)    shift; cmd_log "$@" ;;
  pause)  gh workflow disable "$WF" && echo "Hourly runs paused." ;;
  resume) gh workflow enable "$WF" && echo "Hourly runs resumed." ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
