"""Readiness probes and log rotation; no container runtime socket required."""
import argparse
import json
import signal
import shutil
import subprocess
import time
import urllib.request


def probe(url, timeout, contains):
    deadline = time.monotonic() + timeout
    error = ''
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                body = response.read().decode()
            if contains and contains not in body:
                raise ValueError(f'required content missing: {contains}')
            print(json.dumps({'url': url, 'ready': True}))
            return
        except Exception as exc:
            error = str(exc)
            time.sleep(1)
    raise SystemExit(f'Readiness failed: {url}: {error}')


def check_targets(url, minimum, timeout):
    deadline = time.monotonic() + timeout
    details = []
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                data = json.load(response)
            targets = data['data']['activeTargets']
            details = [(t['scrapeUrl'], t['health'], t.get('lastError')) for t in targets]
            if len(targets) >= minimum and all(t['health'] == 'up' for t in targets):
                print(json.dumps({'all_targets_up': True, 'targets': details}))
                return
        except Exception as exc:
            details = [str(exc)]
        time.sleep(1)
    raise SystemExit('Targets not ready: ' + json.dumps(details))


def rotate(interval):
    shutil.copyfile("/config/inference.conf", "/etc/logrotate.d/inference")
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        subprocess.run(['logrotate', '--state', '/state/logrotate.status',
                        '/etc/logrotate.d/inference'], check=True)
        deadline = time.monotonic() + interval
        while running and time.monotonic() < deadline:
            time.sleep(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('probe')
    p.add_argument('url')
    p.add_argument('--timeout', type=int, default=120)
    p.add_argument('--contains', default='')
    p = sub.add_parser('targets')
    p.add_argument('url')
    p.add_argument('--minimum', type=int, required=True)
    p.add_argument('--timeout', type=int, default=120)
    p = sub.add_parser('rotate')
    p.add_argument('--interval', type=int, default=60)
    args = parser.parse_args()
    if args.command == 'probe':
        probe(args.url, args.timeout, args.contains)
    elif args.command == 'targets':
        check_targets(args.url, args.minimum, args.timeout)
    else:
        rotate(args.interval)
