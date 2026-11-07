#!/usr/bin/env python3
"""Run heterogeneous AISBench cases strictly in JSON order on an existing stack."""
import argparse
import copy
from dataclasses import asdict, dataclass
import math
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Optional

from suite_datasets import LocalDataset, resolve_dataset
from benchmark_stack import (load_config, ais_config, Stack, LABEL, ROOT, write_json,
                             runtime_command, aisbench_environment, check_local_images, validate_config)


@dataclass
class Case:
    name: str
    cfg: dict
    dataset: Optional[LocalDataset]

    def dataset_info(self):
        if self.dataset is None:
            return {'type': 'synthetic'}
        info = asdict(self.dataset)
        if self.dataset.type != 'jsonl':
            info.pop('prompt_field')
        return info


@dataclass
class Suite:
    cases: list
    cooldown: float
    stop_on_failure: bool


def prepare_suite(cfg, suite, directory=None):
    directory = Path.cwd() if directory is None else directory
    cases = suite['cases']
    if not isinstance(cases, list) or not cases:
        raise ValueError('cases must be a nonempty array')
    cooldown = suite.get('cooldown_seconds', 5)
    if type(cooldown) not in (int, float) or not math.isfinite(cooldown) or cooldown < 0:
        raise ValueError('cooldown_seconds must be finite and nonnegative')
    stop = suite.get('stop_on_failure', True)
    if type(stop) is not bool:
        raise ValueError('stop_on_failure must be a JSON boolean')
    prepared = []
    names = set()
    for case in cases:
        name = case['name']
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', name) or name in names:
            raise ValueError('Case names must be unique short lowercase identifiers')
        names.add(name)
        overrides = case.get('benchmark', {})
        if not isinstance(overrides, dict):
            raise ValueError(f'{name}: benchmark must be an object')
        unknown = set(overrides) - {
            'dataset_type','input_length','output_length','request_count','concurrency',
            'request_rate','warmups','mode','generation_kwargs','retry','prefix_length','pressure_seconds'}
        if unknown:
            raise ValueError(f'{name}: unsupported per-case fields: {sorted(unknown)}')
        case_cfg = copy.deepcopy(cfg)
        case_cfg['benchmark'].pop('concurrency_levels', None)
        case_cfg['benchmark'].pop('requests_per_level', None)
        case_cfg['benchmark'].update(overrides)
        try:
            dataset = resolve_dataset(case.get('dataset', {'type': 'synthetic'}), directory, overrides)
            if dataset:
                case_cfg['benchmark']['request_count'] = dataset.requests
            validate_config(case_cfg)
            if dataset:
                for key in ('dataset_type', 'input_length', 'prefix_length'):
                    case_cfg['benchmark'].pop(key, None)
                if dataset.type == 'sharegpt':
                    case_cfg['inference']['api'] = 'chat'
            prepared.append(Case(name, case_cfg, dataset))
        except (ValueError, TypeError, OSError) as exc:
            raise ValueError(f'{name}: {exc}') from exc
    return Suite(prepared, cooldown, stop)


