#!/usr/bin/env bash
# Run the updater from the extracted update package, not from the target repository.
set -Eeuo pipefail
if [[ ${1:-} == --help || $# == 0 ]]; then
  cat <<'EOF'
Usage: bash repo/scripts/apply_code_update.sh TARGET_BUNDLE [--config /path/site.yaml ...]
Rollback: bash repo/scripts/apply_code_update.sh TARGET_BUNDLE --rollback BACKUP_ID [--config ...]
TARGET_BUNDLE is the existing full bundle root (contains repo/, metadata/, .venv/).
Uses TARGET_BUNDLE/.venv/bin/python, or explicit PYTHON. Stop owned stacks with the
old CLI first. Preserves site.yaml, tokenizer, images, venv, generated data and custom
untracked files. No package installation, runtime commands or workloads are run.
EOF
  exit 0
fi
target=$(cd "$1" && pwd)
shift
update=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PYTHON=${PYTHON:-$target/.venv/bin/python}
export PYTHONDONTWRITEBYTECODE=1
if [[ ${1:-} == --rollback ]]; then
  [[ $# -ge 2 ]] || { echo '--rollback requires a backup ID' >&2; exit 2; }
  backup=$2
  shift 2
  exec "$PYTHON" "$update/repo/scripts/code_update.py" rollback --target "$target" --backup "$backup" "$@"
fi
exec "$PYTHON" "$update/repo/scripts/code_update.py" apply --target "$target" --update "$update" "$@"
