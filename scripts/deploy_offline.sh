#!/usr/bin/env bash
# Run from an extracted build_offline_bundle.sh archive. Never runs a workload.
set -Eeuo pipefail
if [[ ${1:-} == --help ]]; then
  cat <<'EOF'
Usage: [PYTHON=python3] bash repo/scripts/deploy_offline.sh [--simulation] [site.yaml]
Requires an extracted offline bundle, Linux x86_64, matching CPython with venv/pip,
and a working Docker or nerdctl/containerd runtime plus the matching GPU runtime/driver.
Installs bundled Python wheels without a package index, loads images into the configured
runtime/namespace, checks local images, starts observability with --no-benchmark.
Inference service must already be running; edit site.yaml before invoking.
With --simulation: import/check images and generate offline-simulation.yaml only.
No GPU, driver or real inference service required; no containers or workloads started.
Then source env.sh and use llm-bench up/verify/down --config offline-simulation.yaml.
SMOKE_PORT_BASE=44000 controls the seven published simulation ports.
EOF
  exit 0
fi
simulation=0
if [[ ${1:-} == --simulation ]]; then simulation=1; shift; fi
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
python - "$site" "$bundle" "$simulation" "${SMOKE_PORT_BASE:-44000}" <<'PY'
from pathlib import Path
import subprocess, sys
sys.path.insert(0, 'scripts')
from benchmark_stack import validate_config
from config_io import read_object
from container_runtime import validate_runtime
from bundle_support import simulation_config, write_simulation
site, bundle = map(Path, sys.argv[1:3])
cfg = read_object(site)
if sys.argv[3] == '1':
    cfg = simulation_config(cfg, bundle / 'tokenizer', bundle / 'generated/offline-smoke',
                            bundle / 'generated/offline-smoke-logs',
                            project='llm-bench-offline-smoke', port_base=int(sys.argv[4]))
else:
    if cfg.get('simulation') or cfg.get('offline') is not True:
        raise ValueError('Offline deployment requires simulation: false and offline: true')
validate_runtime(cfg)
cfg = validate_config(cfg)
root = Path(cfg['storage']['root'])
if (root / 'state.json').exists():
    raise ValueError('An active stack exists; use llm-bench status/down --config before redeploying')
# Generate host-local absolute paths; never reuse online build paths after extraction.
smoke = simulation_config(cfg, bundle / 'tokenizer', bundle / 'generated/offline-smoke',
                          bundle / 'generated/offline-smoke-logs',
                          project='llm-bench-offline-smoke', port_base=int(sys.argv[4]))
validate_config(smoke)
write_simulation(bundle / 'offline-simulation.yaml', smoke)
# Load through exactly the same command/namespace used for deployment. No eval or registry access.
subprocess.run(cfg['runtime'] + ['info'], check=True)
subprocess.run(cfg['runtime'] + ['load', '-i', str(bundle / 'images/images-linux-amd64.tar')], check=True)
PY
if [[ $simulation == 1 ]]; then
  llm-bench images check --config "$bundle/offline-simulation.yaml"
  echo "Import complete (no containers or workloads started). Source $bundle/env.sh, then run:"
  echo "llm-bench up --config $bundle/offline-simulation.yaml"
  echo "llm-bench verify --config $bundle/offline-simulation.yaml"
  exit 0
fi
llm-bench images check --config "$site"
llm-bench up --config "$site" --no-benchmark
llm-bench status --config "$site"
echo "Deployment complete (no benchmark workload run). Source $bundle/env.sh before using llm-bench."
