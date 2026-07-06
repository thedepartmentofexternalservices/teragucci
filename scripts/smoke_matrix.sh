#!/usr/bin/env bash
# smoke_matrix.sh — cross-machine readiness sweep for the Teraguchi test lab.
#
# Runs `teraguchi doctor` on every configured machine over SSH and prints a
# combined readiness matrix (who can be a server, who can be a client). This is
# Piece 3 from .claude/ARCHITECTURE_DIRECTION.md — the foundation for the
# two-machine host<-client smoke once the boxes are reachable.
#
# Setup (three machines, SSH):
#   - Linux:   ssh user@host                (OpenSSH)
#   - macOS:   ssh user@host                (Remote Login enabled)
#   - Windows: ssh user@host                (OpenSSH Server; default shell can
#              be PowerShell — we invoke python explicitly so it doesn't matter)
#
# Configure hosts one of two ways:
#   1. Copy scripts/hosts.env.example -> scripts/hosts.env and edit it, OR
#   2. Export MACHINES="name=ssh_target:role:python_cmd:repo_path ..." inline.
#
# Each machine spec is colon-separated:
#   name          friendly label (mac / win / linux)
#   ssh_target    user@host (or an ssh config alias)
#   role          server | client | both
#   python_cmd    python3 | python | py -3   (interpreter on that box)
#   repo_path     absolute path to the teraguchi checkout on that box
#
# Example:
#   MACHINES="mac=me@10.0.0.5:both:python3:/Users/me/teraguchi \
#             linux=me@10.0.0.6:both:python3:/home/me/teraguchi \
#             win=me@10.0.0.7:client:python:C:/Users/me/teraguchi"
#
# Usage:
#   scripts/smoke_matrix.sh              # human table
#   scripts/smoke_matrix.sh --json       # raw per-host JSON (CI / debugging)
#   SSH_OPTS="-i ~/.ssh/lab" scripts/smoke_matrix.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSH_OPTS="${SSH_OPTS:--o ConnectTimeout=8 -o BatchMode=yes}"
JSON_MODE=0
[ "${1:-}" = "--json" ] && JSON_MODE=1

# Load hosts.env if present (defines MACHINES).
if [ -z "${MACHINES:-}" ] && [ -f "$HERE/hosts.env" ]; then
  # shellcheck disable=SC1091
  source "$HERE/hosts.env"
fi

if [ -z "${MACHINES:-}" ]; then
  echo "ERROR: no machines configured." >&2
  echo "  Copy scripts/hosts.env.example -> scripts/hosts.env and edit," >&2
  echo "  or export MACHINES=... (see header of this script)." >&2
  exit 2
fi

# Collect per-host results into parallel arrays.
declare -a R_NAME R_ROLE R_OS R_READY R_BLOCKERS

run_doctor() {
  # $1 ssh_target  $2 role  $3 python_cmd  $4 repo_path
  local target="$1" role="$2" py="$3" repo="$4"
  # cd into the repo, run the probe as a module, emit JSON. `2>/dev/null` on the
  # remote keeps stderr (venv noise) out of the JSON we parse.
  # shellcheck disable=SC2029
  ssh $SSH_OPTS "$target" \
    "cd '$repo' && $py -m common.doctor --role $role --json 2>/dev/null" 2>/dev/null || echo '{"__error__":true}'
}

jq_or_python() {
  # Prefer jq; fall back to python for parsing so the script has no hard dep.
  if command -v jq >/dev/null 2>&1; then jq -r "$1"; else
    python3 -c "import sys,json;d=json.load(sys.stdin);print($2)" 2>/dev/null || echo "?"
  fi
}

echo "== Teraguchi readiness sweep =="
for spec in $MACHINES; do
  name="${spec%%=*}"; rest="${spec#*=}"
  IFS=':' read -r target role py repo <<< "$rest"
  printf '  probing %-6s (%s, role=%s) ... ' "$name" "$target" "$role"
  out="$(run_doctor "$target" "$role" "${py:-python3}" "$repo")"

  if [ "$JSON_MODE" = "1" ]; then
    echo; echo "--- $name ---"; echo "$out"; continue
  fi

  if echo "$out" | grep -q '"__error__"'; then
    echo "UNREACHABLE"; R_NAME+=("$name"); R_ROLE+=("$role"); R_OS+=("?"); R_READY+=("UNREACHABLE"); R_BLOCKERS+=("-")
    continue
  fi
  os="$(echo "$out"      | jq_or_python '.platform.os'                 "d['platform']['os']")"
  ready="$(echo "$out"   | jq_or_python 'if .ready then "READY" else "BLOCKED" end' "'READY' if d['ready'] else 'BLOCKED'")"
  blockers="$(echo "$out"| jq_or_python '(.blockers // []) | join(",")' "','.join(d.get('blockers',[]))")"
  echo "$ready"
  R_NAME+=("$name"); R_ROLE+=("$role"); R_OS+=("$os"); R_READY+=("$ready"); R_BLOCKERS+=("${blockers:--}")
done

[ "$JSON_MODE" = "1" ] && exit 0

echo
printf '%-8s %-8s %-8s %-10s %s\n' "MACHINE" "OS" "ROLE" "STATUS" "BLOCKERS"
printf '%-8s %-8s %-8s %-10s %s\n' "-------" "--" "----" "------" "--------"
for i in "${!R_NAME[@]}"; do
  printf '%-8s %-8s %-8s %-10s %s\n' \
    "${R_NAME[$i]}" "${R_OS[$i]}" "${R_ROLE[$i]}" "${R_READY[$i]}" "${R_BLOCKERS[$i]}"
done

echo
echo "Next: for each READY server + READY client pair, run the two-machine"
echo "host<-client smoke (Piece 2, loopback e2e — to be added under tests/e2e/)."
