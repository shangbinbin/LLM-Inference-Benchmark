#!/usr/bin/env python3
"""Verify a running Mac fixture stack using real metrics, logs and AISBench reports."""
import argparse
import base64
import json
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request
import uuid

from benchmark_stack import load_config, Stack, LABEL
from observability_labels import task_name
from gpu_metrics import DEVICE_METRICS


def get(url, auth=False):
    headers = {}
    if auth:
        login = 'admin:' + os.environ.get('BENCH_GRAFANA_PASSWORD', 'admin')
        headers['Authorization'] = 'Basic ' + base64.b64encode(login.encode()).decode()
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as response:
        return json.load(response)


def eventually(check, timeout=60):
    deadline = time.monotonic() + timeout
    error = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except Exception as exc:
            error = exc
        time.sleep(1)
    raise RuntimeError(f'Verification timed out: {error}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--exercise-restart', action='store_true', help='rotate fixture logs and restart owned collectors')
    args = parser.parse_args()
    cfg = load_config(args.config)
    if not cfg.get('simulation'):
        parser.error('This verifier is restricted to simulation stacks')
    root = Path(cfg['storage']['root'])
    state = json.loads((root/'state.json').read_text())
    directory = Path(state['config_dir'])
    manifest = json.loads((directory/'manifest.json').read_text())
    if manifest['stack'] != cfg or state['project'] != cfg['project']:
        parser.error('Running state does not match supplied config')
    stack = Stack(cfg, directory, manifest['vendor'])
    evidence = {'simulation': True, 'platform': cfg['platform'], 'vendor': manifest['vendor'], 'checks': {}}
    checks = evidence['checks']
    for name in state['containers']:
        info = stack.inspect(name)
        assert info and info['Config']['Labels'].get(LABEL) == cfg['project'], name
        if name.endswith('-aisbench'):
            assert info['State']['Status'] == 'exited' and info['State']['ExitCode'] == 0, name
        else:
            assert info['State']['Running'], name
    checks['container_states'] = True
    if not cfg.get('publish_ports', True) or any(not cfg['ports'][key] for key in ('prometheus', 'loki', 'grafana')):
        parser.error('Simulation verification needs published Prometheus, Loki and Grafana ports')
    address = cfg.get('port_bind_address', '0.0.0.0')
    address = {'0.0.0.0':'127.0.0.1', '::':'::1'}.get(address, address)
    address = '[' + address + ']' if ':' in address else address
    prom = 'http://' + address + ':' + str(cfg['ports']['prometheus'])
    loki = 'http://' + address + ':' + str(cfg['ports']['loki'])
    grafana = 'http://' + address + ':' + str(cfg['ports']['grafana'])
    def query(expr):
        return get(prom+'/api/v1/query?'+urllib.parse.urlencode({'query': expr}))['data']['result']
    targets = get(prom+'/api/v1/targets')['data']['activeTargets']
    assert len(targets) >= 3 and all(t['health']=='up' for t in targets)
    checks['targets_up'] = [t['scrapeUrl'] for t in targets]
    metric = DEVICE_METRICS[manifest['vendor']]
    checks['gpu_series'] = eventually(lambda: query(metric+'{task='+json.dumps(task_name(cfg))+'}'))
    for series in checks['gpu_series']:
        assert all(series['metric'].get(label) for label in ('task', 'gpu_node', 'gpu_model', 'gpu_index'))
    assert not query(DEVICE_METRICS['hygon' if manifest['vendor']=='nvidia' else 'nvidia'])
    checks['other_vendor_absent'] = True
    checks['node_series'] = eventually(lambda: query('node_cpu_seconds_total'))[0]['metric']
    count = eventually(lambda: query('benchmark_mock_requests_total'))
    assert float(count[0]['value'][1]) >= cfg['benchmark']['request_count']
    checks['fixture_requests'] = count[0]['value'][1]
    assert get(grafana+'/api/health')['database'] == 'ok'
    datasources = get(grafana+'/api/datasources', True)
    assert {'prometheus','loki'} <= {d['type'] for d in datasources}
    dashboards = get(grafana+'/api/search?type=dash-db', True)
    assert any(d.get('uid') == 'inference-task-overview' for d in dashboards), 'Task overview dashboard missing'
    checks['grafana_dashboards'] = [d['title'] for d in dashboards]
    checks['grafana_datasources'] = [d['type'] for d in datasources]
    def logs(marker):
        expr = '{task='+json.dumps(task_name(cfg))+'} |= '+json.dumps(marker)
        result = get(loki+'/loki/api/v1/query_range?'+urllib.parse.urlencode({
            'query': expr, 'start': str(time.time_ns()-3600*10**9), 'end': str(time.time_ns()), 'limit': 100}))
        return result['data']['result']
    checks['inference_logs'] = bool(eventually(lambda: logs('output_tokens')))
    # Inspect only the newest run, never let an older successful report mask a failed run.
    runs = sorted(p for p in (root/'data/results').iterdir() if p.is_dir() and p.name[:8].isdigit())
    assert runs, 'AISBench result folder missing'
    reports = list(runs[-1].glob('performances/*/synthetic.json'))
    assert reports, 'AISBench summary missing'
    summaries = [json.loads(p.read_text()) for p in reports]
    for report in summaries:
        assert float(report['Failed Requests']['total']) == 0, report
        assert float(report['Success Requests']['total']) > 0, report
    checks['aisbench_reports'] = [str(p.relative_to(root)) for p in reports]
    checks['aisbench_counts'] = [{k: s[k] for k in ('Total Requests','Success Requests','Failed Requests')} for s in summaries]
    if args.exercise_restart:
        marker = 'verification-' + uuid.uuid4().hex
        logfile = Path(cfg['storage']['logs'])/'server.log'
        with logfile.open('a') as f:
            f.write(json.dumps({'simulation': True, 'marker': marker})+'\n')
        eventually(lambda: logs(marker))
        stack.call('exec', cfg['project']+'-logrotate', 'logrotate', '-f', '--state', '/state/logrotate.status', '/etc/logrotate.d/inference')
        assert list(logfile.parent.glob('server.log-*.gz'))
        stack.call('restart', cfg['project']+'-alloy', cfg['project']+'-prometheus', cfg['project']+'-loki', cfg['project']+'-grafana')
        eventually(lambda: get(grafana+'/api/health')['database']=='ok')
        eventually(lambda: logs(marker))
        with logfile.open('a') as f:
            f.write(json.dumps({'simulation': True, 'marker': marker+'-after-restart'})+'\n')
        eventually(lambda: logs(marker+'-after-restart'))
        eventually(lambda: query(metric))
        checks['rotation_restart_persistence'] = True
    (root/'verification.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
