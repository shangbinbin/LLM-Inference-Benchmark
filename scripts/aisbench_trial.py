"""Bounded AISBench trials on an existing stack; shared lock with sequential suites."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

from benchmark_stack import (LABEL, ais_config, load_config, write_json,
                             runtime_command, aisbench_environment, check_local_images)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class TrialSession:
    def __init__(self, cfg, run_dir, deadline):
        self.cfg, self.run_dir, self.deadline = cfg, run_dir, deadline
        self.root = Path(cfg['storage']['root'])
        self.lock = self.root / 'suite.lock'
        self.client = cfg['project'] + '-search-client'
        self.probe_name = cfg['project'] + '-search-probe'
        self.state = json.loads((self.root / 'state.json').read_text())
        manifest = json.loads((Path(self.state['config_dir']) / 'manifest.json').read_text())
        if self.state['project'] != cfg['project'] or manifest['stack'] != cfg:
            raise ValueError('Configuration does not match the running stack')
        if self.state['status'] not in ('ready', 'complete'):
            raise ValueError('Start the stack with up --no-benchmark, or wait for its benchmark to finish')
        self.locked = False

    def call(self, *args, timeout=30, cleanup=False, **kwargs):
        remaining = self.deadline - time.monotonic()
        if not cleanup and remaining <= 0:
            raise TimeoutError('search_deadline')
        return subprocess.run(runtime_command(self.cfg, args), text=True,
                              timeout=timeout if cleanup else min(timeout, remaining), **kwargs)

    def inspect(self, name, cleanup=False):
        result = self.call('inspect', name, capture_output=True, cleanup=cleanup)
        if result.returncode:
            # Distinguish absence from a runtime/daemon failure before releasing the lock.
            if 'no such' in result.stderr.lower() or 'not found' in result.stderr.lower():
                return None
            raise RuntimeError(result.stderr.strip())
        return json.loads(result.stdout)[0]

    def __enter__(self):
        with self.lock.open('x') as f:
            json.dump({'pid': os.getpid(), 'kind': 'slo-search', 'run_dir': str(self.run_dir)}, f)
        self.locked = True
        try:
            if self.cfg.get('offline', False):
                required = {}
                for key in ('aisbench', 'toolbox'):
                    required.setdefault(self.cfg['images'][key], []).append(key)
                def inspect_image(*args, capture=False, **kwargs):
                    return self.call(*args, capture_output=capture, **kwargs)
                check_local_images(self.cfg, required, inspect_image)
            else:
                for key in ('aisbench', 'toolbox'):
                    result = self.call('image', 'inspect', self.cfg['images'][key], capture_output=True, check=True)
                    image = json.loads(result.stdout)[0]
                    if image['Os'] + '/' + image['Architecture'] != self.cfg['platform']:
                        raise ValueError('Image architecture mismatch: ' + self.cfg['images'][key])
            if self.inspect(self.client) or self.inspect(self.probe_name):
                raise ValueError('Search client already exists; inspect it before retrying')
        except BaseException:
            self.lock.unlink()
            self.locked = False
            raise
        return self

    def __exit__(self, *_):
        # run_trial cleans up its own client first. A remaining client requires diagnosis.
        if self.locked and all(self.inspect(name, cleanup=True) is None for name in (self.client, self.probe_name)):
            self.lock.unlink()
            self.locked = False

    def ready(self):
        for name in self.state['containers']:
            info = self.inspect(name)
            if name.endswith('-aisbench'):
                if info and info['State']['Running']:
                    raise RuntimeError('Another benchmark is running')
            elif not info or not info['State']['Running'] or info['Config']['Labels'].get(LABEL) != self.cfg['project']:
                raise RuntimeError('Required owned container is unavailable: ' + name)
        # Check target health immediately before each new trial, without an unbounded readiness wait.
        if self.inspect(self.probe_name):
            raise RuntimeError('Probe name collision')
        token = 'probe-' + str(time.time_ns())
        expected = len(self.cfg['topology']['metrics_endpoints']) + (
            1 if self.cfg.get('simulation') else len(self.cfg['gpu']['nodes'])) + 1
        try:
            self.call('run', '--rm', '--name', self.probe_name, '--label', LABEL+'='+self.cfg['project'],
                      '--label', LABEL+'.trial='+token, '--platform', self.cfg['platform'],
                      '--network', self.state['network'], self.cfg['images']['toolbox'],
                      'targets', 'http://prometheus:9090/api/v1/targets', '--minimum', str(expected),
                      '--timeout', '10', timeout=20, capture_output=True, check=True)
        finally:
            self.remove_owned(self.probe_name, token)

    def remove_owned(self, name, token):
        info = self.inspect(name, cleanup=True)
        if info:
            labels = info['Config'].get('Labels', {})
            if labels.get(LABEL) != self.cfg['project'] or labels.get(LABEL+'.trial') != token:
                raise RuntimeError('Refuse to remove a container with different trial ownership')
            self.call('rm', '-f', name, cleanup=True, capture_output=True, check=True)

    def run_trial(self, concurrency, folder, timeout):
        self.ready()
        cfg = copy.deepcopy(self.cfg)
        cfg['benchmark']['concurrency'] = concurrency
        cfg['benchmark'].pop('concurrency_levels', None)
        cfg['benchmark'].pop('requests_per_level', None)
        folder.mkdir()
        write_json(folder / 'stack.json', cfg)
        cfg = load_config(folder / 'stack.json')
        (folder / 'aisbench.py').write_text(ais_config(cfg))
        (folder / 'results').mkdir()
        write_json(folder / 'trial.json', {'status': 'running', 'concurrency': concurrency, 'started_at': utc_now()})
        if self.inspect(self.client):
            raise RuntimeError('Client name collision')
        # Ownership token prevents cleanup of a same-name container created by another actor.
        token = folder.name + '-' + str(time.time_ns())
        attempted = False
        try:
            attempted = True
            self.call('create', *aisbench_environment(cfg), '--platform', cfg['platform'], '--name', self.client,
                      '--label', LABEL + '=' + cfg['project'], '--label', LABEL + '.trial=' + token,
                      '--network', self.state['network'],
                      '--mount', f'type=bind,src={folder}/aisbench.py,dst=/config/aisbench.py,readonly',
                      '--mount', f'type=bind,src={folder}/results,dst=/results',
                      '--mount', f'type=bind,src={cfg["storage"]["tokenizer"]},dst=/tokenizer,readonly',
                      cfg['images']['aisbench'], '/config/aisbench.py', '--mode', 'perf', '--debug',
                      '--max-num-workers', '1', '--num-warmups', str(cfg['benchmark'].get('warmups', 1)),
                      capture_output=True, check=True)
            with (folder / 'client.log').open('w') as log:
                result = self.call('start', '-a', self.client, timeout=timeout, stdout=log, stderr=subprocess.STDOUT)
            info = self.inspect(self.client)
            if result.returncode or not info or info['State']['Running'] or info['State']['ExitCode']:
                raise RuntimeError('AISBench client failed; inspect client.log')
        finally:
            if attempted:
                self.remove_owned(self.client, token)
        return folder / 'results'
