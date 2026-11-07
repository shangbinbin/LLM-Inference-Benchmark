#!/usr/bin/env python3
"""Adaptive concurrency search using real AISBench reports and user-specified SLOs."""
import argparse
import csv
import json
import math
from pathlib import Path
import re
import signal
import sys
import subprocess
import time
from datetime import datetime, timezone

from aisbench_timing import measured_peak_concurrency
from aisbench_trial import TrialSession, utc_now
from benchmark_stack import load_config, write_json


DEFAULTS = {'step': 1, 'repeats': 3, 'strategy': 'verified_max', 'statistic': 'P99',
            'max_rounds': 64, 'max_duration_seconds': 3600, 'trial_timeout_seconds': 300,
            'cooldown_seconds': 5, 'min_requests': 1000}


def validate_policy(raw, cfg):
    policy = dict(DEFAULTS, **raw)
    keys = set(DEFAULTS) | {'min_concurrency', 'max_concurrency', 'thresholds_ms'}
    if set(policy) - keys:
        raise ValueError('Unknown search policy fields: ' + str(sorted(set(policy) - keys)))
    for key in ('min_concurrency', 'max_concurrency', 'step', 'repeats', 'max_rounds', 'min_requests'):
        if type(policy[key]) is not int or policy[key] < 1:
            raise ValueError(key + ' must be a positive integer')
    low, high, step = (policy[k] for k in ('min_concurrency', 'max_concurrency', 'step'))
    if low > high or (high - low) % step:
        raise ValueError('Range must be ordered and (max-min) divisible by step')
    if (high - low) // step > 100000:
        raise ValueError('Search grid exceeds 100001 candidates')
    for key in ('max_duration_seconds', 'trial_timeout_seconds', 'cooldown_seconds'):
        value = policy[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (key != 'cooldown_seconds' and value == 0):
            raise ValueError(key + ' must be finite and positive (cooldown may be zero)')
    if policy['strategy'] not in ('verified_max', 'binary'):
        raise ValueError('strategy must be verified_max/binary')
    if policy['statistic'] not in ('P99', 'P90', 'P75', 'Median', 'Average', 'Max'):
        raise ValueError('Unsupported statistic for the pinned AISBench reports')
    thresholds = policy['thresholds_ms']
    if not isinstance(thresholds, dict) or not thresholds or set(thresholds) - {'TTFT', 'TPOT', 'E2EL', 'ITL'}:
        raise ValueError('thresholds_ms must select TTFT/TPOT/E2EL/ITL')
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in thresholds.values()):
        raise ValueError('Thresholds must be finite positive milliseconds')
    b = cfg['benchmark']
    if b['mode'] != 'perf' or b.get('request_rate', 0) != 0 or b.get('retry', 1) != 1:
        raise ValueError('Search requires perf, request_rate=0 and retry=1')
    if b.get('concurrency_levels') or 'requests_per_level' in b:
        raise ValueError('Remove native sweep arrays before adaptive search')
    if b['output_length'] < 2 or b['request_count'] < max(high, policy['min_requests']):
        raise ValueError('Need output_length >= 2 and request_count >= max_concurrency/min_requests')
    return policy


def numeric(value, unit=''):
    match = re.fullmatch(r'\s*([0-9.eE+\-]+)\s*' + re.escape(unit) + r'\s*', str(value))
    if not match:
        raise ValueError(f'Expected finite nonnegative metric in {unit!r}: {value!r}')
    result = float(match[1])
    if not math.isfinite(result) or result < 0:
        raise ValueError('Invalid numeric metric')
    return result


