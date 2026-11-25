#!/usr/bin/env bash
# Run the job watcher on this computer.
#   ./run_local.sh --dry-run    print matches, no email
#   ./run_local.sh              real run: emails new matches
#   ./run_local.sh --check      which job boards load
#   ./run_local.sh --spend      what it has spent on Claude
# Settings come from .env in this folder (copy .env.example to .env and fill it in).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "Missing .env: run  cp .env.example .env  and fill it in." >&2
  exit 1
fi
# Read KEY=VALUE lines literally (no shell evaluation), so values with spaces, like a Gmail
# app password "abcd efgh ijkl mnop", work without quotes. Surrounding quotes are stripped.
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"
  case "$line" in ''|'#'*) continue ;; esac
  key="${line%%=*}"; val="${line#*=}"
  key="$(echo "$key" | tr -d '[:space:]')"
  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
  if [[ "$val" =~ ^\"(.*)\"$ || "$val" =~ ^\'(.*)\'$ ]]; then val="${BASH_REMATCH[1]}"; fi
  [[ "$val" == "~/"* ]] && val="$HOME/${val#\~/}"
  [ -n "${!key:-}" ] && continue   # a value set on the command line wins over .env
  export "$key=$val"
done < .env

if [ ! -x .venv/bin/python ]; then
  echo "First run: creating .venv and installing packages..."
  python3 -m venv .venv
  .venv/bin/pip install -q "anthropic>=0.60" "pypdf>=4.0" "python-docx>=1.1"
fi

exec .venv/bin/python job_watcher.py "$@"
