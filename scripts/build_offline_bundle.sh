#!/usr/bin/env bash
# Online Linux/amd64 host. Settings are environment variables; see --help.
set -Eeuo pipefail
if [[ ${1:-} == --help ]]; then
  cat <<'EOF'
Usage: [REPO_URL=...] [REF=main] [OUTPUT_DIR=...] bash scripts/build_offline_bundle.sh
Online prerequisites: Linux x86_64, Git, Docker daemon/build support, Python 3.9+ with venv/pip, tar, sha256sum.
Optional: SITE_CONFIG=/absolute/site.yaml, DELIVERY_CONFIG=/absolute/delivery.yaml,
TOKENIZER_DIR=/absolute/tokenizer (optional existing tokenizer; otherwise download Qwen3-0.6B),
TOKENIZER_REVISION=master (ModelScope revision used by the default download),
OFFLINE_PYTHON=3.9 (offline host CPython minor version; independent of container Python 3.11), SMOKE_PORT_BASE=43000.
Default repository: https://github.com/shangbinbin/LLM-Inference-Benchmark.git
Always clones the requested remote ref, downloads and bundles tokenizer assets (no weights),
builds both project images, runs tokenid CPU fixture smoke and verifies metrics/logs/reports,
then exports selected images and creates a checked tar.gz.
No success archive is published if a step fails. Work directory and logs are retained.
EOF
  exit 0
