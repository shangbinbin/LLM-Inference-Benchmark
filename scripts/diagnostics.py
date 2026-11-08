"""Build bounded, local-only diagnostic archives from the selected stack."""
from contextlib import redirect_stderr
from datetime import datetime, timezone
import http.client
import io
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

from render_observability import ROOT

FILE_LIMIT = 256 * 1024
TOTAL_LIMIT = 24 * 1024 * 1024
REPORT_LIMIT = 240
REPORTS = {'client.log', 'suite-results.json', 'search-results.json', 'trial.json',
           'batch_perf_summary.csv', 'policy.json'}
SECRET = r'(?:[a-z0-9_-]{0,48}[_-])?(?:authorization|proxy-authorization|api[-_]?key|access[-_]?token|refresh[-_]?token|token|password|passwd|secret|cookie|set-cookie)'


def redact(text):
    text = re.sub(r'(?im)^(\s*(?:set-cookie|cookie):)[^\r\n]*', r'\1 [redacted]', text)
    text = re.sub(r'(https?://)[^/\s@]+@', r'\1[redacted]@', text, flags=re.I)
    text = re.sub(r'(?i)\b(Bearer|Basic)\s+[\w.+/=-]+', r'\1 [redacted]', text)
    text = re.sub(rf'''(?ix)(["']?\b{SECRET}["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s&,;}}]+)''',
                  r'\1"[redacted]"', text)
    return text


