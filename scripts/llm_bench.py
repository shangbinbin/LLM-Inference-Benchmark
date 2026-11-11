"""Command-line interface for the observability stack and AISBench workloads."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import re
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from diagnostics import collect, run_diagnosed
from benchmark_stack import ROOT, Stack, prepare, validate_config, published_ports
from run_aisbench_suite import prepare_suite, run_suite
from slo_search import run_search


PROFILES = {
    'linux-single': 'nvidia-single',
    'mac-simulation': 'mac-simulation',
    'mac-offline': 'mac-offline',
    'mac-slo-search': 'mac-slo-search',
}


def read_object(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f'{path}: expected a JSON object')
    return value


def workload_options(parser):
    parser.add_argument('--input-len', type=int, dest='input_length', help='synthetic input length')
    parser.add_argument('--output-len', type=int, dest='output_length', help='requested output length')
    parser.add_argument('--requests', type=int, dest='request_count', help='number of measured requests')
    parser.add_argument('--concurrency', type=int, help='maximum concurrent requests')


def workload(args):
    return {key: getattr(args, key) for key in
            ('input_length', 'output_length', 'request_count', 'concurrency')
            if getattr(args, key, None) is not None}


def configure(parser):
    parser.add_argument('--config', type=Path, help='stack JSON (overlay when used with --profile)')
    parser.add_argument('--profile', choices=PROFILES, help='built-in stack defaults')
    parser.add_argument('--project', help='unique container and network name prefix')
    parser.add_argument('--endpoint', help='inference service root URL')
    parser.add_argument('--model', help='served model identifier')
    parser.add_argument('--host-address', help='node_exporter host (port 9100)')
    parser.add_argument('--gpu-vendor', choices=('auto', 'nvidia', 'hygon'))
    parser.add_argument('--gpus', type=int, help='GPU count for a single-node configuration')
    for name, help_text in [('tokenizer', 'local tokenizer directory'), ('data-dir', 'state, data and reports'),
                            ('log-dir', 'inference service log directory')]:
        parser.add_argument('--' + name, type=Path, help=help_text)
    parser.add_argument('--offline', action='store_true', help='require local images and disable reporting')
    workload_options(parser)


def select_stack(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--data-dir', type=Path, help='data directory of the running stack')
    group.add_argument('--config', type=Path, help='locate a stack by storage.root and project')


def parser_for_cli():
    parser = argparse.ArgumentParser(prog='llm-bench', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name, desc in [('up', 'Start observability and run AISBench'), ('plan', 'Show effective configuration and startup order')]:
        sub = commands.add_parser(name, help=desc)
        configure(sub)
        group = sub.add_mutually_exclusive_group()
        group.add_argument('--cases', type=Path, help='execute this ordered AISBench suite')
        group.add_argument('--no-benchmark', action='store_true', help='start only observability')
    images = commands.add_parser('images', help='Inspect or build local images').add_subparsers(dest='action', required=True)
    check = images.add_parser('check', help='check selected local images and platform')
    configure(check)
    check.add_argument('--no-benchmark', action='store_true', help='omit AISBench image')
    configure(images.add_parser('build', help='build toolbox and AISBench images'))
    for name, desc in [('status', 'Inspect running stack'), ('down', 'Remove owned containers and network')]:
        select_stack(commands.add_parser(name, help=desc))
    logs = commands.add_parser('logs', help='Collect offline diagnostics').add_subparsers(dest='action', required=True)
    bundle = logs.add_parser('collect', help='Write a local diagnostic tar.gz')
    select_stack(bundle)
    bundle.add_argument('--output', type=Path, help='new tar.gz file (default: storage.root/diagnostics/)')
    bundle.add_argument('--run', type=Path, help='collect only this run relative to data/results')
    bundle.add_argument('--extra-log', type=Path, action='append', default=[],
                        help='also include this local external component log (repeatable)')
    bundle.add_argument('--tail', type=int, default=500, help='recent lines per log (1..10000)')
    bench = commands.add_parser('bench', help='Run workloads on an existing stack').add_subparsers(dest='action', required=True)
    single = bench.add_parser('run', help='run one AISBench case')
    select_stack(single)
    workload_options(single)
    suite = bench.add_parser('suite', help='run cases in declared order')
    select_stack(suite)
    suite.add_argument('--cases', type=Path, required=True)
    slo = bench.add_parser('slo', help='search concurrency under latency limits')
    select_stack(slo)
    slo.add_argument('--cases', type=Path, help='search each case in the ordered AISBench suite')
    slo.add_argument('--policy', type=Path, required=True)
    return parser


def overlay(base, changes):
    for key, value in changes.items():
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            overlay(base[key], value)
        else:
            base[key] = value


def configured_stack(args):
    if args.config and not args.profile:
        cfg = read_object(args.config)
    else:
        profile = PROFILES[args.profile or 'linux-single']
        cfg = read_object(ROOT / 'configs/stacks' / (profile + '.json'))
        if args.config:
            overlay(cfg, read_object(args.config))
    if args.project is not None:
        cfg['project'] = args.project
    if args.endpoint is not None:
        url = urlsplit(args.endpoint)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('--endpoint requires an HTTP(S) service root URL')
        cfg['inference']['base_url'] = args.endpoint
        for entry in cfg['topology']['metrics_endpoints']:
            if entry['target'].startswith('REPLACE_INFERENCE_HOST:'):
                entry['target'] = url.netloc if url.port else f'{url.netloc}:{443 if url.scheme == "https" else 80}'
    if args.host_address:
        cfg['node_exporter_target'] = args.host_address + ':' + str(published_ports(cfg)['node-exporter'])
    if args.model is not None:
        cfg['inference']['model'] = args.model
    for path, field in [(args.tokenizer, 'tokenizer'), (args.data_dir, 'root'), (args.log_dir, 'logs')]:
        if path is not None:
            cfg['storage'][field] = str(path.expanduser().resolve())
    if args.gpu_vendor:
        cfg['gpu']['vendor'] = args.gpu_vendor
    if args.gpus is not None:
        if len(cfg['gpu']['nodes']) != 1 or args.gpus < 1:
            raise ValueError('--gpus requires one node and a positive GPU count')
        cfg['gpu']['nodes'][0]['gpus'] = args.gpus
    if args.offline:
        cfg['offline'] = True
    settings = workload(args)
    if 'concurrency' in settings:
        cfg['benchmark'].pop('concurrency_levels', None)
        cfg['benchmark'].pop('requests_per_level', None)
    cfg['benchmark'].update(settings)
    return validate_config(cfg)


def reference(root):
    digest = hashlib.sha256(str(root).encode()).hexdigest()
    return Path.cwd() / '.llm-bench' / (digest + '.json')


def save_reference(path, value):
    path.parent.mkdir(exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, mode='w', delete=False) as tmp:
        json.dump(value, tmp)
    Path(tmp.name).replace(path)


def remember(stack):
    save_reference(reference(stack.root), {'root': str(stack.root), 'project': stack.project})
    try:
        save_reference(stack.root/'diagnostics/last-stack.json', {'config_hash': stack.directory.name})
    except OSError as exc:
        print(f'Could not save diagnostic context: {exc}', file=sys.stderr)


def running_stack(args):
    expected = None
    if args.data_dir is not None:
        root = args.data_dir.expanduser().resolve()
    elif args.config is not None:
        site = read_object(args.config)
        root = (ROOT / Path(site['storage']['root']).expanduser()).resolve()
        expected = site['project']
    else:
        active = []
        for path in sorted((Path.cwd() / '.llm-bench').glob('*.json')):
            ref = read_object(path)
            statefile = Path(ref['root']) / 'state.json'
            if statefile.exists() and read_object(statefile)['project'] == ref['project']:
                active.append(ref)
        if len(active) != 1:
            roots = ', '.join(x['root'] for x in active)
            raise ValueError(f'{len(active)} stacks in this working directory ({roots}). Select one with --data-dir or --config')
        root = Path(active[0]['root'])
        expected = active[0]['project']
    if not (root / 'state.json').is_file():
        raise ValueError(f'No active stack in {root}. Start it with llm-bench up')
    state = read_object(root / 'state.json')
    directory = Path(state['config_dir'])
    manifest = read_object(directory / 'manifest.json')
    cfg = manifest['stack']
    if (cfg['project'] != state['project'] or (expected and expected != state['project'])
            or Path(cfg['storage']['root']).resolve() != root):
        raise ValueError('Selected stack identity or storage path differs from its saved configuration')
    return Stack(cfg, directory, manifest['vendor']), state


def diagnostic_context(args):
    if not (args.data_dir or args.config):
        stack, _ = running_stack(args)
        return stack.cfg, stack.vendor

    site = read_object(args.config) if args.config else None
    root = ((ROOT / Path(site['storage']['root']).expanduser()).resolve() if site
            else args.data_dir.expanduser().resolve())
    if (root/'state.json').exists():
        if site:
            try:
                active = read_object(root/'state.json')
            except (ValueError, OSError):
                active = {}
            if active.get('project', site['project']) != site['project']:
                raise ValueError('Selected data directory belongs to another project')
        try:
            stack, _ = running_stack(args)
        except (ValueError, KeyError, OSError) as exc:
            print(f'Could not read active stack: {exc}', file=sys.stderr)
        else:
            return stack.cfg, stack.vendor
    context = root/'diagnostics/last-stack.json'
    if context.exists():
        key = read_object(context)['config_hash']
        if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{12}', key):
            raise ValueError('Invalid saved diagnostic configuration ID')
        manifest = read_object(root/'config'/key/'manifest.json')
        cfg = manifest['stack']
        if Path(cfg['storage']['root']).resolve() != root or (site and site['project'] != cfg['project']):
            raise ValueError('Diagnostic context does not match the requested stack')
        return cfg, manifest['vendor']
    if site:
        return site, None
    raise ValueError('No saved diagnostic context; use --config with the original site JSON')


@contextmanager
def operation(root):
    # Kernel releases this advisory lock even if a CLI process exits unexpectedly.
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.llm-bench.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f'Another llm-bench operation is active in {root}') from exc
        yield


def execute(args):
    if args.command in ('up', 'plan', 'images'):
        cfg = configured_stack(args)
        suite = None
        if getattr(args, 'cases', None):
            suite = prepare_suite(cfg, read_object(args.cases), args.cases.resolve().parent)
        directory, vendor = prepare(cfg)
        stack = Stack(cfg, directory, vendor)
        if args.command == 'plan':
            print(json.dumps({'config': cfg, 'gpu_vendor': vendor, 'config_dir': str(directory),
                              'services': [s['name'] for s in stack.specs()],
                              'cases': [{'name': case.name, 'dataset': case.dataset_info(),
                                         'benchmark': case.cfg['benchmark']} for case in suite.cases] if suite else [],
                              'workloads': [case.name for case in suite.cases] if suite else
                              ([] if args.no_benchmark else ['default'])}, indent=2, ensure_ascii=False))
        elif args.command == 'images':
            if args.action == 'check':
                def check_images():
                    stack.call('info', capture=True)
                    stack.preflight_images(not args.no_benchmark)
                run_diagnosed(stack, 'images check', check_images)
            else:
                stack.build()
        else:
            with operation(stack.root):
                if stack.statefile.exists():
                    raise ValueError(f'Stack state exists in {stack.root}. Use bench, status or down')
                remember(stack)
                try:
                    run_diagnosed(stack, 'up', stack.up, run_benchmark=not args.no_benchmark and suite is None,
                                  needs_client=suite is not None)
                finally:
                    if not stack.statefile.exists():
                        reference(stack.root).unlink(missing_ok=True)
                if suite:
                    return run_diagnosed(stack, 'up --cases', run_suite, cfg, suite)
        return 0

    if args.command == 'logs':
        cfg, vendor = diagnostic_context(args)
        path = collect(cfg, vendor, output=args.output, lines=args.tail, extra_logs=args.extra_log, selected=args.run)
        print('Diagnostic bundle: ' + str(path))
        return 0

    stack, state = running_stack(args)
    if args.command == 'status':
        print(json.dumps(state, indent=2))
        for name in state['containers']:
            info = stack.inspect(name)
            print(name, info['State']['Status'] if info else 'missing')
        return 0
    with operation(stack.root):
        # Reload after locking so a concurrent lifecycle change cannot select a different stack.
        current, _ = running_stack(args)
        if current.directory != stack.directory or current.root != stack.root:
            raise ValueError('Selected stack changed. Retry with --data-dir')
        if args.command == 'down':
            stack.down()
            reference(stack.root).unlink(missing_ok=True)
        else:
            return run_diagnosed(stack, 'bench ' + args.action, execute_benchmark, stack, args)
    return 0


def execute_benchmark(stack, args):
    if args.action == 'slo':
        suite = prepare_suite(stack.cfg, read_object(args.cases), args.cases.resolve().parent) if args.cases else None
        return run_search(stack.cfg, read_object(args.policy), suite)
    raw = read_object(args.cases) if args.action == 'suite' else {
        'cases': [{'name': 'single', 'benchmark': workload(args)}]}
    directory = args.cases.resolve().parent if args.action == 'suite' else Path.cwd()
    return run_suite(stack.cfg, prepare_suite(stack.cfg, raw, directory))


def main():
    args = parser_for_cli().parse_args()
    if args.command == 'bench' and args.action == 'slo':
        def interrupted(*_):
            raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM, interrupted)
    try:
        return execute(args)
    except KeyboardInterrupt:
        return 130
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
