#!/usr/bin/env python3
"""Create a local site JSON from a reviewed example without overwriting existing files."""
import argparse
import copy
import json
from pathlib import Path

from benchmark_stack import ROOT, validate_config
from container_runtime import runtime_scope, select_runtime

DEFAULT_SOURCE = ROOT / 'configs/sites/gpu41-qwen3-0.6b-random-1k.json'


def create_site(source, output, runtime=None, namespace=None):
    cfg = json.loads(source.read_text(encoding='utf-8'))
    if not isinstance(cfg, dict):
        raise ValueError('The source configuration must be a JSON object')
    select_runtime(cfg, runtime, namespace)
    # Validate a separate copy: preserve the example's relative storage paths in the output.
    # Validation neither starts containers nor creates storage.logs or reads a Token value.
    # The site's tokenizer may only exist on the offline host; plan/up check it there.
    validate_config(copy.deepcopy(cfg), check_tokenizer=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
    return cfg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE, help='complete stack JSON; defaults to the GPU41 example')
    parser.add_argument('--output', type=Path, default=Path('generated/site/site.json'), help='new output path, relative to the current directory')
    parser.add_argument('--runtime', choices=('docker', 'nerdctl'), help='override the example runtime')
    parser.add_argument('--namespace', help='nerdctl namespace; preserve the source namespace or use benchmark')
    args = parser.parse_args()
    try:
        cfg = create_site(args.source, args.output, args.runtime, args.namespace)
        print('Created site config: ' + str(args.output.resolve()))
        print('Runtime: ' + json.dumps(runtime_scope(cfg), ensure_ascii=False))
        print('Review service addresses, paths, ports and workload before starting the stack.')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(1, 'ERROR: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