def evaluate(results, benchmark, concurrency, policy):
    reports = list(results.glob('*/performances/*/synthetic.json'))
    if len(reports) != 1:
        raise ValueError('Exactly one fresh synthetic report is required')
    report = reports[0]
    common = json.loads(report.read_text())
    total = numeric(common['Total Requests']['total'])
    if total != benchmark['request_count'] or total < policy['min_requests']:
        raise ValueError('Incomplete or insufficient request count')
    if numeric(common['Failed Requests']['total']) != 0 or numeric(common['Success Requests']['total']) != total:
        raise ValueError('Failed requests: invalid measurement, not a latency boundary')
    # AISBench reports configured batch_size here, not the observed peak.
    configured_concurrency = numeric(common['Max Concurrency']['total'])
    if configured_concurrency != concurrency:
        raise ValueError('Configured concurrency differs from the requested concurrency')
    details = report.with_name(report.stem + '_details.jsonl')
    peak = measured_peak_concurrency(details, int(total))
    if peak != concurrency:
        raise ValueError(f'Measured peak concurrency {peak} differs from requested {concurrency}')
    with report.with_suffix('.csv').open(newline='') as f:
        rows = [r for r in csv.DictReader(f) if r['Stage'] == 'total']
    def row(metric):
        matching = [r for r in rows if r['Performance Parameters'] == metric]
        if len(matching) != 1 or numeric(matching[0]['N']) != total:
            raise ValueError('Missing, duplicate or incomplete metric: ' + metric)
        return matching[0]
    output = row('OutputTokens')
    if any(numeric(output[key]) != benchmark['output_length'] for key in ('Min', 'Max')):
        raise ValueError('Actual output length differs from the fixed workload')
    values = {m: numeric(row(m)[policy['statistic']], 'ms') for m in policy['thresholds_ms']}
    violations = [m for m, limit in policy['thresholds_ms'].items() if values[m] > limit]
    return {'status': 'SLO_FAIL' if violations else 'PASS', 'latencies_ms': values,
            'violations': violations, 'success_requests': int(total),
            'configured_concurrency': int(configured_concurrency), 'measured_peak_concurrency': peak,
            'concurrency_evidence': str(details.relative_to(results.parent)),
            'output_tokens_per_second': numeric(common['Output Token Throughput']['total'], 'token/s'),
            'report': str(report.relative_to(results.parent))}


class SearchStopped(Exception):
    pass


def search(policy, measure, checkpoint=lambda _: None, clock=time.monotonic, sleep=time.sleep, started=None):
    """Pure decision engine: measure(concurrency, repeat, round) returns validated trial data."""
    started = clock() if started is None else started
    grid = range(policy['min_concurrency'], policy['max_concurrency'] + 1, policy['step'])
    rounds, observations = [], {}
    result = {'schema_version': 1, 'policy': policy, 'started_at': utc_now(), 'rounds': rounds,
              'status': 'running', 'best_observed_concurrency': None, 'recommended_concurrency': None,
              'maximum_verified': False, 'conditional_on_monotonicity': policy['strategy'] == 'binary'}
    trial_count = 0

    def save():
        result['elapsed_seconds'] = round(clock() - started, 3)
        result['trial_count'] = trial_count
        checkpoint(result)

    def time_budget():
        if clock() - started >= policy['max_duration_seconds']:
            raise SearchStopped('time_budget')

    def probe(index, phase):
        nonlocal trial_count
        if index in observations:
            return observations[index]
        time_budget()
        if len(rounds) >= policy['max_rounds']:
            raise SearchStopped('round_budget')
        concurrency = grid[index]
        current = {'round': len(rounds) + 1, 'phase': phase, 'concurrency': concurrency,
                   'status': 'running', 'trials': [], 'started_at': utc_now()}
        rounds.append(current)
        save()
        for repeat in range(1, policy['repeats'] + 1):
            if trial_count:
                if clock() - started + policy['cooldown_seconds'] >= policy['max_duration_seconds']:
                    raise SearchStopped('time_budget')
                sleep(policy['cooldown_seconds'])
            time_budget()
            trial_count += 1
            trial = {'repeat': repeat, 'started_at': utc_now(), 'status': 'running'}
            current['trials'].append(trial)
            save()
            try:
                trial.update(measure(concurrency, repeat, current['round']))
                if trial['status'] not in ('PASS', 'SLO_FAIL'):
                    raise ValueError('Unexpected trial status')
                time_budget()
            except KeyboardInterrupt:
                trial.update(status='INTERRUPTED', finished_at=utc_now())
                raise
            except Exception as exc:
                trial.update(status='INVALID', reason=str(exc), finished_at=utc_now())
                current['status'] = 'INVALID'
                save()
                if clock() - started >= policy['max_duration_seconds']:
                    raise SearchStopped('time_budget') from exc
                reason = 'trial_timeout' if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else 'invalid_trial'
                raise SearchStopped(reason) from exc
            trial['finished_at'] = utc_now()
            save()
        passed = all(t['status'] == 'PASS' for t in current['trials'])
        current.update(status='PASS' if passed else 'SLO_FAIL', finished_at=utc_now())
        observations[index] = passed
        if passed:
            result['best_observed_concurrency'] = max(concurrency, result['best_observed_concurrency'] or 0)
        save()
        return passed

    try:
        # Endpoint checks establish a bracket, then integer binary search reduces it.
        low, high = -1, len(grid)
        if probe(0, 'lower_bound'):
            low = 0
            if len(grid) > 1:
                if probe(len(grid) - 1, 'upper_bound'):
                    low = len(grid) - 1
                else:
                    high = len(grid) - 1
                    while high - low > 1:
                        middle = (low + high) // 2
                        if probe(middle, 'binary'):
                            low = middle
                        else:
                            high = middle
        else:
            high = 0
        if policy['strategy'] == 'verified_max':
            # Do not assume global monotonicity: rule out every greater grid point.
            for index in range(len(grid) - 1, low, -1):
                if probe(index, 'verify_higher'):
                    low = index
                    break
        time_budget()
        result.update(status='complete', stop_reason='upper_bound_passed' if low == len(grid) - 1 else (
            'boundary_found' if low >= 0 else 'no_feasible_concurrency'),
            recommended_concurrency=grid[low] if low >= 0 else None,
            maximum_verified=policy['strategy'] == 'verified_max')
    except SearchStopped as exc:
        result.update(status='stopped', stop_reason=str(exc))
    except KeyboardInterrupt:
        result.update(status='stopped', stop_reason='interrupted')
    finally:
        for current in rounds:
            if current['status'] == 'running':
                current['status'] = 'INCOMPLETE'
        result['finished_at'] = utc_now()
        save()
    return result


