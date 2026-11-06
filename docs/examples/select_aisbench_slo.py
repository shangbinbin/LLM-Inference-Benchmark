#!/usr/bin/env python3
"""Read-only example: select measured concurrency levels satisfying a supplied SLO."""
import argparse
import csv
import json
import math
from pathlib import Path
import re


def number(text, unit):
    match = re.fullmatch(r'\s*([0-9.eE+\-]+)\s*' + re.escape(unit) + r'\s*', str(text))
    if not match:
        raise ValueError(f'Expected a value in {unit}: {text!r}')
    value = float(match[1])
    if not math.isfinite(value) or value < 0:
        raise ValueError('Metric must be finite and nonnegative')
    return value


def select(suite, policy, result_file):
    candidates = policy['concurrency_candidates']
    repeats = policy['repeats']
    minimum = policy['min_requests']
    if not candidates or any(type(n) is not int or n < 1 for n in candidates) or len(set(candidates)) != len(candidates):
        raise ValueError('concurrency_candidates must contain unique positive integers')
    if type(repeats) is not int or repeats < 1 or type(minimum) is not int or minimum < 1:
        raise ValueError('repeats and min_requests must be positive integers')
    limits = {metric: policy[key] for metric, key in (
        ('TTFT', 'ttft_p99_ms'), ('TPOT', 'tpot_p99_ms'), ('E2EL', 'e2el_p99_ms'))}
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in limits.values()):
        raise ValueError('SLO limits must be finite positive milliseconds')
    expected = {case['name']: case for case in suite['cases']}
    if len(expected) != len(suite['cases']):
        raise ValueError('Duplicate expected case names')
    for concurrency in candidates:
        if sum(c['benchmark']['concurrency'] == concurrency for c in expected.values()) != repeats:
            raise ValueError('Suite must contain exactly repeats cases per candidate')
    if len(expected) != len(candidates) * repeats:
        raise ValueError('Unexpected concurrency in suite')
    entries = json.loads(result_file.read_text())
    observed = {e['case']: e for e in entries}
    if len(observed) != len(entries) or set(observed) - set(expected):
        raise ValueError('Duplicate or unexpected result cases')
    rows, workloads = [], set()
    for name, case in expected.items():
        row = {'case': name, 'concurrency': case['benchmark']['concurrency'], 'status': 'INVALID'}
        try:
            entry = observed[name]
            b = entry['benchmark']
            if not all(b.get(k) == v for k, v in case['benchmark'].items()):
                raise ValueError('Run parameters do not match the requested case')
            if b['mode'] != 'perf' or b['request_rate'] != 0 or b.get('retry', 1) != 1:
                raise ValueError('Example requires perf mode, request_rate=0 and retry=1')
            workloads.add(json.dumps({k: v for k, v in b.items() if k != 'concurrency'}, sort_keys=True))
            if not entry['passed'] or entry['exit_code'] != 0 or len(entry['reports']) != 1:
                raise ValueError('Client/report integrity check failed')
            report = (result_file.parent / entry['reports'][0]).resolve()
            report.relative_to(result_file.parent.resolve())
            common = json.loads(report.read_text())
            total = common['Total Requests']['total']
            if total != b['request_count'] or total < minimum:
                raise ValueError('Insufficient or incomplete request sample')
            if common['Failed Requests']['total'] != 0 or common['Success Requests']['total'] != total:
                raise ValueError('This example requires 100% successful requests')
            if common['Max Concurrency']['total'] != b['concurrency']:
                raise ValueError('Observed peak concurrency did not reach the requested level')
            with report.with_suffix('.csv').open(newline='') as f:
                stats = [r for r in csv.DictReader(f) if r['Stage'] == 'total']
            outputs = [r for r in stats if r['Performance Parameters'] == 'OutputTokens']
            if len(outputs) != 1 or b['output_length'] < 2 or any(
                number(outputs[0][stat], '') != b['output_length'] for stat in ('Min', 'Max')
            ):
                raise ValueError('Example requires fixed actual output length >= 2; check early EOS/stop')
            values = {}
            for metric in limits:
                matching = [r for r in stats if r['Performance Parameters'] == metric]
                if len(matching) != 1 or float(matching[0]['N']) != total:
                    raise ValueError(f'Missing/invalid {metric} samples')
                values[metric] = number(matching[0]['P99'], 'ms')
            throughput = number(common['Output Token Throughput']['total'], 'token/s')
            row.update(p99_ms=values, output_tokens_per_second=throughput,
                       status='PASS' if all(values[m] <= limits[m] for m in limits) else 'SLO_FAIL')
        except (KeyError, ValueError, TypeError, OSError) as exc:
            row['reason'] = str(exc)
        rows.append(row)
    if len(workloads) > 1:
        raise ValueError('All cases must use the same workload except concurrency')
    groups = []
    for concurrency in sorted(candidates):
        trials = [r for r in rows if r['concurrency'] == concurrency]
        passed = all(r['status'] == 'PASS' for r in trials)
        group = {'concurrency': concurrency, 'all_repeats_passed': passed}
        if passed:
            group['worst_p99_ms'] = {m: max(r['p99_ms'][m] for r in trials) for m in limits}
            group['min_output_tokens_per_second'] = min(r['output_tokens_per_second'] for r in trials)
        groups.append(group)
    complete = all(r['status'] != 'INVALID' for r in rows)
    eligible = [g for g in groups if g['all_repeats_passed']] if complete else []
    largest = max(eligible, key=lambda g: g['concurrency']) if eligible else None
    fastest = max(eligible, key=lambda g: (g['min_output_tokens_per_second'], -g['concurrency'])) if eligible else None
    return {'policy': policy, 'complete': complete, 'trials': rows, 'candidates': groups,
            'max_observed_passing_concurrency': largest, 'best_observed_passing_throughput': fastest,
            'search_ceiling_reached': bool(largest and largest['concurrency'] == max(candidates))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True, help='Exact suite-results.json from one run')
    args = parser.parse_args()
    try:
        result = select(json.loads(args.suite.read_text()), json.loads(args.policy.read_text()), args.results)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')
    output = args.results.parent / 'slo-selection.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['max_observed_passing_concurrency'] else 1)


if __name__ == '__main__':
    main()
