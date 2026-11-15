#!/usr/bin/env bash
# Run from an extracted build_offline_bundle.sh archive. Never runs a workload.
set -Eeuo pipefail
if [[ ${1:-} == --help ]]; then
  cat <<'EOF'
Usage: [PYTHON=python3] bash repo/scripts/deploy_offline.sh [site.yaml]
Requires an extracted offline bundle, Linux x86_64, matching CPython with venv/pip,
and a working Docker or nerdctl/containerd runtime plus the matching GPU runtime/driver.
Installs bundled Python wheels without a package index, loads images into the configured
runtime/namespace, checks local images, starts observability with --no-benchmark.
Inference service must already be running; edit site.yaml before invoking.
EOF
  exit 0
fi
[[ $# -le 1 ]] || { echo 'Use --help for usage' >&2; exit 2; }
bundle=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
[[ -f $bundle/SHA256SUMS && -f $bundle/metadata/offline-python.txt ]] || {
  echo 'Run this script from the extracted offline bundle: repo/scripts/deploy_offline.sh' >&2; exit 1;
}
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || { echo 'Requires Linux x86_64' >&2; exit 1; }
site=${1:-$bundle/site.yaml}
site=$(realpath "$site")
cd "$bundle"
sha256sum --check --quiet SHA256SUMS
PYTHON=${PYTHON:-python3}
required=$(cat metadata/offline-python.txt)
actual=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
[[ $actual == "$required" ]] || { echo "Bundle requires CPython $required; got $actual. Set PYTHON=/path/to/python$required" >&2; exit 1; }
"$PYTHON" -m venv "$bundle/.venv"
export PATH="$bundle/.venv/bin:$bundle/repo/bin:$PATH"
export PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1
python -m pip install --no-index --find-links "$bundle/wheelhouse" -r "$bundle/repo/requirements.txt"
# Make recovery commands available even if image import/startup fails.
cat > "$bundle/env.sh" <<'EOF'
_llm_bundle=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export PATH="$_llm_bundle/.venv/bin:$_llm_bundle/repo/bin:$PATH"
unset _llm_bundle
EOF
cd "$bundle/repo"
python - "$site" "$bundle" <<'PY'
from pathlib import Path
import subprocess, sys
sys.path.insert(0, 'scripts')
from benchmark_stack import validate_config
from config_io import read_object
from container_runtime import validate_runtime
site, bundle = map(Path, sys.argv[1:])
cfg = read_object(site)
if cfg.get('simulation') or cfg.get('offline') is not True:
    raise ValueError('Offline deployment requires simulation: false and offline: true')
validate_runtime(cfg)
cfg = validate_config(cfg)
root = Path(cfg['storage']['root'])
if (root / 'state.json').exists():
    raise ValueError('An active stack exists; use llm-bench status/down --config before redeploying')
# Load through exactly the same command/namespace used for deployment. No eval or registry access.
subprocess.run(cfg['runtime'] + ['info'], check=True)
subprocess.run(cfg['runtime'] + ['load', '-i', str(bundle / 'images/images-linux-amd64.tar')], check=True)
PY
llm-bench images check --config "$site"
llm-bench up --config "$site" --no-benchmark
llm-bench status --config "$site"
echo "Deployment complete (no benchmark workload run). Source $bundle/env.sh before using llm-bench."