def run_suite(cfg, suite):
    root = Path(cfg['storage']['root'])
    state = json.loads((root/'state.json').read_text())
    if state['project'] != cfg['project'] or state['status'] not in ('ready', 'complete'):
        raise ValueError('Start the matching stack with up --no-benchmark first, or wait for its benchmark to complete')
    directory = Path(state['config_dir'])
    manifest = json.loads((directory/'manifest.json').read_text())
    if manifest['stack'] != cfg:
        raise ValueError('Config differs from the running stack; use the original config')
    stack = Stack(cfg, directory, manifest['vendor'])
    run = root/'data/results/suites'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run.mkdir(parents=True)
    prepared = []
    for case in suite.cases:
        folder = run/case.name
        folder.mkdir()
        snapshot = dict(case.cfg)
        snapshot['dataset'] = case.dataset_info()
        write_json(folder/'stack.json', snapshot)
        (folder/'aisbench.py').write_text(ais_config(case.cfg, case.dataset))
        (folder/'results').mkdir()
        prepared.append((case, folder))
    lock = root/'suite.lock'
    summary = []
    with lock.open('x') as handle:
        handle.write(str(run))
    try:
        if cfg.get('offline', False):
            required = {}
            for key in ('aisbench', 'toolbox'):
                required.setdefault(cfg['images'][key], []).append(key)
            check_local_images(cfg, required, stack.call)
        else:
            stack.check_image(cfg['images']['aisbench'])
        for index, (case, folder) in enumerate(prepared):
            name = case.name
            if index:
                time.sleep(suite.cooldown)
            for container in state['containers']:
                info = stack.inspect(container)
                if container.endswith('-aisbench'):
                    if info and info['State']['Running']:
                        raise RuntimeError('A one-click benchmark is still running')
                elif not info or not info['State']['Running'] or info['Config']['Labels'].get(LABEL) != cfg['project']:
                    raise RuntimeError('Required owned container is unavailable: '+container)
            stack.probe(cfg['inference']['base_url'].rstrip('/')+'/v1/models', 'data')
            b = case.cfg['benchmark']
            inputs = []
            if case.dataset:
                inputs = ['--mount', case.dataset.mount()]
                if case.dataset.type == 'jsonl':
                    extension = ROOT / 'containers/aisbench/llm_bench_jsonl.py'
                    inputs += ['-e', 'PYTHONPATH=/config', '--mount',
                               f'type=bind,src={extension},dst=/config/llm_bench_jsonl.py,readonly']
            cmd = ['run',*aisbench_environment(cfg),'--rm','--platform',cfg['platform'],
                  '--name',cfg['project']+'-suite-client','--label',LABEL+'='+cfg['project'],
                  '--network',state['network'],
                  '--mount',f'type=bind,src={folder}/aisbench.py,dst=/config/aisbench.py,readonly',
                  '--mount',f'type=bind,src={folder}/results,dst=/results',
                  '--mount',f'type=bind,src={cfg["storage"]["tokenizer"]},dst=/tokenizer,readonly',
                  *inputs,
                  cfg['images']['aisbench'],'/config/aisbench.py','--mode','perf','--debug',
                  '--max-num-workers','1','--num-warmups',str(b.get('warmups',1))]
            if b['mode']=='pressure':
                cmd += ['--pressure','--pressure-time',str(b['pressure_seconds'])]
            entry = {'case':name,'started_at':datetime.now(timezone.utc).isoformat(),'benchmark':b}
            entry['dataset'] = case.dataset_info()
            print('START '+name, flush=True)
            with (folder/'client.log').open('w') as log:
                result = subprocess.run(runtime_command(cfg, cmd), stdout=log, stderr=subprocess.STDOUT)
            abbr = case.dataset.type if case.dataset else 'synthetic'
            reports = list((folder/'results').glob(f'*/performances/*/{abbr}.json'))
            data = [json.loads(p.read_text()) for p in reports]
            passed = result.returncode==0 and bool(data) and all(
                float(r['Failed Requests']['total'])==0 and float(r['Success Requests']['total'])>0 for r in data)
            if passed and b['mode']=='perf':
                passed = sum(float(r['Success Requests']['total']) for r in data)==b['request_count']
            entry.update({'finished_at':datetime.now(timezone.utc).isoformat(),'exit_code':result.returncode,
                          'passed':passed,'reports':[str(p.relative_to(run)) for p in reports]})
            summary.append(entry)
            write_json(run/'suite-results.json',summary)
            print(('PASS ' if passed else 'FAIL ')+name, flush=True)
            if not passed and suite.stop_on_failure:
                break
    finally:
        lock.unlink()
    print('Suite report: '+str(run/'suite-results.json'))
    return 0 if len(summary)==len(prepared) and all(c['passed'] for c in summary) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--suite', type=Path, required=True)
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
        code = run_suite(cfg, prepare_suite(cfg, json.loads(args.suite.read_text()), args.suite.resolve().parent))
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'ERROR: {exc}\n')
    if code:
        raise SystemExit(code)


if __name__=='__main__':
    main()
