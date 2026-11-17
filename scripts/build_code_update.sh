#!/usr/bin/env bash
# Online source-only delivery; deliberately needs no container runtime or tokenizer.
set -Eeuo pipefail
if [[ ${1:-} == --help ]]; then
  cat <<'EOF'
Usage: BASE_REF=<deployed revision> [REF=main] bash scripts/build_code_update.sh
Requires Git, Python 3.9+ with venv/pip, tar and sha256sum; no Docker/GPU required.
REPO_URL defaults to https://github.com/shangbinbin/LLM-Inference-Benchmark.git.
OUTPUT_DIR defaults to $PWD/generated/code-delivery. BASE_REF is required and must
match the offline bundle's metadata/revision.txt. Pulls remote code, checks image/
dependency compatibility, runs unit tests, and packages source plus update metadata.
Does not build/pull/export images, download tokenizers or run container smoke tests.
EOF
  exit 0
fi
[[ $# == 0 && -n ${BASE_REF:-} ]] || { echo 'BASE_REF is required; use --help' >&2; exit 2; }
for cmd in git python3 tar sha256sum cmp; do command -v "$cmd" >/dev/null; done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else "Requires Python 3.9 or newer")'
REPO_URL=${REPO_URL:-https://github.com/shangbinbin/LLM-Inference-Benchmark.git}
REF=${REF:-main}
OUTPUT_DIR=${OUTPUT_DIR:-$PWD/generated/code-delivery}
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(cd "$OUTPUT_DIR" && pwd)
work=$(mktemp -d "$OUTPUT_DIR/.build-XXXXXXXX")
exec > >(tee "$work/build.log") 2>&1
phase=source-checkout
cleanup() {
  result=$?
  trap - EXIT
  if [[ $result != 0 ]]; then
    echo "Code update failed at stage: $phase (exit $result)" >&2
    if [[ $phase == unit-tests && -f $work/unit-tests.log ]]; then
      echo "Unit tests failed; last 100 lines of $work/unit-tests.log:" >&2
      tail -n 100 "$work/unit-tests.log" >&2 || true
    fi
    echo "Code update failed; logs retained in $work" >&2
  fi
  exit "$result"
}
trap cleanup EXIT
git clone --no-checkout -- "$REPO_URL" "$work/source"
cd "$work/source"
resolve() {
  [[ $1 != -* ]] || { echo 'Git ref cannot start with -' >&2; return 2; }
  if git show-ref --verify --quiet "refs/remotes/origin/$1"; then
    git rev-parse --verify "refs/remotes/origin/$1^{commit}"
  else
    git rev-parse --verify "$1^{commit}"
  fi
}
base_revision=$(resolve "$BASE_REF")
revision=$(resolve "$REF")
git checkout --detach "$revision"
mkdir "$work/base" "$work/new"
git archive "$base_revision" | tar -xf - -C "$work/base"
git archive "$revision" | tar -xf - -C "$work/new"
cmp -s "$work/base/requirements.txt" requirements.txt || {
  echo 'Host requirements changed; build a full offline bundle' >&2; exit 1;
}
phase=python-dependencies
python3 -m venv "$work/venv"
export PATH="$work/venv/bin:$PATH"
python -m pip install -r requirements.txt
phase=compatibility-check
python scripts/code_update.py check --base "$work/base" --source "$work/new"
phase=unit-tests
echo "[build] unit-tests; log: $work/unit-tests.log"
python -m unittest discover -s tests > "$work/unit-tests.log" 2>&1
phase=archive-creation
name="llm-bench-code-${base_revision:0:12}-to-${revision:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"
python scripts/code_update.py pack --base "$work/base" --source "$work/new" \
  --base-revision "$base_revision" --revision "$revision" \
  --test-log "$work/unit-tests.log" --output "$work/$name"
tar -czf "$OUTPUT_DIR/$name.tar.gz.partial" -C "$work" "$name"
mv "$OUTPUT_DIR/$name.tar.gz.partial" "$OUTPUT_DIR/$name.tar.gz"
(cd "$OUTPUT_DIR" && sha256sum "$name.tar.gz" > "$name.tar.gz.sha256")
echo "Code update ready: $OUTPUT_DIR/$name.tar.gz"
