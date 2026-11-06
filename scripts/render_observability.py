#!/usr/bin/env python3
"""Compose common, hardware, network and environment settings (Python stdlib only)."""
import argparse
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / 'configs'


def read(path):
    return json.loads(path.read_text())


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def config_dir(value):
    path = (CONFIGS / value).resolve()
    if not path.is_relative_to(CONFIGS.resolve()) or not path.is_dir():
        raise ValueError(f'Invalid configuration directory: {value}')
    return path


def validate(env, profile, adapter, allow_template):
    if profile['vendor'] != adapter['vendor']:
        raise ValueError('GPU profile and adapter vendors differ')
    if not allow_template and ('template' in [env['status'], profile['status'], adapter['status']]
                               or 'REPLACE_' in json.dumps([env, profile, adapter])):
        raise ValueError('Unfinished template; use --allow-template for preview only')
    inf = env['inference']
    if inf['engine'] != 'sglang':
        raise ValueError('Only the SGLang application metrics adapter is implemented')
    nodes = env['nodes']
    if not nodes or len({n['name'] for n in nodes}) != len(nodes):
        raise ValueError('Nodes must be nonempty with unique names')
    if len({n['gpu_target'] for n in nodes}) != len(nodes):
        raise ValueError('Each GPU exporter target must be unique')
    if len(inf['log_files']) != len(nodes) or len(set(inf['log_files'])) != len(nodes):
        raise ValueError('Provide one unique active log file per node rank')
    for value in [inf['tp'], inf['pp']] + [n['gpus'] for n in nodes]:
        if type(value) is not int or value <= 0:
            raise ValueError('TP, PP and GPU counts must be positive integers')
    for path in inf['log_files']:
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', path) or '..' in Path(path).parts:
            raise ValueError('Log files must be absolute paths without wildcards, spaces or traversal')
    for url in env['services'].values():
        if not re.fullmatch(r'https?://[^\s"\\]+', url):
            raise ValueError('Service URLs must be HTTP(S) URLs without quotes or whitespace')


