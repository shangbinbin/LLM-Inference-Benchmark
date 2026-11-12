"""Canonical task/replica/device labels shared by scrapes, logs and dashboards."""
import re
from pathlib import PurePosixPath
from gpu_metrics import exporter_port


def label_value(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(c in value for c in '`"\\') or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(field + ' must be a nonempty string of at most 128 characters without controls, quotes, backslashes or backticks')
    return value


def task_name(cfg):
    return label_value(cfg.get('task', cfg['project']), 'task')


def log_sources(cfg):
    raw = cfg.get('log_sources')
    if raw is None:
        files = cfg.get('log_files', ['server.log'])
        if not isinstance(files, list) or not files:
            raise ValueError('log_files must be a nonempty array')
        raw = [{'path': path} for path in files]
    if not isinstance(raw, list) or not raw:
        raise ValueError('log_sources must be a nonempty array')
    endpoints = cfg.get('topology', {}).get('metrics_endpoints') or []
    result, seen = [], set()
    for i, source in enumerate(raw):
        if not isinstance(source, dict):
            raise ValueError('log_sources entries must be objects')
        path = source.get('path')
        if (not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_./-]+', path)
                or '..' in PurePosixPath(path).parts or path.startswith('/') or path in seen):
            raise ValueError('Log source paths must be unique safe relative paths without wildcards')
        path = PurePosixPath(path).as_posix()
        if path == '.' or path in seen:
            raise ValueError('Log source paths must identify distinct files')
        seen.add(path)
        endpoint = endpoints[i] if i < len(endpoints) else {}
        role = source.get('role', endpoint.get('role', 'master' if i == 0 else 'worker'))
        replica = source.get('replica', endpoint.get('replica', 'replica-' + str(i)))
        item = {'path': path, 'role': label_value(role, 'log source role'),
                'replica': label_value(replica, 'log source replica')}
        if 'node' in source:
            item['node'] = label_value(source['node'], 'log source node')
        result.append(item)
    return result


def validate_labels(cfg):
    task_name(cfg)
    if cfg['inference'].get('engine', 'auto') not in ('auto', 'vllm', 'sglang'):
        raise ValueError('inference.engine must be auto/vllm/sglang')
    log_sources(cfg)
    for i, endpoint in enumerate(cfg['topology'].get('metrics_endpoints') or []):
        label_value(endpoint.get('role', 'master'), 'metrics endpoint role')
        label_value(endpoint.get('replica', 'replica-' + str(i)), 'metrics endpoint replica')
    for node in cfg['gpu']['nodes']:
        label_value(node['name'], 'GPU node name')
        if 'model' in node:
            label_value(node['model'], 'GPU node model')
    for field in ('model_label', 'index_label'):
        if field in cfg['gpu'] and (not isinstance(cfg['gpu'][field], str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', cfg['gpu'][field])):
            raise ValueError('gpu.' + field + ' must name an exporter metric label')


def application_labels(cfg, endpoint, index):
    role = endpoint.get('role', 'master')
    return {'task': task_name(cfg), 'deployment': cfg['project'], 'environment': cfg['project'],
            'inference_group': endpoint.get('group', task_name(cfg)), 'role': role, 'instance_role': role,
            'replica': endpoint.get('replica', 'replica-' + str(index)),
            'inference_engine': cfg['inference'].get('engine', 'auto')}


def log_labels(cfg, source):
    result = {'__path__': '/logs/' + source['path'], 'task': task_name(cfg),
              'job': 'inference', 'service_name': 'inference',
              'deployment': cfg['project'], 'environment': cfg['project'],
              'instance_role': source['role'], 'role': source['role'], 'replica': source['replica'],
              'source_file': source['path'], 'simulation': str(bool(cfg.get('simulation'))).lower()}
    if source.get('node'):
        result['node'] = source['node']
    return result


def configure_gpu_labels(job, cfg, vendor):
    nodes = cfg['gpu']['nodes']
    if cfg.get('simulation'):
        targets = [('fixture:18000', nodes[0])]
        job['metrics_path'] = '/gpu-metrics'
    else:
        port = exporter_port(cfg, vendor)
        targets = [(n.get('target', 'gpu-exporter:' + str(port)), n) for n in nodes]
    job['static_configs'] = []
    for target, node in targets:
        job['static_configs'].append({'targets': [target], 'labels': {
            'task': task_name(cfg), 'deployment': cfg['project'], 'environment': cfg['project'],
            'service': job['job_name'], 'node': node['name'], 'gpu_node': node['name'],
            'gpu_vendor': vendor, 'gpu_model': node.get('model', 'simulated' if cfg.get('simulation') else ''),
        }})
    rules = []
    model_label = cfg['gpu'].get('model_label', 'modelName' if vendor == 'nvidia' else 'name')
    if model_label:
        rules.append({'source_labels': ['gpu_model', model_label], 'separator': ';', 'regex': ';(.+)',
                      'target_label': 'gpu_model', 'replacement': '$1'})
    rules += [{'source_labels': ['gpu_model'], 'regex': '', 'target_label': 'gpu_model', 'replacement': 'unspecified'},
              {'source_labels': [cfg['gpu'].get('index_label', 'gpu' if vendor == 'nvidia' else 'minor_number')],
               'regex': '(.+)', 'target_label': 'gpu_index', 'replacement': '$1'}]
    if cfg.get('simulation'):
        for target in job['static_configs']:
            target['labels']['simulation'] = 'true'
    job['metric_relabel_configs'] = rules
