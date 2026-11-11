#!/usr/bin/env python3
"""Config-driven OCI container lifecycle; Docker now, nerdctl adapter for containerd."""
import argparse
import configparser
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request

from diagnostics import record_event
from render_observability import config_dir, render, write_json, ROOT

LABEL = 'io.llm-benchmark.project'
ADAPTERS = {'nvidia': 'gpu/nvidia/model-agnostic', 'hygon': 'gpu/hygon/model-agnostic-hcu-v3'}


def command(args, capture=False, check=True):
    print('+ ' + ' '.join(str(a) for a in args), flush=True)
    return subprocess.run([str(a) for a in args], check=check, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def runtime_command(cfg, args):
    """Apply the same pull policy to lifecycle, suite and search containers."""
    args = list(args)
    if cfg.get('offline', False):
        if args[0] in ('build', 'pull') or args[:2] in (['image', 'build'], ['image', 'pull']):
            raise ValueError('Offline mode forbids image builds and pulls; import prepared images first')
        if args[0] in ('run', 'create'):
            args.insert(1, '--pull=never')
    return cfg['runtime'] + args


def inference_environment(cfg):
    """Forward a named host variable without embedding its value in argv/config."""
    name = cfg.get('inference', {}).get('api_key_env')
    if not name:
        return []
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
        raise ValueError('inference.api_key_env must be an environment variable name')
    value = os.environ.get(name, '')
    if not value.strip():
        raise ValueError('Required inference token environment variable is unset or empty: ' + name)
    if value.lower().startswith('bearer ') or any(c.isspace() for c in value):
        raise ValueError('Inference token must contain only the token, without Bearer prefix or whitespace')
    return ['-e', name]


def aisbench_environment(cfg):
    auth = inference_environment(cfg)
    if auth:
        extension = ROOT / 'containers/aisbench/llm_bench_auth.py'
        auth += ['-e', 'PYTHONPATH=/config', '--mount',
                 f'type=bind,src={extension},dst=/config/llm_bench_auth.py,readonly']
    if not cfg.get('offline', False):
        return auth
    return auth + [arg for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_DATASETS_OFFLINE',
                           'HF_HUB_DISABLE_TELEMETRY') for arg in ('-e', key + '=1')]


def check_local_images(cfg, images, call):
    """Inspect all selected images without registry access; aggregate every problem."""
    missing, mismatched, errors = [], [], []
    for image, roles in images.items():
        result = call('image', 'inspect', image, capture=True, check=False)
        label = image + ' [' + ', '.join(roles) + ']'
        if result.returncode:
            detail = (result.stderr or result.stdout or '').strip()
            if 'no such' in detail.lower() or 'not found' in detail.lower():
                missing.append(label)
            else:
                errors.append(label + ': ' + detail)
            continue
        info = json.loads(result.stdout)[0]
        actual = info.get('Os', '') + '/' + info.get('Architecture', '')
        if actual != cfg['platform']:
            mismatched.append(label + ': ' + actual + ' (expected ' + cfg['platform'] + ')')
    sections = []
    for title, entries in [('Missing images', missing), ('Image architecture mismatch', mismatched),
                           ('Image inspection errors', errors)]:
        if entries:
            sections.append(f'{title} ({len(entries)}):\n' + '\n'.join('  - ' + s for s in entries))
    if sections:
        raise ValueError('Local image preflight failed; no new containers or network created.\n' +
                         '\n'.join(sections) + '\nImport matching images into the configured runtime/namespace and retry.')
    print(f'Local image preflight passed: {len(images)} images ({cfg["platform"]})', flush=True)


def load_config(path):
    return validate_config(json.loads(path.read_text()))


