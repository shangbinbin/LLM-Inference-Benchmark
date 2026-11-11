#!/usr/bin/env python3
"""Verify production offline policy on a Mac simulation stack.

No images are pulled/built/deleted, and no Docker Desktop settings are changed.
Network isolation and disabled host publishing are injected; pull policy comes from production code.
"""
import copy
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import benchmark_stack as stack_module
import run_aisbench_suite
import slo_search


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


PROBE = '''
import json, socket, time, urllib.request, urllib.parse
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(url):
    with opener.open(url, timeout=5) as response:
        return json.load(response)
blocked = []
for address in ('1.1.1.1', '8.8.8.8'):
    try:
        with socket.create_connection((address, 443), timeout=2):
            raise RuntimeError('Unexpected public egress: ' + address)
    except OSError as exc:
        blocked.append({'address': address, 'error': str(exc)})
targets = get('http://prometheus:9090/api/v1/targets')['data']['activeTargets']
assert len(targets) >= 3 and all(t['health'] == 'up' for t in targets)
metric = METRIC
samples = get('http://prometheus:9090/api/v1/query?' + urllib.parse.urlencode({'query': metric}))['data']['result']
assert samples, metric
health = get('http://grafana:3000/api/health')
assert health['database'] == 'ok'
deadline = time.monotonic() + 30
while True:
    logs = get('http://loki:3100/loki/api/v1/query_range?' + urllib.parse.urlencode(
        {'query': '{service_name="inference"}', 'limit': 20}))['data']['result']
    if logs:
        break
    if time.monotonic() >= deadline:
        raise RuntimeError('No inference logs in Loki')
    time.sleep(1)
print(json.dumps({'blocked_public_tcp_probes': blocked,
    'targets': [{'url': t['scrapeUrl'], 'health': t['health']} for t in targets],
    'gpu_metric': metric, 'gpu_samples': len(samples), 'grafana_database': health['database'],
    'loki_log_entries': sum(len(s['values']) for s in logs)}))
'''


