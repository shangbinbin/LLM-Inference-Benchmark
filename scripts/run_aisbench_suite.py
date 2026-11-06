#!/usr/bin/env python3
"""Run heterogeneous AISBench cases strictly in JSON order on an existing stack."""
import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time

from benchmark_stack import load_config, ais_config, Stack, LABEL, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--suite', type=Path, required=True)
    args = parser.parse_args()
    cfg = load_config(args.config)
    root = Path(cfg['storage']['root'])
    state = json.loads((root/'state.json').read_text())
    if state['project'] != cfg['project'] or state['status'] not in ('ready', 'complete'):
        parser.error('Start the matching stack with up --no-benchmark first, or wait for its benchmark to complete')
    directory = Path(state['config_dir'])
    manifest = json.loads((directory/'manifest.json').read_text())
    if manifest['stack'] != cfg:
        parser.error('Config differs from the running stack; use the original config')
    stack = Stack(cfg, directory, manifest['vendor'])
    suite = json.loads(args.suite.read_text())
    cases = suite['cases']
    names = [c['name'] for c in cases]
    if not cases or len(set(names)) != len(names) or any(not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', n) for n in names):
        parser.error('Case names must be unique short lowercase identifiers')
    cooldown = suite.get('cooldown_seconds', 5)
    if not isinstance(cooldown, (float, int)) or cooldown < 0:
        parser.error('cooldown_seconds must be nonnegative')
    run = root/'data/results/suites'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run.mkdir(parents=True)
    # Validate every case before sending any request. Preserve common storage and target.
    prepared = []
    for case in cases:
        case_cfg = copy.deepcopy(cfg)
        unknown = set(case.get('benchmark', {})) - {
            'dataset_type','input_length','output_length','request_count','concurrency',
            'request_rate','warmups','mode','generation_kwargs','retry','prefix_length','pressure_seconds'}
        if unknown:
            parser.error('Unsupported per-case fields: '+str(sorted(unknown)))
        case_cfg['benchmark'].pop('concurrency_levels', None)
        case_cfg['benchmark'].pop('requests_per_level', None)
        case_cfg['benchmark'].update(case.get('benchmark', {}))
        folder = run/case['name']
        folder.mkdir()
        write_json(folder/'stack.json', case_cfg)
        case_cfg = load_config(folder/'stack.json')
        (folder/'aisbench.py').write_text(ais_config(case_cfg))
        (folder/'results').mkdir()
        prepared.append((case['name'], case_cfg, folder))
    lock = root/'suite.lock'
    summary = []
    with lock.open('x') as handle:
        handle.write(str(run))
    try:
        stack.check_image(cfg['images']['aisbench'])
        for index, (name, case_cfg, folder) in enumerate(prepared):
            if index:
                time.sleep(cooldown)
            for container in state['containers']:
                info = stack.inspect(container)
                if container.endswith('-aisbench'):
                    if info and info['State']['Running']:
                        raise RuntimeError('A one-click benchmark is still running')
                elif not info or not info['State']['Running'] or info['Config']['Labels'].get(LABEL) != cfg['project']:
                    raise RuntimeError('Required owned container is unavailable: '+container)
            stack.probe(cfg['inference']['base_url'].rstrip('/')+'/v1/models', 'data')
            b = case_cfg['benchmark']
            cmd = cfg['runtime'] + ['run','--rm','--platform',cfg['platform'],
                  '--name',cfg['project']+'-suite-client','--label',LABEL+'='+cfg['project'],
                  '--network',state['network'],
                  '--mount',f'type=bind,src={folder}/aisbench.py,dst=/config/aisbench.py,readonly',
                  '--mount',f'type=bind,src={folder}/results,dst=/results',
                  '--mount',f'type=bind,src={cfg["storage"]["tokenizer"]},dst=/tokenizer,readonly',
                  cfg['images']['aisbench'],'/config/aisbench.py','--mode','perf','--debug',
                  '--max-num-workers','1','--num-warmups',str(b.get('warmups',1))]
            if b['mode']=='pressure':
                cmd += ['--pressure','--pressure-time',str(b['pressure_seconds'])]
            entry = {'case':name,'started_at':datetime.now(timezone.utc).isoformat(),'benchmark':b}
            print('START '+name, flush=True)
            with (folder/'client.log').open('w') as log:
                result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
            reports = list((folder/'results').glob('*/performances/*/synthetic.json'))
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
            if not passed and suite.get('stop_on_failure',True):
                break
    finally:
        lock.unlink()
    print('Suite report: '+str(run/'suite-results.json'))
    if len(summary)!=len(cases) or not all(c['passed'] for c in summary):
        raise SystemExit(1)


if __name__=='__main__':
    main()