def run_search(cfg, raw_policy):
    try:
        policy = validate_policy(raw_policy, cfg)
        run = Path(cfg['storage']['root']) / 'data/results/slo-search' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        run.mkdir(parents=True)
        write_json(run / 'stack.json', cfg)
        write_json(run / 'policy.json', policy)
        started = time.monotonic()
        deadline = started + policy['max_duration_seconds']
        output = run / 'search-results.json'
        print('Search report: ' + str(output), flush=True)
        with TrialSession(cfg, run, deadline) as session:
            def measure(concurrency, repeat, round_number):
                folder = run / f'round-{round_number:03d}-c{concurrency}-r{repeat}'
                print(f'TRIAL round={round_number} concurrency={concurrency} repeat={repeat}', flush=True)
                began = utc_now()
                try:
                    results = session.run_trial(concurrency, folder, policy['trial_timeout_seconds'])
                    trial = evaluate(results, cfg['benchmark'], concurrency, policy)
                except BaseException as exc:
                    folder.mkdir(exist_ok=True)
                    write_json(folder / 'trial.json', {'status': 'INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else 'INVALID',
                               'reason': str(exc), 'started_at': began, 'finished_at': utc_now(),
                               'concurrency': concurrency, 'repeat': repeat})
                    raise
                trial.update(directory=folder.name, started_at=began, finished_at=utc_now(), concurrency=concurrency)
                write_json(folder / 'trial.json', trial)
                print(f'{trial["status"]} c={concurrency}: {trial["latencies_ms"]}', flush=True)
                return trial
            def checkpoint(result):
                # Atomic checkpoints survive interruption without leaving half-written JSON.
                temporary = output.with_suffix('.tmp')
                write_json(temporary, result)
                temporary.replace(output)
            result = search(policy, measure, checkpoint, started=started)
        print(json.dumps({k: v for k, v in result.items() if k not in ('rounds', 'policy')}, indent=2))
        return 0 if result['status'] == 'complete' and result['recommended_concurrency'] is not None else 1
    except (KeyboardInterrupt, ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        reason = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'execution_error'
        if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) and 'deadline' in locals() and time.monotonic() >= deadline:
            reason = 'time_budget'
        if 'output' in locals():
            failed = json.loads(output.read_text()) if output.exists() else {
                'schema_version': 1, 'policy': policy, 'rounds': [], 'best_observed_concurrency': None,
                'trial_count': 0, 'conditional_on_monotonicity': policy['strategy'] == 'binary'}
            failed.update(status='stopped', stop_reason=reason, error=str(exc),
                          recommended_concurrency=None, maximum_verified=False, finished_at=utc_now())
            write_json(output, failed)
        print(f'ERROR: {exc}', file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else (1 if reason == 'time_budget' else 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--search', type=Path, required=True, help='SLO search policy JSON')
    args = parser.parse_args()
    try:
        return run_search(load_config(args.config), json.loads(args.search.read_text()))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f'ERROR: {exc}\n')


if __name__ == '__main__':
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())
