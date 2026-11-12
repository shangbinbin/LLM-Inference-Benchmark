"""Docker/nerdctl command differences and explicit containerd namespace selection."""
from pathlib import Path
import re


def runtime_kind(cfg):
    explicit = cfg.get('runtime_kind')
    if explicit is not None:
        if explicit not in ('docker', 'nerdctl'):
            raise ValueError('runtime_kind must be docker/nerdctl')
        return explicit
    return 'nerdctl' if any(Path(arg).name == 'nerdctl' for arg in cfg.get('runtime', [])) else 'docker'


def namespace(cfg):
    args = cfg.get('runtime', [])
    binary = next((i for i, arg in enumerate(args) if Path(arg).name == 'nerdctl'), None)
    if binary is not None:
        args = args[binary+1:]  # Do not interpret sudo -n as a containerd namespace.
    values = []
    for i, arg in enumerate(args):
        if arg in ('--namespace', '-n'):
            values.append(args[i+1] if i+1 < len(args) else '')
        elif arg.startswith('--namespace='):
            values.append(arg.split('=', 1)[1])
    if len(values) != 1 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,75}', values[0]):
        raise ValueError('nerdctl runtime requires one explicit --namespace NAME (or -n NAME); use the same namespace for image load')
    return values[0]


def validate_runtime(cfg):
    args = cfg.get('runtime', ['docker'])
    if not isinstance(args, list) or not args or any(not isinstance(a, str) or not a.strip() for a in args):
        raise ValueError('runtime must be a nonempty command array of strings')
    if Path(args[0]).name in ('ctr', 'ctrcli'):
        raise ValueError('ctr/ctrcli is not a Docker-compatible runtime; use nerdctl --namespace benchmark')
    if runtime_kind(cfg) == 'nerdctl':
        namespace(cfg)


def adapt_command(cfg, args):
    """Use Docker-compatible inspection explicitly, independent of nerdctl defaults."""
    args = list(args)
    if runtime_kind(cfg) != 'nerdctl':
        return args
    if args[0] in ('run', 'create'):
        args.insert(1, '--log-driver=json-file')  # Keep logs collect usable with custom nerdctl defaults.
    elif args[0] == 'inspect':
        args.insert(1, '--mode=dockercompat')
    elif args[:2] in (['image', 'inspect'], ['network', 'inspect']):
        args.insert(2, '--mode=dockercompat')
        if args[:2] == ['image', 'inspect'] and cfg.get('platform'):
            args.insert(3, '--platform='+cfg['platform'])
    return args


def service_network(cfg, network, service, host=False):
    if host:
        return ['--network', 'host']
    # nerdctl's hosts store publishes hostnames to peers on the same CNI network.
    # It does not implement Docker's --network-alias flag (tested with v2.1.3).
    alias = '--hostname' if runtime_kind(cfg) == 'nerdctl' else '--network-alias'
    return ['--network', network, alias, service]


def runtime_scope(cfg):
    scope = {'command': cfg.get('runtime', ['docker']), 'kind': runtime_kind(cfg)}
    if scope['kind'] == 'nerdctl':
        scope['namespace'] = namespace(cfg)
    return scope


def missing_resource(result, resource='container'):
    message = (getattr(result, 'stderr', '') or '') + (getattr(result, 'stdout', '') or '')
    if resource == 'network':
        return 'no network found matching' in message.lower() or 'no such network' in message.lower()
    return bool(re.search(r'no such (?:object|container)\b', message, re.I))