def validate_config(cfg):
    if cfg.get('schema_version') != 1:
        raise ValueError('schema_version must be 1')
    if type(cfg.get('offline', False)) is not bool:
        raise ValueError('offline must be a JSON boolean (true/false)')
    if not re.fullmatch('[a-z][a-z0-9-]{0,39}', cfg['project']):
        raise ValueError('project must be a short lowercase container name')
    for key in ('root', 'logs', 'tokenizer'):
        if not cfg['storage'].get(key):
            raise ValueError(f'storage.{key} is required (AISBench summary requires a tokenizer)')
        p = Path(cfg['storage'][key]).expanduser()
        cfg['storage'][key] = str((ROOT / p).resolve() if not p.is_absolute() else p.resolve())
    if cfg['storage']['root'] == cfg['storage']['logs']:
        raise ValueError('storage.root and storage.logs must differ')
    cfg.setdefault('runtime', ['docker'])
    latency = cfg.get('simulation_latency')
    if latency is not None:
        if not cfg.get('simulation') or not isinstance(latency, dict) or set(latency) != {'capacity', 'overload_delay_seconds'}:
            raise ValueError('simulation_latency is only for simulation and requires capacity/overload_delay_seconds')
        if type(latency['capacity']) is not int or latency['capacity'] < 1:
            raise ValueError('Simulation capacity must be a positive integer')
        delay = latency['overload_delay_seconds']
        if type(delay) not in (int, float) or not math.isfinite(delay) or delay <= 0:
            raise ValueError('Simulation overload delay must be finite and positive')
    if cfg.get('platform') not in ('linux/amd64', 'linux/arm64'):
        raise ValueError('platform must explicitly be linux/amd64 or linux/arm64')
    if not cfg.get('simulation') and cfg['platform'] != 'linux/amd64':
        raise ValueError('Production GPU servers in this project require linux/amd64')
    if not isinstance(cfg['runtime'], list) or not cfg['runtime']:
        raise ValueError('runtime must be a command array, e.g. ["docker"]')
    if cfg['topology']['kind'] not in ('single', 'distributed', 'pd'):
        raise ValueError('topology.kind must be single/distributed/pd')
    if cfg.get('host_metrics') == 'host' and not cfg.get('node_exporter_target'):
        raise ValueError('host_metrics=host requires node_exporter_target=<host-address>:9100')
    if not cfg['topology']['metrics_endpoints']:
        raise ValueError('At least one inference metrics endpoint is required')
    nodes = cfg['gpu']['nodes']
    if not nodes or len({n['name'] for n in nodes}) != len(nodes):
        raise ValueError('GPU nodes must have unique names')
    if cfg['gpu']['deployment'] not in ('local', 'external'):
        raise ValueError('GPU deployment must be local/external')
    if cfg['gpu']['deployment'] == 'local' and len(nodes) != 1:
        raise ValueError('Local exporter manages one host; distributed hosts require external exporters')
    if cfg['gpu']['deployment'] == 'external' and any(not n.get('target') for n in nodes):
        raise ValueError('Each external exporter requires an independent target')
    if not cfg.get('simulation') and 'REPLACE_' in json.dumps(cfg):
        raise ValueError('Fill all REPLACE_ placeholders first')
    auth_name = cfg['inference'].get('api_key_env')
    if auth_name is not None and (not isinstance(auth_name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', auth_name)):
        raise ValueError('inference.api_key_env must be an environment variable name')
    if 'api_key' in cfg['inference']:
        raise ValueError('Use inference.api_key_env instead of storing api_key in JSON')
    bench = cfg['benchmark']
    if cfg['inference'].get('api', 'completions') not in ('completions', 'chat'):
        raise ValueError('inference.api must be completions/chat')
    if not cfg['inference']['base_url'].startswith(('http://', 'https://')):
        raise ValueError('inference.base_url must be an HTTP(S) service root URL')
    for key in ('input_length', 'output_length', 'request_count', 'concurrency'):
        if type(bench[key]) is not int or bench[key] < 1:
            raise ValueError(f'benchmark.{key} must be a positive integer')
    if bench['dataset_type'] not in ('string', 'tokenid'):
        raise ValueError('dataset_type must be string/tokenid')
    tokenizer = Path(cfg['storage']['tokenizer'])
    if not (tokenizer / 'tokenizer_config.json').is_file():
        raise ValueError('storage.tokenizer requires a local tokenizer_config.json; used by synthetic generation/results')
    if type(bench.get('retry', 1)) is not int or bench.get('retry', 1) < 1:
        raise ValueError('benchmark.retry is the total attempt count in this AISBench version; it must be >= 1')
    if bench['mode'] not in ('perf', 'pressure'):
        raise ValueError('benchmark.mode must be perf/pressure')
    if bench.get('request_rate', 0) < 0 or bench.get('warmups', 1) < 0:
        raise ValueError('request_rate and warmups must be nonnegative')
    if bench['mode'] == 'pressure' and bench.get('pressure_seconds', 0) < 1:
        raise ValueError('pressure_seconds must be positive')
    if not 0 <= bench.get('prefix_length', 0) <= bench['input_length']:
        raise ValueError('prefix_length must be within input_length')
    if bench.get('concurrency_levels') is not None:
        levels = bench['concurrency_levels']
        counts = bench.get('requests_per_level', [])
        if bench['mode'] != 'perf' or not isinstance(levels, list) or not 2 <= len(levels) <= 200:
            raise ValueError('concurrency_levels requires perf mode and 2..200 levels')
        if any(type(n) is not int or n < 1 for n in levels + counts):
            raise ValueError('Sweep levels/request counts must be positive integers')
        if len(set(levels)) != len(levels) or len(counts) != len(levels):
            raise ValueError('Sweep levels must be unique and paired with requests_per_level')
        if any(n > bench['request_count'] for n in counts):
            raise ValueError('request_count must cover the largest requests_per_level')
    elif 'requests_per_level' in bench:
        raise ValueError('requests_per_level requires concurrency_levels')
    for item in cfg['topology']['metrics_endpoints']:
        if item['role'] not in ('master', 'prefill', 'decode', 'router', 'worker'):
            raise ValueError('Unsupported inference endpoint role')
    return cfg


def detect_vendor(cfg):
    vendor = cfg['gpu']['vendor']
    if vendor == 'auto':
        if cfg.get('simulation'):
            raise ValueError('Simulation must explicitly select nvidia or hygon')
        found = []
        for name, executable in [('nvidia', 'nvidia-smi'), ('hygon', 'hy-smi')]:
            if shutil.which(executable):
                r = subprocess.run([executable], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
                if r.returncode == 0 and r.stdout.strip():
                    found.append(name)
        if len(found) != 1:
            raise ValueError('Cannot uniquely detect local GPU vendor; set gpu.vendor explicitly for remote/mixed hosts')
        vendor = found[0]
    if vendor not in ADAPTERS:
        raise ValueError(f'Unsupported GPU vendor: {vendor}')
    return vendor


def synthetic_config(b):
    ds_type = b['dataset_type']
    synthetic = {'Type': ds_type, 'RequestCount': b['request_count'], 'TrustRemoteCode': False}
    if ds_type == 'string':
        synthetic['StringConfig'] = {key: {'Method': 'uniform', 'Params': {'MinValue': b[value], 'MaxValue': b[value]}}
                                     for key, value in [('Input', 'input_length'), ('Output', 'output_length')]}
    else:
        synthetic['TokenIdConfig'] = {'RequestSize': b['input_length'], 'PrefixLen': b.get('prefix_length', 0)}
    return (f'synthetic.synthetic_gen_{ds_type} import synthetic_datasets',
            f'datasets[0]["config"] = {synthetic!r}')


def ais_config(cfg, dataset=None):
    b = cfg['benchmark']
    endpoint = cfg['inference']
    model_file = 'vllm_api_stream_chat' if endpoint.get('api', 'completions') == 'chat' else 'vllm_api_general_stream'
    extension = ''
    if dataset is None:
        dataset_import, settings = synthetic_config(b)
    elif dataset.type == 'sharegpt':
        dataset_import = 'sharegpt.sharegpt_gen import sharegpt_datasets'
        model_file = 'vllm_api_stream_chat_multiturn'
        settings = (f'datasets[0]["path"] = {dataset.container_path!r}\n'
                    f'datasets[0]["reader_cfg"]["test_range"] = "[:{dataset.records}]"')
    else:
        dataset_import = 'custom.custom_qa_gen import custom_datasets'
        extension = 'from llm_bench_jsonl import BusinessJsonlDataset, LiteralPrompt\n'
        settings = f'''datasets[0].update(type=BusinessJsonlDataset, abbr="jsonl", path={dataset.container_path!r},
                   prompt_field={dataset.prompt_field!r}, records={dataset.records})
datasets[0]["reader_cfg"] = dict(input_columns=["question"], output_column=None)
datasets[0]["infer_cfg"]["prompt_template"] = dict(type=LiteralPrompt, template="{{question}}")'''
    model = {'abbr': 'aisbench-target', 'model': endpoint['model'], 'url': endpoint['base_url'],
             'path': '/tokenizer', 'stream': True,
             'batch_size': b['concurrency'], 'request_rate': b.get('request_rate', 0),
             'max_out_len': b['output_length'], 'retry': b.get('retry', 1),
             'generation_kwargs': b.get('generation_kwargs', {'temperature': 0, 'ignore_eos': True})}
    auth = ''
    if endpoint.get('api_key_env'):
        backend = 'BearerVLLMAPIChat' if 'chat' in model_file else 'BearerVLLMAPI'
        auth = (f"from llm_bench_auth import {backend}\n"
                f"models[0].update(type={backend}, api_key_env={endpoint['api_key_env']!r})\n")
    return f'''# Generated from stack JSON. The backend class name is AISBench's upstream name.
from mmengine.config import read_base
with read_base():
    from ais_bench.benchmark.configs.summarizers.perf.default_perf import summarizer
    from ais_bench.benchmark.configs.datasets.{dataset_import} as datasets
    from ais_bench.benchmark.configs.models.vllm_api.{model_file} import models
{extension}models[0].update({model!r})
{auth}{settings}
work_dir = "/results"
'''


def prepare(cfg):
    vendor = detect_vendor(cfg)
    root = Path(cfg['storage']['root'])
    source_hash = hashlib.sha256(Path(__file__).read_bytes() + (ROOT/'scripts/render_observability.py').read_bytes())
    # Suite files and local corpora do not affect the rendered monitoring stack.
    asset_dirs = [ROOT/'configs/common', ROOT/'configs/gpu']
    if cfg.get('network'):
        asset_dirs.append(config_dir(cfg['network']['adapter']))
    for directory in asset_dirs:
        for asset in sorted(directory.rglob('*')):
            if asset.is_file():
                source_hash.update(asset.read_bytes())
    config_hash = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode() + source_hash.digest()).hexdigest()
    run_dir = root / 'config' / config_hash[:12]
    if (run_dir / 'manifest.json').exists():
        manifest = json.loads((run_dir / 'manifest.json').read_text())
        if manifest['vendor'] != vendor:
            raise ValueError('Detected vendor changed: use a new storage.root or explicit gpu.vendor')
        return run_dir, vendor
    if run_dir.exists():
        raise ValueError(f'Incomplete generated directory: {run_dir}; inspect and remove it before retry')
    root.mkdir(parents=True, exist_ok=True)
    Path(cfg['storage']['logs']).mkdir(parents=True, exist_ok=True)
    for name in ('prometheus', 'loki', 'grafana', 'alloy', 'logrotate', 'results'):
        (root / 'data' / name).mkdir(parents=True, exist_ok=True)
    targets = cfg['topology']['metrics_endpoints']
    gpu_nodes = cfg['gpu']['nodes']
    logs = cfg.get('log_files', ['server.log'])
    if any(not re.fullmatch(r'[A-Za-z0-9_./-]+', x) or '..' in Path(x).parts or x.startswith('/') for x in logs):
        raise ValueError('log_files must be safe relative paths under storage.logs')
    # Legacy renderer supplies common assets; the new topology interface replaces scrape/log targets below.
    env = {'name': cfg['project'], 'status': 'reference', 'gpu_profile': ADAPTERS[vendor],
           'nodes': [{'name': n['name'], 'gpus': n['gpus'], 'gpu_target':
                      n.get('target', 'gpu-exporter:9400' if vendor == 'nvidia' else 'gpu-exporter:16080')}
                     for n in gpu_nodes],
           'inference': {'engine': 'sglang', 'deployment': cfg['project'], 'group': cfg['project'],
                         'model': cfg['inference']['model'], 'tp': cfg['topology'].get('tp', 1),
                         'pp': cfg['topology'].get('pp', 1), 'metrics_target': targets[0]['target'],
                         'log_files': [f'/logs/node-{i}.log' for i in range(len(gpu_nodes))]},
           'network': cfg.get('network'),
           'services': {'prometheus_url': 'http://prometheus:9090', 'loki_url': 'http://loki:3100'}}
    envfile = root / f'.environment-{config_hash[:12]}.json'
    write_json(envfile, env)
    render(envfile, run_dir)
    envfile.unlink()
    prom = json.loads((run_dir / 'prometheus/prometheus.yml').read_text())
    application = prom['scrape_configs'][0]
    application['static_configs'] = []
    for endpoint in targets:
        application['static_configs'].append({'targets': [endpoint['target']], 'labels': {
            'deployment': cfg['project'], 'inference_group': endpoint.get('group', cfg['project']),
            'role': endpoint['role'], 'environment': cfg['project']}})
    gpu = prom['scrape_configs'][1]
    if cfg.get('simulation'):
        gpu['metrics_path'] = '/gpu-metrics'
        gpu['static_configs'] = [{'targets': ['fixture:18000'], 'labels': {'node': 'simulated', 'simulation': 'true'}}]
    node_target = cfg.get('node_exporter_target', 'node-exporter:9100')
    prom['scrape_configs'].append({'job_name': 'node-exporter', 'static_configs': [{'targets': [node_target]} ]})
    write_json(run_dir / 'prometheus/prometheus.yml', prom)
    # Logs are independent of TP/PP and node count: allow per-role/subdirectory paths for future PD deployments.
    entries = []
    for filename in logs:
        entries.append({'__path__': '/logs/'+filename, 'job': 'inference', 'service_name': 'inference',
                        'deployment': cfg['project'], 'environment': cfg['project'], 'source_file': filename,
                        'simulation': str(bool(cfg.get('simulation'))).lower()})
    target_text = '[\n'+',\n'.join('{' + ', '.join(json.dumps(k)+' = '+json.dumps(v) for k,v in t.items()) + '}' for t in entries)+',\n]'
    template = (ROOT/'configs/common/alloy/config.alloy.tmpl').read_text()
    (run_dir/'alloy/config.alloy').write_text(template.replace('@@LOG_TARGETS@@', target_text).replace('@@LOKI_PUSH_URL@@', 'http://loki:3100/loki/api/v1/push'))
    template = (ROOT/'configs/common/logrotate/inference.conf.tmpl').read_text()
    (run_dir/'logrotate/inference.conf').write_text(template.replace('@@LOG_FILES@@', ' '.join('/logs/'+p for p in logs)))
    with (run_dir/'loki/loki.yaml').open('a') as f:
        f.write('\ningester:\n  wal:\n    dir: /var/lib/loki/wal\ncompactor:\n  working_directory: /var/lib/loki/compactor\n')
    (run_dir/'aisbench.py').write_text(ais_config(cfg))
    if cfg.get('offline', False):
        ini = configparser.ConfigParser(interpolation=None)
        for source in (run_dir/'grafana/grafana.ini', ROOT/'configs/common/grafana/offline.ini'):
            with source.open() as stream:
                ini.read_file(stream)
        with (run_dir/'grafana/grafana.ini').open('w') as stream:
            ini.write(stream)
    write_json(run_dir/'manifest.json', {'vendor': vendor, 'config_hash': config_hash, 'simulation': cfg.get('simulation', False), 'stack': cfg})
    return run_dir, vendor


class Stack:
    def __init__(self, cfg, config_dir, vendor):
        self.cfg, self.directory, self.vendor = cfg, config_dir, vendor
        self.runtime = cfg['runtime']; self.project = cfg['project']
        self.network = self.project+'-net'; self.root = Path(cfg['storage']['root'])
        self.statefile = self.root/'state.json'; self.created = []
        self.uid = f'{os.getuid()}:{os.getgid()}'

    def call(self, *args, **kwargs):
        argv = runtime_command(self.cfg, args)
        if args[0] in ('run', 'create', 'network') and not kwargs.get('capture'):
            result = command(argv, capture=True, check=False)
            if result.stdout:
                print(result.stdout, end='', flush=True)
            if result.stderr:
                print(result.stderr, end='', file=sys.stderr, flush=True)
            if args[0] == 'run' and '--rm' in args:
                record_event(self.root, 'readiness', exit_code=result.returncode,
                             output=(result.stdout + result.stderr)[-8192:])
            if kwargs.get('check', True):
                result.check_returncode()
            return result
        return command(argv, **kwargs)

    def inspect(self, name):
        result = self.call('inspect', name, capture=True, check=False)
        if result.returncode:
            return None
        return json.loads(result.stdout)[0]

    def build_one(self, name):
        if self.cfg.get('offline', False):
            raise ValueError('Offline mode forbids image builds and source downloads; import prepared images first')
        if name == 'aisbench':
            context = self.root / 'build' / 'aisbench'
            context.mkdir(parents=True, exist_ok=True)
            archive = context / 'source.tar.gz'
            if not archive.exists():
                with urllib.request.urlopen('https://codeload.github.com/AISBench/benchmark/tar.gz/e330dc134c33f47671926144d9c143b554bc71cc', timeout=120) as response:
                    content = response.read()
                archive.write_bytes(content)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != '87a5434c8fa8b78aa57b588f00fbfb1861435d602496f6473b4a9dece94647c2':
                raise ValueError('AISBench source archive checksum mismatch')
            shutil.copyfile(ROOT/'containers/aisbench/Dockerfile', context/'Dockerfile')
            self.call('build', '--network=host', '--platform', self.cfg['platform'], '-t', self.cfg['images'][name], str(context))
        else:
            self.call('build', '--network=host', '--platform', self.cfg['platform'], '-t', self.cfg['images'][name], '-f', str(ROOT/'containers/toolbox/Dockerfile'), str(ROOT))

    def check_image(self, image):
        result = self.call('image', 'inspect', image, capture=True, check=False)
        if result.returncode == 0:
            info = json.loads(result.stdout)[0]
            actual = info.get('Os', '') + '/' + info.get('Architecture', '')
            if actual != self.cfg['platform']:
                raise ValueError(f'Image architecture mismatch: {image} is {actual}, expected {self.cfg["platform"]}; rebuild/pull for the configured platform')

    def build(self):
        for name in ('toolbox', 'aisbench'):
            self.build_one(name)

    def preflight_images(self, run_benchmark=True):
        images = {}
        for spec in self.specs():
            images.setdefault(spec['image'], []).append(spec['name'])
        if run_benchmark:
            images.setdefault(self.cfg['images']['aisbench'], []).append('aisbench')
        check_local_images(self.cfg, images, self.call)

    def specs(self):
        images = self.cfg['images']; config = self.directory; data = self.root/'data'
        def mount(src, dst, ro=False):
            return ['--mount', f'type=bind,src={src},dst={dst}'+(',readonly' if ro else '')]
        def service(name, args, cmd, ready=None):
            return {'name': name, 'args': args, 'image': images[name], 'command': cmd, 'ready': ready}
        log_mount = mount(self.cfg['storage']['logs'], '/logs')
        user = ['--user', self.uid]
        specs = []
        if self.cfg.get('simulation'):
            specs.append({'name': 'fixture', 'args': log_mount+['--entrypoint','python','-e','MOCK_GPU_VENDOR='+self.vendor],
                          'image': images['toolbox'], 'command': ['/opt/benchmark/mock_server.py'],
                          'ready': ('http://fixture:18000/v1/models', 'mock-model')})
            if self.cfg.get('simulation_latency'):
                latency = self.cfg['simulation_latency']
                specs[-1]['args'] += ['-e', 'MOCK_CAPACITY='+str(latency['capacity']),
                                     '-e', 'MOCK_OVERLOAD_DELAY_SECONDS='+str(latency['overload_delay_seconds'])]
        if self.cfg['gpu']['deployment'] == 'local' and not self.cfg.get('simulation'):
            if self.vendor == 'nvidia':
                args = ['--runtime=nvidia', '-e', 'NVIDIA_VISIBLE_DEVICES=all',
                        '-e', 'NVIDIA_DRIVER_CAPABILITIES=compute,utility',
                        '--cap-add', 'SYS_ADMIN', '-e', 'DCGM_EXPORTER_KUBERNETES=false']
                image = images['nvidia_exporter']; port = 9400; marker = 'DCGM_FI_DEV_GPU_UTIL'
            else:
                args = ['--privileged','-e','CONNECT_K8S=false','-e','METRICS_LEVEL=low']
                for path in ('/dev','/opt/hyhal','/etc/hostname','/etc/vdev'):
                    args += mount(path,path,path!='/dev')
                image = images['hygon_exporter']; port=16080; marker='hcu_memorycap_bytes'
            specs.append({'name':'gpu-exporter','args':args,'image':image,'command':[],
                          'ready':(f'http://gpu-exporter:{port}/metrics',marker)})
        host_args, host_cmd = [], []
        if self.cfg.get('host_metrics', 'container') == 'host':
            if self.cfg.get('simulation'):
                raise ValueError('Mac simulation cannot collect macOS host metrics with Linux node_exporter')
            host_args = ['--pid','host']+mount('/proc', '/host/proc', True)+mount('/sys','/host/sys',True)+mount('/','/host/root',True)
            host_cmd = ['--path.procfs=/host/proc','--path.sysfs=/host/sys','--path.rootfs=/host/root']
        node_target = self.cfg.get('node_exporter_target', 'node-exporter:9100')
        specs.append(service('node-exporter', host_args, host_cmd, ('http://'+node_target+'/metrics','node_exporter_build_info')))
        specs[-1]['host_network'] = self.cfg.get('host_metrics') == 'host'
        specs.append(service('loki', user+mount(config/'loki/loki.yaml','/etc/loki/config.yaml',True)+mount(data/'loki','/var/lib/loki'),
                             ['-config.file=/etc/loki/config.yaml'], ('http://loki:3100/ready','ready')))
        specs.append({'name':'logrotate','image':images['toolbox'],'args':log_mount+mount(config/'logrotate/inference.conf','/config/inference.conf',True)+mount(data/'logrotate','/state'),
                      'command':['rotate','--interval','60'],'ready':None})
        specs.append(service('alloy', ['--user','0:0']+mount(self.cfg['storage']['logs'],'/logs',True)+mount(config/'alloy/config.alloy','/etc/alloy/config.alloy',True)+mount(data/'alloy','/var/lib/alloy'),
                             ['run']+(['--disable-reporting'] if self.cfg.get('offline', False) else [])+
                             ['--server.http.listen-addr=0.0.0.0:12345','--storage.path=/var/lib/alloy','/etc/alloy/config.alloy'], ('http://alloy:12345/-/ready','')))
        specs.append(service('prometheus',user+mount(config/'prometheus','/etc/prometheus',True)+mount(data/'prometheus','/prometheus'),
                             ['--config.file=/etc/prometheus/prometheus.yml','--storage.tsdb.path=/prometheus','--storage.tsdb.retention.time='+self.cfg.get('metrics_retention','7d')],('http://prometheus:9090/-/ready','')))
        specs.append(service('grafana',user+mount(config/'grafana/grafana.ini','/etc/grafana/grafana.ini',True)+mount(config/'grafana/provisioning','/etc/grafana/provisioning',True)+mount(config/'grafana/dashboards','/etc/grafana/dashboards',True)+mount(data/'grafana','/var/lib/grafana'),
                             [],('http://grafana:3000/api/health','ok')))
        for spec in specs:
            port = self.cfg.get('ports',{}).get(spec['name'])
            if port:
                internal = {'grafana':3000,'prometheus':9090,'loki':3100}[spec['name']]
                spec['args'] += ['-p', f'127.0.0.1:{port}:{internal}']
        return specs

    def probe(self, url, contains='', *, inference=False):
        auth = inference_environment(self.cfg) if inference else []
        options = ['--api-key-env', self.cfg['inference']['api_key_env']] if auth else []
        self.call('run','--rm',*auth,'--platform',self.cfg['platform'],'--network',self.network,self.cfg['images']['toolbox'],
                  'probe',url,'--timeout',str(self.cfg.get('startup_timeout',120)),'--contains',contains,*options)

    def up(self, run_benchmark=True, *, needs_client=False):
        needs_client = run_benchmark or needs_client
        if self.statefile.exists():
            raise ValueError('Stack state exists; use status/down before starting a new run')
        inference_environment(self.cfg)
        self.call('info',capture=True)
        if self.cfg.get('offline', False):
            self.preflight_images(needs_client)
        else:
            for name in (('toolbox', 'aisbench') if needs_client else ('toolbox',)):
                if self.call('image', 'inspect', self.cfg['images'][name], capture=True, check=False).returncode:
                    self.build_one(name)
        if not self.cfg.get('simulation') and self.cfg['gpu']['deployment']=='local' and platform.system()!='Linux':
            raise ValueError('Real local GPU exporters require Linux; use simulation on macOS')
        for name in (('toolbox', 'aisbench') if needs_client else ('toolbox',)):
            self.check_image(self.cfg['images'][name])
        if self.inspect(self.network):
            raise ValueError('Name collision; choose a different project')
        # Refuse to adopt any existing network/container, including resources from another run.
        existing = self.call('network','inspect',self.network,capture=True,check=False)
        if existing.returncode == 0:
            raise ValueError('Network already exists; choose another project or clean up the owned stack')
        self.call('network','create','--label',f'{LABEL}={self.project}',self.network)
        self.save('starting')
        try:
            for spec in self.specs():
                name = self.project+'-'+spec['name']
                if self.inspect(name):
                    raise ValueError(f'Container already exists: {name}')
                self.check_image(spec['image'])
                network_args = ['--network','host'] if spec.get('host_network') else ['--network',self.network,'--network-alias',spec['name']]
                # A failed docker run can still leave a created container (e.g. port conflict).
                self.created.append(name); self.save('starting')
                self.call('run','-d','--platform',self.cfg['platform'],'--name',name,'--label',f'{LABEL}={self.project}',
                          *network_args,
                          *spec['args'],spec['image'],*spec['command'])
                if spec['ready']:
                    self.probe(*spec['ready'])
                info=self.inspect(name)
                if not info or not info['State']['Running']:
                    raise ValueError(f'{name} exited during startup')
                print('READY '+spec['name'],flush=True)
            # Require all exporter series before sending load; never silently fall back to another vendor.
            expected = len(self.cfg['topology']['metrics_endpoints']) + (1 if self.cfg.get('simulation') else len(self.cfg['gpu']['nodes'])) + 1
            self.call('run', '--rm', '--platform', self.cfg['platform'], '--network', self.network, self.cfg['images']['toolbox'],
                      'targets', 'http://prometheus:9090/api/v1/targets', '--minimum', str(expected),
                      '--timeout', str(self.cfg.get('startup_timeout', 120)))
            for node in self.cfg['gpu']['nodes']:
                if self.cfg['gpu']['deployment']=='external' and not self.cfg.get('simulation'):
                    self.probe('http://'+node['target']+'/metrics', 'DCGM_FI_DEV_GPU_UTIL' if self.vendor=='nvidia' else 'hcu_memorycap_bytes')
            self.probe(self.cfg['inference']['base_url'].rstrip('/')+'/v1/models', 'data', inference=True)
            self.save('ready')
            if run_benchmark:
                self.bench()
        except BaseException:
            self.save('failed')
            print('Startup/benchmark failed; containers and logs retained for diagnosis. Use down to remove owned containers.',file=sys.stderr)
            raise

    def save(self, status):
        record_event(self.root, 'stack', status=status, component=self.created[-1] if self.created else None)
        write_json(self.statefile,{'project':self.project,'network':self.network,'containers':self.created,
                                 'config_dir':str(self.directory),'status':status,'updated_at':datetime.now(timezone.utc).isoformat()})

    def bench(self):
        b=self.cfg['benchmark']; name=self.project+'-aisbench'
        results = self.root/'data/results'
        previous_runs = set(results.iterdir())
        if self.inspect(name):
            raise ValueError('Previous AISBench container exists; use down before a new one-click run')
        args=['create',*aisbench_environment(self.cfg),'--platform',self.cfg['platform'],'--name',name,'--label',f'{LABEL}={self.project}','--network',self.network,
              '--mount',f'type=bind,src={self.directory}/aisbench.py,dst=/config/aisbench.py,readonly',
              '--mount',f'type=bind,src={self.root}/data/results,dst=/results']
        if self.cfg['storage'].get('tokenizer'):
            args += ['--mount',f'type=bind,src={Path(self.cfg["storage"]["tokenizer"]).resolve()},dst=/tokenizer,readonly']
        args += [self.cfg['images']['aisbench'],'/config/aisbench.py','--mode','perf',
                 '--max-num-workers','1','--num-warmups',str(b.get('warmups',1))]
        if b.get('concurrency_levels'):
            # Normal runner applies upstream's inter-case cooldown; debug runner does not.
            args += ['--batch-size', *map(str, b['concurrency_levels']),
                     '--num-prompts', *map(str, b['requests_per_level'])]
        else:
            args += ['--debug']
        if b['mode']=='pressure':
            args += ['--pressure','--pressure-time',str(b['pressure_seconds'])]
        self.call(*args)
        self.created.append(name);self.save('benchmark-running')
        result=self.call('start','-a',name,check=False)
        info=self.inspect(name)
        if result.returncode or not info or info['State']['ExitCode']:
            self.save('benchmark-failed');raise RuntimeError('AISBench failed; inspect runtime logs and results')
        runs = [p for p in results.iterdir() if p not in previous_runs and p.is_dir()]
        reports = [p for run in runs for p in run.glob('performances/*/synthetic.json')]
        summaries = [json.loads(p.read_text()) for p in reports]
        expected_reports = len(b.get('concurrency_levels', [b['concurrency']]))
        if len(summaries) != expected_reports or any(
            float(r['Failed Requests']['total']) != 0 or float(r['Success Requests']['total']) <= 0 for r in summaries
        ):
            raise RuntimeError('AISBench reports missing or contain failed requests')
        expected_count = sum(b['requests_per_level']) if b.get('concurrency_levels') else b['request_count']
        if b['mode'] == 'perf' and sum(float(r['Success Requests']['total']) for r in summaries) != expected_count:
            raise RuntimeError('AISBench completed request count differs from configured workload')
        self.save('complete')

    def down(self):
        if (self.root/'suite.lock').exists():
            raise ValueError('Suite lock exists; wait for the suite to finish before down')
        state=json.loads(self.statefile.read_text())
        for name in reversed(state['containers']):
            info=self.inspect(name)
            if info:
                if info.get('Config',{}).get('Labels',{}).get(LABEL)!=self.project:
                    raise ValueError(f'Refuse to remove unowned container: {name}')
                self.call('rm','-f',name)
        r=self.call('network','inspect',state['network'],capture=True,check=False)
        if r.returncode==0:
            if json.loads(r.stdout)[0].get('Labels',{}).get(LABEL)!=self.project:
                raise ValueError('Refuse to remove unowned network')
            self.call('network','rm',state['network'])
        self.statefile.unlink()
        print('Removed owned containers/network; persistent data retained')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['plan','check-images','build','up','status','down'])
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--no-benchmark',action='store_true',help='start observability only')
    args=p.parse_args()
    try:
        cfg=load_config(args.config)
        if args.action in ('down','status'):
            state=json.loads((Path(cfg['storage']['root'])/'state.json').read_text())
            if state['project'] != cfg['project']:
                raise ValueError('State project does not match config')
            directory=Path(state['config_dir']);vendor=json.loads((directory/'manifest.json').read_text())['vendor']
        else:
            directory,vendor=prepare(cfg)
        stack=Stack(cfg,directory,vendor)
        if args.action=='plan':
            print(json.dumps({'gpu_vendor':vendor,'simulation':cfg.get('simulation',False),'config_dir':str(directory),'services':stack.specs(),'final_client':'AISBench'},ensure_ascii=False,indent=2))
        elif args.action=='build':stack.build()
        elif args.action=='check-images':
            stack.call('info',capture=True)
            stack.preflight_images(not args.no_benchmark)
        elif args.action=='up':stack.up(not args.no_benchmark)
        elif args.action=='down':stack.down()
        else:
            print(json.dumps(state,indent=2))
            for name in state['containers']:
                info=stack.inspect(name)
                print(name, info['State'] if info else 'missing')
    except (ValueError,KeyError,OSError,subprocess.SubprocessError,RuntimeError) as exc:
        p.exit(1,f'ERROR: {exc}\n')


if __name__=='__main__':
    main()
