#!/usr/bin/env bash
# Online Linux/amd64 host. Settings are environment variables; see --help.
set -Eeuo pipefail
if [[ ${1:-} == --help ]]; then
  cat <<'EOF'
Usage: [REPO_URL=...] [REF=main] [OUTPUT_DIR=...] bash scripts/build_offline_bundle.sh
Online prerequisites: Linux x86_64, Git, Docker daemon/build support, Python 3.10+ with venv/pip, tar, sha256sum.
Optional: SITE_CONFIG=/absolute/site.yaml, DELIVERY_CONFIG=/absolute/delivery.yaml,
TOKENIZER_DIR=/absolute/tokenizer (copied into bundle; no model weights),
OFFLINE_PYTHON=3.11 (offline CPython minor version), SMOKE_PORT_BASE=43000.
Default repository: https://github.com/shangbinbin/LLM-Inference-Benchmark.git
Always clones the requested remote ref, builds both project images, runs CPU fixture smoke
and verifies metrics/logs/reports, then exports selected images and creates a checked tar.gz.
No success archive is published if a step fails. Work directory and logs are retained.
EOF
  exit 0
fi
[[ $# == 0 ]] || { echo 'Use --help for usage' >&2; exit 2; }
for cmd in git docker python3 tar sha256sum; do command -v "$cmd" >/dev/null; done
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || { echo 'Requires Linux x86_64' >&2; exit 1; }
REPO_URL=${REPO_URL:-https://github.com/shangbinbin/LLM-Inference-Benchmark.git}
REF=${REF:-main}
OFFLINE_PYTHON=${OFFLINE_PYTHON:-3.11}
[[ $OFFLINE_PYTHON =~ ^3\.(1[0-9])$ ]] || { echo 'OFFLINE_PYTHON must be 3.10 or newer' >&2; exit 2; }
export OFFLINE_PYTHON
OUTPUT_DIR=${OUTPUT_DIR:-$PWD/generated/delivery}
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR=$(cd "$OUTPUT_DIR" && pwd)
work=$(mktemp -d "$OUTPUT_DIR/.build-XXXXXXXX")
exec > >(tee "$work/build.log") 2>&1
smoke_config=
smoke_owned=0
cleanup() {
  result=$?
  trap - EXIT
  if [[ $smoke_owned == 1 ]]; then
    "$work/venv/bin/python" "$work/source/bin/llm-bench" down --config "$smoke_config" || true
  fi
  if [[ $result != 0 ]]; then echo "Build failed; logs and evidence retained in $work" >&2; fi
  exit "$result"
}
trap cleanup EXIT
docker info >/dev/null
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
if [[ -n ${TOKENIZER_DIR:-} ]]; then
  [[ -f $TOKENIZER_DIR/tokenizer_config.json ]] || { echo 'TOKENIZER_DIR needs tokenizer_config.json' >&2; exit 1; }
  mkdir "$bundle/tokenizer"
  # Include only tokenizer assets, never copy an entire model directory/weight files.
  python - "$TOKENIZER_DIR" "$bundle/tokenizer" <<'PY'
from pathlib import Path
import shutil, sys
src, dst = map(Path, sys.argv[1:])
patterns = ('tokenizer*', 'special_tokens_map.json', 'added_tokens.json', 'vocab*',
            'merges.txt', '*.model', 'config.json', 'chat_template*')
for path in src.iterdir():
    if path.is_file() and any(path.match(pattern) for pattern in patterns):
        shutil.copyfile(path, dst / path.name)
    elif path.is_dir() and path.name == 'chat_templates':
        shutil.copytree(path, dst / path.name)
PY
fi
smoke_config="$work/source/generated/delivery-smoke.yaml"
export BUNDLE_PATH="$bundle" SMOKE_CONFIG="$smoke_config" SMOKE_PORT_BASE=${SMOKE_PORT_BASE:-43000}
python - <<'PY'
import os
from pathlib import Path
import sys
sys.path.insert(0, 'scripts')
from config_io import read_object, write_yaml
cfg = read_object('configs/stacks/nerdctl-offline-smoke.yaml')
cfg.update(project='delivery-smoke-' + Path(os.environ['BUNDLE_PATH']).parent.name.removeprefix('.build-').lower(),
           runtime=['docker'], offline=False, task='delivery-smoke')
cfg['images'] = read_object(Path(os.environ['BUNDLE_PATH']) / 'site.yaml')['images']
cfg['storage'].update(root='generated/delivery-smoke', logs='generated/delivery-smoke-logs')
tokenizer = Path(os.environ['BUNDLE_PATH']) / 'tokenizer'
if tokenizer.is_dir():
    cfg['storage']['tokenizer'] = str(tokenizer)
base = int(os.environ['SMOKE_PORT_BASE'])
if not 1024 <= base <= 65529:
    raise ValueError('SMOKE_PORT_BASE must be 1024..65529')
cfg['ports'] = {key: base + i for i, key in enumerate(cfg['ports'])}
write_yaml(os.environ['SMOKE_CONFIG'], cfg)
PY
python -m unittest discover -s tests > "$bundle/metadata/unit-tests.log" 2>&1
bin/llm-bench images build --config "$smoke_config"
# Pull and validate all archives before smoke; runtime then runs with pull=never.
python scripts/prepare_offline_images.py --site "$bundle/site.yaml" \
  --config "$bundle/delivery.yaml" --output "$bundle"
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
bin/llm-bench up --config "$smoke_config"
python scripts/verify_simulation.py --config "$smoke_config"
cp generated/delivery-smoke/verification.json "$bundle/metadata/online-smoke.json"
cp "$smoke_config" "$bundle/metadata/online-smoke.yaml"
bin/llm-bench down --config "$smoke_config"
smoke_owned=0
cp docs/single-node-offline.md "$bundle/操作说明.md"
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
