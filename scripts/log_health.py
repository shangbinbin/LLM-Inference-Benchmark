"""Read-only checks of configured files and the project's Loki ingestion path."""
import json
from pathlib import Path
import stat
import subprocess

from observability_labels import log_sources, task_name
from render_observability import ROOT


def log_root(cfg):
    return (ROOT / Path(cfg['storage']['logs']).expanduser()).absolute()


def file_status(path, source):
    item = {'path': str(path), 'source_file': source}
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            item['status'] = 'not_a_file'
        else:
            with path.open('rb') as stream:
                stream.read(1)  # Check readability without returning log contents.
            item.update(status='readable' if info.st_size else 'empty', bytes=info.st_size)
    except FileNotFoundError:
        item['status'] = 'missing'
    except OSError as exc:
        item.update(status='unreadable', error=str(exc))
    return item


def host_files(cfg):
    return [file_status(log_root(cfg) / source['path'], source['path']) for source in log_sources(cfg)]


def file_errors(cfg, files, *, host=False):
    deferred = cfg.get('simulation', False) or cfg.get('allow_missing_logs', False)
    return [item for item in files if item['status'] not in ('readable', 'empty')
            and not (deferred and item['status'] == 'missing')
            and not (host and item['status'] == 'unreadable')]


def missing_directory(cfg):
    try:
        return not log_root(cfg).is_dir()
    except PermissionError:
        # A non-root CLI may lack access while Alloy's root UID can read the mount.
        return False


def require_log_files(cfg):
    if cfg.get('simulation'):
        return
    files = host_files(cfg)
    if missing_directory(cfg) or file_errors(cfg, files, host=True):
        details = '\n'.join('  ' + item['status'] + ': ' + item['path'] for item in files)
        raise ValueError('Configured inference logs are unavailable; no project containers created.\n' + details +
                         '\nFix storage.logs + log_sources[].path in the site JSON to match the real service file. '
                         'An empty existing file is allowed; for deliberately delayed files set allow_missing_logs=true '
                         '(the log directory must already exist). Use llm-bench logs check --config <site.json>.')


# Run in the existing toolbox image with the same read-only /logs mount and UID as Alloy.
# No image rebuild, runtime socket, API Token, log writes or synthetic log markers are required.
PROBE = r'''
import json, pathlib, stat, sys, time, urllib.parse, urllib.request
p = json.loads(sys.argv[1])
result = {'files': []}
for source in p['sources']:
    path = pathlib.Path('/logs') / source
    item = {'path':str(path), 'source_file':source}
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            item['status'] = 'not_a_file'
        else:
            with path.open('rb') as stream: stream.read(1)
            item.update(status='readable' if info.st_size else 'empty', bytes=info.st_size)
    except FileNotFoundError: item['status'] = 'missing'
    except OSError as exc: item.update(status='unreadable', error=str(exc))
    result['files'].append(item)
if p['query_loki']:
    # These are fixed service names on the owned container network, never external URLs.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def get(url):
        with opener.open(url, timeout=5) as response: return response.read().decode()
    for service, url in [('alloy','http://alloy:12345/-/ready'), ('loki','http://loki:3100/ready')]:
        try: get(url); result[service] = {'ready':True}
        except Exception as exc: result[service] = {'ready':False, 'error':str(exc)}
    try:
        end = time.time_ns()
        interval = {'start':str(end-p['lookback_minutes']*60*10**9), 'end':str(end)}
        labels = json.loads(get('http://loki:3100/loki/api/v1/labels?'+urllib.parse.urlencode(interval)))
        query = dict(interval, query='{task='+json.dumps(p['task'])+'}', limit='1')
        data = json.loads(get('http://loki:3100/loki/api/v1/query_range?'+urllib.parse.urlencode(query)))
        if labels.get('status') != 'success' or data.get('status') != 'success':
            raise ValueError('Loki did not return a successful query')
        result.setdefault('loki', {}).update(labels=labels.get('data', []), logs_found=bool(data['data']['result']),
            task=p['task'], lookback_minutes=p['lookback_minutes'])
    except Exception as exc: result.setdefault('loki', {}).update(error=str(exc))
print(json.dumps(result))
'''


def container_report(cfg, command_builder, network='none', query_loki=False, lookback_minutes=60):
    payload = {'sources': [source['path'] for source in log_sources(cfg)], 'task': task_name(cfg),
               'query_loki': query_loki, 'lookback_minutes': lookback_minutes}
    args = ['run', '--rm', '--user', '0:0', '--platform', cfg['platform'], '--network', network,
            '--label', 'io.llm-benchmark.project=' + cfg['project'],
            '--mount', 'type=bind,src=' + str(log_root(cfg)) + ',dst=/logs,readonly',
            '--entrypoint', 'python', cfg['images']['toolbox'], '-c', PROBE, json.dumps(payload)]
    result = subprocess.run(command_builder(cfg, args), text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError('Read-only log probe failed: ' + (result.stderr or result.stdout).strip())
    report = json.loads(result.stdout)
    if not isinstance(report, dict) or not isinstance(report.get('files'), list):
        raise ValueError('Invalid log probe response')
    return report


def check_logs(cfg, command_builder, network=None, lookback_minutes=60):
    if type(cfg.get('allow_missing_logs', False)) is not bool:
        raise ValueError('allow_missing_logs must be a JSON boolean (true/false)')
    report = {'project': cfg['project'], 'task': task_name(cfg), 'host_files': host_files(cfg)}
    errors = file_errors(cfg, report['host_files'], host=True)
    if missing_directory(cfg):
        errors.append({'path': str(log_root(cfg)), 'status': 'missing_log_directory'})
    if network and not errors:
        report['container'] = container_report(cfg, command_builder, network, True, lookback_minutes)
        errors.extend(file_errors(cfg, report['container']['files']))
        for service in ('alloy', 'loki'):
            status = report['container'].get(service, {})
            if not status.get('ready') or status.get('error'):
                errors.append({'service': service, **status})
    report['errors'] = errors
    found = report.get('container', {}).get('loki', {}).get('logs_found', False)
    report['status'] = 'error' if errors else ('ok' if found else 'pending')
    report['hint'] = ('Fix storage.logs + log_sources[].path and file permissions; check absolute symlinks from inside /logs. '
                      'After changing the site, down/up the owned stack so Alloy uses the new manifest.' if errors else
                      'Compare Grafana task and time range with this report. No log lines are written or returned. '
                      'If pending, start the stack if needed, send a small real request and recheck; '
                      'or increase --lookback-minutes for older entries.')
    return report