def clean_json(value):
    if isinstance(value, dict):
        return {key: ('[redacted]' if re.fullmatch(SECRET, key, re.I) or key == 'generation_kwargs'
                      else clean_json(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_json(item) for item in value]
    return redact(value) if isinstance(value, str) else value


def clean_text(text):
    # Keep JSONL parseable when a console event contains a quoted credential.
    lines = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith('{'):
            try:
                value = json.loads(line)
            except ValueError:
                pass
            else:
                lines.append(json.dumps(clean_json(value), ensure_ascii=False) + '\n')
                continue
        lines.append(redact(line))
    return ''.join(lines)


def record_event(root, action, **details):
    # Observability must not change the outcome of the action being observed.
    try:
        path = root / 'diagnostics/events.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            path.replace(path.with_suffix('.previous.jsonl'))
        event = dict(at=datetime.now(timezone.utc).isoformat(), action=action, **details)
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a') as stream:
            stream.write(json.dumps(clean_json(event), ensure_ascii=False) + '\n')
    except OSError as exc:
        print('Could not save diagnostic event: ' + str(exc), file=sys.stderr)


def failure_detail(exc):
    parts = [str(exc)]
    for attr in ('stderr', 'stdout'):
        value = getattr(exc, attr, None)
        if value:
            tail = value[-4096:]
            parts.append(tail.decode(errors='replace') if isinstance(tail, bytes) else tail)
    return redact('\n'.join(parts)[-16000:])


def safe_file(base, path):
    # Refuse symlinked inputs, including parent components of individual logs.
    relative = path.relative_to(base)
    if '..' in relative.parts or any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(base)):
        raise ValueError('unsafe file path')
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('expected a regular file')


def tail_bytes(stream, lines=None):
    size = stream.seek(0, os.SEEK_END)
    stream.seek(max(0, size - FILE_LIMIT))
    data = stream.read(FILE_LIMIT)
    if lines is not None:
        data = b'\n'.join(data.splitlines()[-lines:])
    return data.decode('utf-8', errors='replace'), size > FILE_LIMIT


class Bundle:
    def __init__(self, tar):
        self.tar = tar
        self.bytes = 0
        self.notes = []
        self.files = 0

    def add(self, name, text, *, structured=False, mtime=None):
        data = (json.dumps(clean_json(text), ensure_ascii=False, indent=2) if structured else clean_text(text)).encode()
        if self.bytes + len(data) > TOTAL_LIMIT:
            if 'archive size limit reached' not in self.notes:
                self.notes.append('archive size limit reached')
            return
        self.put(name, data, mtime)
        self.bytes += len(data)

    def put(self, name, data, mtime=None):
        member = tarfile.TarInfo(name)
        member.mtime = int(time.time() if mtime is None else mtime)
        member.size, member.mode = len(data), 0o600
        self.tar.addfile(member, io.BytesIO(data))
        self.files += 1

    def file(self, base, path, name, lines=None, *, structured=False, failures_only=False):
        try:
            safe_file(base, path)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as f:
                source = os.fstat(fd)
                if not stat.S_ISREG(source.st_mode):
                    raise ValueError('expected a regular file')
                text, truncated = tail_bytes(f, lines)
            if failures_only:
                failures = []
                for line in text.splitlines():
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue  # The first or last record may be partial in a live tail.
                    if isinstance(item, dict) and item.get('success') is False:
                        failures.append({k: item[k] for k in ('id', 'turn_id', 'error_info')
                                         if isinstance(item.get(k), (int, str))})
                if failures:
                    self.add(name, failures, structured=True, mtime=source.st_mtime)
                return
            if structured:
                if truncated:
                    self.notes.append(name + ': JSON exceeds file limit; omitted')
                    return
                self.add(name, json.loads(text), structured=True, mtime=source.st_mtime)
                return
            if truncated:
                self.notes.append(name + ': byte tail only')
            self.add(name, text, mtime=source.st_mtime)
        except (OSError, ValueError, RecursionError) as exc:
            self.notes.append(f'{name}: {exc}')


def collect_targets(bundle, obj):
    bindings = (obj.get('NetworkSettings', {}).get('Ports') or {}).get('9090/tcp') or []
    local = next((b for b in bindings if b.get('HostIp') == '127.0.0.1'), None)
    if not local:
        bundle.notes.append('Prometheus loopback port unavailable; see CLI readiness events for earlier targets')
        return
    client = http.client.HTTPConnection('127.0.0.1', int(local['HostPort']), timeout=3)
    try:
        client.request('GET', '/api/v1/targets?state=active')
        response = client.getresponse()
        if response.status != 200:
            raise ValueError(f'HTTP {response.status}')
        body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError('target response exceeds 1 MiB')
        targets = json.loads(body)['data']['activeTargets']
        rows = [{key: target.get(key) for key in ('scrapePool', 'scrapeUrl', 'health', 'lastScrape', 'lastError')}
                for target in targets]
        bundle.add('monitoring/targets.json', rows, structured=True)
    except (http.client.HTTPException, OSError, ValueError, TypeError, KeyError) as exc:
        bundle.notes.append('Prometheus targets: ' + str(exc))
    finally:
        client.close()


def collect_containers(bundle, cfg, lines):
    from benchmark_stack import LABEL
    runtime = cfg.get('runtime', ['docker'])
    project = cfg['project']

    def query(*args):
        result = subprocess.run([*runtime, *args], capture_output=True, timeout=10, check=True)
        return result.stdout.decode(errors='replace')

    try:
        bundle.add('runtime/version.txt', query('version'))
    except (OSError, subprocess.SubprocessError) as exc:
        bundle.notes.append('runtime version: ' + failure_detail(exc))
    try:
        ids = query('ps', '-a', '--filter', f'label={LABEL}={project}', '--format', '{{.ID}}').split()
    except (OSError, subprocess.SubprocessError) as exc:
        bundle.notes.append('container discovery: ' + failure_detail(exc))
        return
    if not ids:
        bundle.notes.append('no containers found with project label ' + project)
    for cid in ids:
        try:
            if not re.fullmatch('[a-fA-F0-9]{12,64}', cid):
                raise ValueError('invalid runtime container ID')
            obj = json.loads(query('inspect', cid))[0]
            config = obj['Config']
            if (config.get('Labels') or {}).get(LABEL) != project:
                raise ValueError('project label mismatch')
            name = obj['Name'].lstrip('/')
            if not re.fullmatch(r'[a-zA-Z0-9_.-]+', name):
                raise ValueError('invalid container name')
            prefix = f'containers/{name}/'
            state = obj.get('State') or {}
            status = {k: state.get(k) for k in ('Status', 'Running', 'ExitCode', 'OOMKilled', 'Error', 'StartedAt', 'FinishedAt')}
            status.update(name=name, id=cid, image=config.get('Image'), image_id=obj.get('Image'),
                          restarts=obj.get('RestartCount'), health=(state.get('Health') or {}).get('Status'))
            bundle.add(prefix + 'state.json', status, structured=True)
            if name == project + '-prometheus' and state.get('Running'):
                collect_targets(bundle, obj)
            # Keep both application streams without buffering arbitrary-length log lines in RAM.
            with tempfile.TemporaryFile() as log:
                result = subprocess.run([*runtime, 'logs', '--timestamps', '--tail', str(lines), cid],
                                        stdout=log, stderr=subprocess.STDOUT, timeout=15)
                text, truncated = tail_bytes(log)
                bundle.add(prefix + 'container.log', text)
                if result.returncode or truncated:
                    bundle.notes.append(f'{name}: logs exit={result.returncode}, truncated={truncated}')
        except (KeyError, IndexError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
            bundle.notes.append(f'container {cid}: ' + failure_detail(exc))


def collect_results(bundle, base, lines, selected):
    # Only visit the latest run of each normal CLI entry point.
    for kind in ('suites', 'slo-search', ''):
        parent = base / kind
        if selected and selected.parent != Path(kind):
            continue
        if not parent.is_dir():
            continue
        if base.is_symlink() or parent.is_symlink():
            bundle.notes.append(f'results/{kind}: symlink skipped')
            continue
        runs = [p for p in parent.iterdir() if p.name[:1].isdigit() and not p.is_symlink() and p.is_dir()]
        if not runs:
            continue
        run = base/selected if selected else max(runs, key=lambda p: p.name)
        candidates = []
        for folder, dirs, names in os.walk(run):
            dirs[:] = [d for d in dirs if d not in ('db_data', 'predictions', 'configs', 'tmp')]
            for name in names:
                path = Path(folder) / name
                parts = path.relative_to(run).parts
                if (name in REPORTS or ('logs' in parts and path.suffix in ('.log', '.out')) or
                        ('performances' in parts and path.stem in ('synthetic', 'jsonl', 'sharegpt')
                         and path.suffix in ('.csv', '.json')) or
                        ('performances' in parts and name in ('synthetic_details.jsonl', 'jsonl_details.jsonl',
                                                              'sharegpt_details.jsonl'))):
                    if not path.is_symlink():
                        try:
                            candidates.append((path.stat().st_mtime_ns, path))
                        except OSError as exc:
                            bundle.notes.append(str(exc))
        # Preserve summaries before the most recent trial evidence on long searches.
        candidates.sort(key=lambda pair: (pair[1].name in ('suite-results.json', 'search-results.json'), pair[0]), reverse=True)
        if len(candidates) > REPORT_LIMIT:
            bundle.notes.append(f'{run.name}: limited to {REPORT_LIMIT} result files')
        for _, path in candidates[:REPORT_LIMIT]:
            member = 'results/' + str(path.relative_to(base))
            if path.name.endswith('_details.jsonl'):
                bundle.file(base, path, member.replace('_details.jsonl', '_failures.json'), lines, failures_only=True)
            else:
                bundle.file(base, path, member, lines if path.suffix in ('.log', '.out') else None,
                            structured=path.suffix == '.json')


def collect(cfg, vendor=None, *, output=None, lines=500, extra_logs=(), selected=None, error=None):
    if not 1 <= lines <= 10000:
        raise ValueError('--tail must be 1..10000')
    root = (ROOT / Path(cfg['storage']['root']).expanduser()).resolve()
    logs = (ROOT / Path(cfg['storage']['logs']).expanduser()).resolve()
    base = root/'data/results'
    if selected:
        if not re.fullmatch(r'(?:(?:suites|slo-search)/)?[0-9][0-9A-Z_]*', str(selected)):
            raise ValueError('--run requires a run ID relative to data/results, e.g. suites/<UTC>')
        path = base / selected
        if path.resolve() != path or not path.is_dir():
            raise ValueError('Selected result run missing or symlinked: ' + str(selected))
    timestamp = datetime.now(timezone.utc)
    dest = output or root/'diagnostics'/('llm-bench-' + timestamp.strftime('%Y%m%dT%H%M%S%fZ') + '.tar.gz')
    dest = dest.expanduser().absolute()
    if dest.exists():
        raise FileExistsError('Output already exists: ' + str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix='.llm-bench-') as staging:
        tmp = Path(staging) / 'bundle.tar.gz'
        with tarfile.open(tmp, mode='w:gz') as tar:
            bundle = Bundle(tar)
            meta = {'created_at': timestamp.isoformat(), 'project': cfg['project'], 'offline': cfg.get('offline', False),
                    'simulation': cfg.get('simulation', False), 'platform': cfg.get('platform'),
                    'host_os': platform.system(), 'host_arch': platform.machine(), 'python': platform.python_version(),
                    'gpu_vendor': vendor or cfg.get('gpu', {}).get('vendor'), 'images': cfg.get('images', {}),
                    'topology': cfg.get('topology', {}), 'gpu_deployment': cfg.get('gpu', {}).get('deployment'),
                    'inference': {k: cfg.get('inference', {}).get(k) for k in ('model', 'api', 'base_url')},
                    'tail_lines': lines, 'selected_run': str(selected) if selected else None, 'error': error}
            if (root/'state.json').exists():
                bundle.file(root, root/'state.json', 'stack/state.json', structured=True)
            else:
                bundle.notes.append('state.json unavailable (before startup or after down)')
            for events in ('events.previous.jsonl', 'events.jsonl'):
                path = root/'diagnostics'/events
                if path.exists():
                    bundle.file(root, path, 'cli/' + events, lines)
            collect_containers(bundle, cfg, lines)
            for name in cfg.get('log_files', ['server.log']):
                if not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts:
                    bundle.notes.append('invalid inference log path skipped')
                    continue
                bundle.file(logs, logs/name, 'inference/' + name, lines)
            for index, path in enumerate(extra_logs, 1):
                path = path.expanduser().absolute()
                bundle.file(path.parent, path, f'external/{index}-{path.name}', lines)
            try:
                if (root/'data').is_symlink():
                    raise ValueError('symlinked data directory skipped')
                collect_results(bundle, base, lines, selected)
            except (OSError, ValueError) as exc:
                bundle.notes.append('results: ' + str(exc))
            meta.update(files_collected=bundle.files, collection_notes=bundle.notes)
            bundle.put('manifest.json', json.dumps(clean_json(meta), ensure_ascii=False, indent=2).encode())
        tmp.chmod(0o600)
        # Reserve a new destination and publish on the same filesystem, including removable media.
        os.close(os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        try:
            tmp.replace(dest)
        except OSError:
            dest.unlink()
            raise
    return dest


class ErrorTail:
    def __init__(self, stream):
        self.stream = stream
        self.text = ''

    def write(self, text):
        self.text = (self.text + text[-4096:])[-4096:]
        return self.stream.write(text)

    def __getattr__(self, name):
        return getattr(self.stream, name)


def run_diagnosed(stack, action, fn, *args, **kwargs):
    def snapshot(message):
        try:
            path = collect(stack.cfg, stack.vendor, error={'operation': action, 'message': message})
            print('Diagnostic bundle: ' + str(path), file=sys.stderr, flush=True)
        except Exception as exc:
            print('Diagnostic collection failed: ' + str(exc), file=sys.stderr)

    record_event(stack.root, action, status='started')
    stderr = ErrorTail(sys.stderr)
    try:
        with redirect_stderr(stderr):
            code = fn(*args, **kwargs)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        detail = failure_detail(exc)
        record_event(stack.root, action, status='failed', error=detail)
        snapshot(detail)
        raise
    record_event(stack.root, action, status='finished', exit_code=code or 0)
    if code:
        snapshot(f'{action} exited with code {code}. ' + (stderr.text.strip() or 'See case/trial summaries and client logs.'))
    return code