def main():
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64',
            'This validation entry point is for Apple Silicon Mac only')
    tag = uuid.uuid4().hex[:8]
    output = ROOT / 'generated/offline-validation' / tag
    output.mkdir(parents=True)
    base = json.loads((ROOT / 'configs/stacks/mac-offline.json').read_text())
    require(base['runtime'] == ['docker'] and base['platform'] == 'linux/arm64',
            'Expected the Docker arm64 simulation template')
    base['ports'] = {}
    base['publish_ports'] = False  # All checks run inside the isolated network.
    original_command = stack_module.command
    original_run = subprocess.run
    evidence = {'platform': 'linux/arm64', 'simulation': True, 'offline': True, 'run_id': tag,
                'started_at_utc': stack_module.datetime.now(stack_module.timezone.utc).isoformat(),
                'images': [], 'cases': []}
    active = {'name': 'preflight'}
    events = []

    def record(event):
        event['case'] = active['name']
        events.append(event)
        with (output / 'commands.jsonl').open('a') as stream:
            stream.write(json.dumps(event) + '\n')

    def guarded_run(args, *positional, **kwargs):
        args = list(map(str, args))
        require(args[0] == 'docker', 'Unexpected runtime')
        effective = args[:]
        if args[1] in ('build', 'pull'):
            record({'requested': args, 'blocked': True})
            raise RuntimeError('OFFLINE_GUARD: build/pull blocked')
        if args[1:3] == ['network', 'create']:
            effective.insert(3, '--internal')
        if args[1] in ('run', 'create'):
            require('--pull=never' in args, 'Production code omitted --pull=never')
        record({'requested': args, 'effective': effective})
        return original_run(effective, *positional, **kwargs)

    def blocked_download(url, *args, **kwargs):
        record({'host_download': str(url), 'blocked': True})
        raise RuntimeError('OFFLINE_GUARD: host download blocked')

    # Check selected local images first. Never acquire missing prerequisites.
    for key in ('toolbox', 'aisbench', 'node-exporter', 'loki', 'alloy', 'prometheus', 'grafana'):
        image = base['images'][key]
        info = json.loads(original_command(['docker', 'image', 'inspect', image], capture=True).stdout)[0]
        require(info['Os'] + '/' + info['Architecture'] == 'linux/arm64', 'Wrong image platform: ' + image)
        evidence['images'].append({'image': image, 'id': info['Id'], 'platform': 'linux/arm64'})

    def run_case(name, missing=None, vendor='nvidia'):
        active['name'] = name
        start = len(events)
        cfg = copy.deepcopy(base)
        cfg['project'] = 'offline-' + tag + '-' + name
        cfg['storage']['root'] = str(output / name / 'stack')
        cfg['storage']['logs'] = str(output / name / 'logs')
        cfg['gpu']['vendor'] = vendor
        missing_keys = [missing] if isinstance(missing, str) else (missing or [])
        for key in missing_keys:
            cfg['images'][key] = 'registry.invalid/offline-missing:' + tag + '-' + key
            result = original_command(['docker', 'image', 'inspect', cfg['images'][key]],
                                      capture=True, check=False)
            require(result.returncode != 0, 'Negative test tag unexpectedly exists')
        config = output / (name + '.json')
        stack_module.write_json(config, cfg)
        cfg = stack_module.load_config(config)
        directory, selected_vendor = stack_module.prepare(cfg)
        stack = stack_module.Stack(cfg, directory, selected_vendor)
        outcome = {'name': name, 'vendor': vendor, 'status': 'failed'}
        evidence['cases'].append(outcome)
        try:
            failure = None
            try:
                stack.up()
            except (ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                failure = str(exc)
            if missing:
                require(failure is not None, 'Missing-image run unexpectedly succeeded')
                current = events[start:]
                require('Missing images (' + str(len(missing_keys)) + ')' in failure, 'Missing list is incomplete')
                for key in missing_keys:
                    require(cfg['images'][key] in failure, 'Missing tag omitted from error')
                require(not any(e.get('blocked') for e in current), 'Production policy tried build/download')
                require(not any(e.get('requested', [])[1:2] in (['run'], ['create']) or
                                e.get('requested', [])[1:3] == ['network', 'create'] for e in current),
                        'Resources were created before the missing-image failure')
                require(not stack.statefile.exists(), 'Failed preflight wrote runtime state')
                if missing == 'aisbench':
                    stack.preflight_images(False)
                    outcome['monitor_only_preflight_passed'] = True
                for key in ('toolbox', 'aisbench'):
                    try:
                        stack.build_one(key)
                    except ValueError as exc:
                        require('Offline mode forbids' in str(exc), 'Unexpected build rejection')
                    else:
                        raise RuntimeError('Offline build unexpectedly allowed')
                outcome.update(status='passed', expected_failure=failure)
            else:
                require(failure is None, failure or 'Stack failed')
                network = json.loads(stack.call('network', 'inspect', stack.network, capture=True).stdout)[0]
                require(network['Internal'] is True, 'Network is not internal')
                for name_in_state in stack.created:
                    info = stack.inspect(name_in_state)
                    require(set(info['NetworkSettings']['Networks']) == {stack.network},
                            'Container attached to an unexpected network')
                metric = 'DCGM_FI_DEV_GPU_UTIL' if vendor == 'nvidia' else 'hcu_utilizationrate'
                result = stack.call('run', '--rm', '--platform', 'linux/arm64', '--network', stack.network,
                                    '--entrypoint', 'python', cfg['images']['toolbox'], '-c',
                                    PROBE.replace('metric = METRIC', 'metric = ' + repr(metric)), capture=True)
                observations = json.loads(result.stdout)
                reports = list((stack.root / 'data/results').glob('*/performances/*/synthetic.json'))
                require(len(reports) == 1, 'Expected one AISBench report')
                report = json.loads(reports[0].read_text())
                require(not any(e.get('blocked') for e in events[start:]), 'Positive run attempted build/download')
                outcome.update(status='passed', internal_network=True, observations=observations,
                               success_requests=report['Success Requests']['total'],
                               failed_requests=report['Failed Requests']['total'],
                               report=str(reports[0].relative_to(output)))
                if vendor == 'nvidia':
                    suite_path = output / 'suite.json'
                    stack_module.write_json(suite_path, {'cooldown_seconds': 0, 'cases': [
                        {'name': 'string', 'benchmark': {'request_count': 4}},
                        {'name': 'tokenid', 'benchmark': {'request_count': 4, 'dataset_type': 'tokenid'}}]})
                    with patch.object(sys, 'argv', ['suite', '--config', str(config), '--suite', str(suite_path)]):
                        run_aisbench_suite.main()
                    suite_report = next((stack.root/'data/results/suites').glob('*/suite-results.json'))
                    suite_results = json.loads(suite_report.read_text())
                    require(len(suite_results) == 2 and all(c['passed'] for c in suite_results), 'Offline suite failed')
                    search_path = output / 'search.json'
                    stack_module.write_json(search_path, {'min_concurrency': 1, 'max_concurrency': 2,
                        'repeats': 1, 'min_requests': 8, 'max_rounds': 2, 'max_duration_seconds': 180,
                        'trial_timeout_seconds': 60, 'cooldown_seconds': 0, 'thresholds_ms': {'TTFT': 1000, 'TPOT': 1000}})
                    with patch.object(sys, 'argv', ['search', '--config', str(config), '--search', str(search_path)]):
                        require(slo_search.main() == 0, 'Offline search failed')
                    search_report = next((stack.root/'data/results/slo-search').glob('*/search-results.json'))
                    search_result = json.loads(search_report.read_text())
                    require(search_result['recommended_concurrency'] == 2, 'Unexpected search result')
                    outcome.update(suite_cases_passed=2, search_trials=search_result['trial_count'],
                                   search_recommended_concurrency=search_result['recommended_concurrency'])
                # Check effective settings, not merely the generated files.
                alloy = stack.inspect(stack.project + '-alloy')
                require('--disable-reporting' in alloy['Config']['Cmd'], 'Alloy reporting remains enabled')
                for component in ('grafana', 'alloy', 'loki'):
                    logged = stack.call('logs', stack.project + '-' + component, capture=True)
                    log = logged.stdout + logged.stderr
                    require(not any(marker in log for marker in ('Update check failed', 'Failed to install plugin',
                        'Error while updating detectors', 'Error downloading plugin manifest keys',
                        'stats.grafana.org', 'grafana.com/api/')), 'External activity in ' + component + ' logs')
                outcome['no_known_external_requests_in_logs'] = True
                require(not any(e.get('blocked') for e in events[start:]), 'Offline path attempted forbidden operation')
        finally:
            # Only remove objects owned by this case; preserve evidence and unrelated stacks.
            if stack.statefile.exists():
                for name_in_state in stack.created:
                    result = stack.call('logs', '--tail', '100', name_in_state, capture=True, check=False)
                    (output / (name_in_state + '.log')).write_text(result.stdout + result.stderr)
                stack.down()
            remaining = stack.call('ps', '-aq', '--filter', 'label=' + stack_module.LABEL + '=' + cfg['project'], capture=True)
            require(not remaining.stdout.strip(), 'Owned containers remain after cleanup')
            remaining = stack.call('network', 'ls', '-q', '--filter', 'label=' + stack_module.LABEL + '=' + cfg['project'], capture=True)
            require(not remaining.stdout.strip(), 'Owned network remains after cleanup')
            outcome['cleaned_up'] = True
            stack_module.write_json(output / 'summary.json', evidence)

    print('Evidence directory: ' + str(output), flush=True)
    with patch.object(subprocess, 'run', guarded_run), patch.object(stack_module.urllib.request, 'urlopen', blocked_download):
        for name, missing in [('missing-toolbox', 'toolbox'), ('missing-aisbench', 'aisbench'),
                              ('missing-public', 'node-exporter'),
                              ('missing-many', ['toolbox', 'aisbench', 'grafana'])]:
            run_case(name, missing=missing)
        run_case('nvidia')
        run_case('hygon', vendor='hygon')
    evidence['status'] = 'passed'
    stack_module.write_json(output / 'summary.json', evidence)
    print('PASS: ' + str(output / 'summary.json'), flush=True)


if __name__ == '__main__':
    main()