def render(environment, output, allow_template=False):
    env = read(environment)
    profile = read(config_dir(env['gpu_profile']) / 'profile.json')
    gpu = config_dir(profile['adapter'])
    adapter = read(gpu / 'adapter.json')
    validate(env, profile, adapter, allow_template)
    # Output must be fresh: never mix dashboards from two different GPU adapters.
    if output.exists():
        raise ValueError(f'Output already exists: {output}; choose a new directory')
    inf = env['inference']
    nodes = env['nodes']
    labels = {'service': 'sglang', 'deployment': inf['deployment'],
              'inference_group': inf['group'], 'model': inf['model'],
              'topology': f"{len(nodes)}node-tp{inf['tp']}-pp{inf['pp']}",
              'environment': env['name'], 'gpu_vendor': profile['vendor'],
              'gpu_model': profile['model'], 'replica_id': '0'}
    prom = read(CONFIGS / 'common/prometheus/base.json')
    prom['scrape_configs'] = [
        {'job_name': 'sglang-distributed-master', 'scheme': 'http', 'metrics_path': '/metrics',
         'scrape_interval': '2s', 'scrape_timeout': '1s',
         'static_configs': [{'targets': [inf['metrics_target']],
                             'labels': {**labels, 'role': 'master', 'node_rank': '0',
                                        'pod': inf['deployment'] + '-0'}}]},
        {k: adapter[k] for k in ('job_name', 'scheme', 'metrics_path')}
    ]
    prom['scrape_configs'][1]['static_configs'] = [
        {'targets': [n['gpu_target']], 'labels': {'service': adapter['job_name'],
         'node': n['name'], 'environment': env['name'], 'gpu_vendor': profile['vendor'],
         'gpu_model': profile['model']}} for n in nodes]
    network = None
    if env.get('network'):
        net = env['network']
        network = config_dir(net['adapter'])
        job = read(network / 'scrape-job.json')
        if net.get('dns_names'):
            job['dns_sd_configs'] = [{'names': net['dns_names'], 'type': 'A',
                                      'port': net['port'], 'refresh_interval': '30s'}]
        elif net.get('targets'):
            job['static_configs'] = [{'targets': net['targets']}]
        else:
            raise ValueError('Network requires dns_names + port or targets')
        prom['scrape_configs'].append(job)
        prom['rule_files'].append('/etc/prometheus/rules/network.yml')
    dashboards = [(CONFIGS / 'common/grafana/dashboards/sglang.json', 'sglang.json'),
                  (gpu / adapter['dashboard'], 'gpu.json')]
    if network:
        dashboards.append((network / 'grafana/network.json', 'network.json'))
    # Read all JSON inputs before creating the output directory.
    dashboard_data = [(read(path), name) for path, name in dashboards]
    common = CONFIGS / 'common'
    alloy = (common / 'alloy/config.alloy.tmpl').read_text()
    targets = []
    for rank, log in enumerate(inf['log_files']):
        targets.append({**labels, '__path__': log, 'job': 'sglang', 'service_name': 'sglang',
                        'node_rank': str(rank), 'role': 'master' if rank == 0 else 'worker',
                        'node': nodes[rank]['name'], 'pod': f"{inf['deployment']}-{rank}",
                        'instance': f"{inf['deployment']}-{rank}"})
    # Alloy maps use equals and trailing commas, not JSON colons.
    target_text = '[\n' + '\n'.join('    {\n' + '\n'.join(
        f'      {json.dumps(k)} = {json.dumps(v, ensure_ascii=False)},' for k, v in t.items())
        + '\n    },' for t in targets) + '\n  ]'
    alloy = alloy.replace('@@LOG_TARGETS@@', target_text).replace(
        '@@LOKI_PUSH_URL@@', env['services']['loki_url'].rstrip('/') + '/loki/api/v1/push')
    rotate = (common / 'logrotate/inference.conf.tmpl').read_text().replace(
        '@@LOG_FILES@@', ' '.join(inf['log_files']))
    output.mkdir(parents=True)
    write_json(output / 'prometheus/prometheus.yml', prom)  # JSON is valid YAML.
    for data, name in dashboard_data:
        write_json(output / 'grafana/dashboards' / name, data)
    write_json(output / 'grafana/provisioning/dashboards/benchmark.yml',
               read(common / 'grafana/dashboard-provider.json'))
    write_json(output / 'grafana/provisioning/datasources/benchmark.yml', {'apiVersion': 1,
               'datasources': [
                   {'name': 'Benchmark Prometheus', 'uid': 'benchmark-prometheus', 'type': 'prometheus',
                    'access': 'proxy', 'url': env['services']['prometheus_url'], 'isDefault': True},
                   {'name': 'Benchmark Loki', 'uid': 'benchmark-loki', 'type': 'loki',
                    'access': 'proxy', 'url': env['services']['loki_url']} ]})
    for path, content in [('alloy/config.alloy', alloy), ('logrotate/inference.conf', rotate)]:
        dest = output / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    for source, destination in [(common / 'grafana/grafana.ini', 'grafana/grafana.ini'),
                                (common / 'loki/loki.yaml', 'loki/loki.yaml')]:
        dest = output / destination
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    if network:
        dest = output / 'prometheus/rules/network.yml'
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(network / 'prometheus/alerts.yml', dest)
    write_json(output / 'environment.json', env)
    (output / 'README.txt').write_text(
        ('TEMPLATE PREVIEW ONLY — NOT READY FOR DEPLOYMENT\n' if allow_template else '') +
        'Generated configuration only; no services deployed. See docs/observability.md.\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allow-template', action='store_true', help='preview unfinished templates only')
    args = parser.parse_args()
    try:
        render(args.environment, args.output, args.allow_template)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f'Error: {exc}\n')
    print(f'Generated: {args.output}')


if __name__ == '__main__':
    main()