fi
[[ $# == 0 ]] || { echo 'Use --help for usage' >&2; exit 2; }
for cmd in git docker python3 tar sha256sum; do command -v "$cmd" >/dev/null; done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else "Online host requires Python 3.9 or newer")'
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || { echo 'Requires Linux x86_64' >&2; exit 1; }
REPO_URL=${REPO_URL:-https://github.com/shangbinbin/LLM-Inference-Benchmark.git}
REF=${REF:-main}
for setting in SITE_CONFIG DELIVERY_CONFIG TOKENIZER_DIR; do
  [[ -z ${!setting:-} || ${!setting} == /* ]] || { echo "$setting must be an absolute path" >&2; exit 2; }
done
OFFLINE_PYTHON=${OFFLINE_PYTHON:-3.9}
[[ $OFFLINE_PYTHON =~ ^3\.(9|[1-9][0-9])$ ]] || { echo 'OFFLINE_PYTHON must be 3.9 or newer (major.minor, e.g. 3.9 or 3.11)' >&2; exit 2; }
export OFFLINE_PYTHON
OUTPUT_DIR=${OUTPUT_DIR:-$PWD/generated/delivery}
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(cd "$OUTPUT_DIR" && pwd)
work=$(mktemp -d "$OUTPUT_DIR/.build-XXXXXXXX")
exec > >(tee "$work/build.log") 2>&1
smoke_config=
smoke_owned=0
phase=preflight
unit_test_log=
stage() { phase=$1; echo "[build] $phase"; }
cleanup() {
  result=$?
  trap - EXIT
  if [[ $smoke_owned == 1 ]]; then
    "$work/venv/bin/python" "$work/source/bin/llm-bench" down --config "$smoke_config" || true
  fi
  if [[ $result != 0 ]]; then
    echo "Build failed at stage: $phase (exit $result)" >&2
    if [[ $phase == unit-tests && -n $unit_test_log && -f $unit_test_log ]]; then
      echo "Unit tests failed; last 100 lines of $unit_test_log:" >&2
      tail -n 100 "$unit_test_log" >&2 || true
    fi
    echo "Build failed; logs and evidence retained in $work" >&2
  fi
  exit "$result"
}
trap cleanup EXIT
stage preflight
docker info >/dev/null
stage source-checkout
git clone --no-checkout -- "$REPO_URL" "$work/source"
cd "$work/source"
if git show-ref --verify --quiet "refs/remotes/origin/$REF"; then
  git checkout --detach "refs/remotes/origin/$REF"
else
  git checkout --detach "$REF"
fi
revision=$(git rev-parse HEAD)
name="llm-bench-offline-${revision:0:12}-$(date -u +%Y%m%dT%H%M%SZ)"
bundle="$work/$name"
mkdir -p "$bundle/repo" "$bundle/metadata" "$bundle/wheelhouse"
git archive HEAD | tar -xf - -C "$bundle/repo"
stage python-dependencies
python3 -m venv "$work/venv"
export PATH="$work/venv/bin:$PATH"
python -m pip install -r requirements.txt
python -m pip download --only-binary=:all: --platform manylinux2014_x86_64 \
  --implementation cp --abi "cp${OFFLINE_PYTHON//./}" --python-version "$OFFLINE_PYTHON" \
  -r requirements.txt --dest "$bundle/wheelhouse"
printf '%s\n' "$OFFLINE_PYTHON" > "$bundle/metadata/offline-python.txt"
printf '%s\n' "$revision" > "$bundle/metadata/revision.txt"
cp "${SITE_CONFIG:-configs/sites/single-node.example.yaml}" "$bundle/site.yaml"
cp "${DELIVERY_CONFIG:-configs/delivery/offline-images.yaml}" "$bundle/delivery.yaml"
stage tokenizer-preparation
if [[ -z ${TOKENIZER_DIR:-} ]]; then
  python -m pip install -r requirements-build.txt
fi
# Bash 4.2/4.3 treats an empty array as unset under nounset. Keep this command
# nonempty for both the default download and the optional local tokenizer path.
tokenizer_command=(python scripts/bundle_support.py --output "$bundle/tokenizer"
  --metadata "$bundle/metadata/tokenizer.json" --revision "${TOKENIZER_REVISION:-master}")
if [[ -n ${TOKENIZER_DIR:-} ]]; then tokenizer_command+=(--source "$TOKENIZER_DIR"); fi
"${tokenizer_command[@]}"
stage simulation-configuration
smoke_config="$work/source/generated/delivery-smoke.yaml"
export BUNDLE_PATH="$bundle" SMOKE_CONFIG="$smoke_config" SMOKE_PORT_BASE=${SMOKE_PORT_BASE:-43000}
python - <<'PY'
import os
from pathlib import Path
import sys
sys.path.insert(0, 'scripts')
from config_io import read_object
from bundle_support import simulation_config, write_simulation
bundle = Path(os.environ['BUNDLE_PATH'])
cfg = simulation_config(read_object(bundle / 'site.yaml'), bundle / 'tokenizer',
                        'generated/delivery-smoke', 'generated/delivery-smoke-logs',
                        project='delivery-smoke-' + bundle.parent.name.removeprefix('.build-').lower(),
                        port_base=int(os.environ['SMOKE_PORT_BASE']), online=True)
write_simulation(os.environ['SMOKE_CONFIG'], cfg)
PY
stage unit-tests
unit_test_log="$bundle/metadata/unit-tests.log"
echo "Unit test log: $unit_test_log"
python -m unittest discover -s tests > "$unit_test_log" 2>&1
stage image-build
bin/llm-bench images build --config "$smoke_config"
# Pull/check images before smoke, but export delivery archives only after verification.
stage image-preparation
python scripts/prepare_offline_images.py --site "$bundle/site.yaml" \
  --config "$bundle/delivery.yaml" --output "$bundle" --pull-only
python - <<'PY'
import os, sys
sys.path.insert(0, 'scripts')
from config_io import read_object, write_yaml
path = os.environ['SMOKE_CONFIG']
cfg = read_object(path)
cfg['offline'] = True
write_yaml(path, cfg)
PY
smoke_owned=1
stage simulation-startup
bin/llm-bench up --config "$smoke_config"
stage simulation-verification
bin/llm-bench verify --config "$smoke_config"
cp generated/delivery-smoke/verification.json "$bundle/metadata/online-smoke.json"
cp "$smoke_config" "$bundle/metadata/online-smoke.yaml"
stage simulation-cleanup
bin/llm-bench down --config "$smoke_config"
smoke_owned=0
stage image-export
python scripts/prepare_offline_images.py --site "$bundle/site.yaml" \
  --config "$bundle/delivery.yaml" --output "$bundle" --local-only
cp "docs/单机共节点离线部署与分级压测.md" "$bundle/操作说明.md"
stage archive-creation
# site.yaml and delivery.yaml are intended to be edited on site; verify every immutable payload.
(cd "$bundle" && find repo images metadata wheelhouse -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
if [[ -d $bundle/tokenizer ]]; then
  (cd "$bundle" && find tokenizer -type f -print0 | sort -z | xargs -0 sha256sum >> SHA256SUMS)
fi
tar -czf "$OUTPUT_DIR/$name.tar.gz.partial" -C "$work" "$name"
mv "$OUTPUT_DIR/$name.tar.gz.partial" "$OUTPUT_DIR/$name.tar.gz"
(cd "$OUTPUT_DIR" && sha256sum "$name.tar.gz" > "$name.tar.gz.sha256")
echo "Bundle ready: $OUTPUT_DIR/$name.tar.gz"
echo "Build logs: $work/build.log"
