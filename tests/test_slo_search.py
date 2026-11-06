import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from benchmark_stack import load_config
from aisbench_trial import TrialSession
from slo_search import DEFAULTS, evaluate, search, validate_policy


class SearchTests(unittest.TestCase):
    def policy(self, **changes):
        return dict(DEFAULTS, min_concurrency=1, max_concurrency=6, repeats=2,
                    thresholds_ms={'TTFT': 200, 'TPOT': 50}, **changes)

    def run_search(self, predicate, policy=None):
        calls = []
        def measure(c, repeat, round_number):
            calls.append((c, repeat))
            return {'status': 'PASS' if predicate(c, repeat) else 'SLO_FAIL'}
        result = search(policy or self.policy(), measure, sleep=lambda _: None)
        return result, calls

    def test_binary_then_verify_reports_exact_grid_maximum(self):
        result, calls = self.run_search(lambda c, _: c <= 2)
        self.assertEqual([r['concurrency'] for r in result['rounds']], [1, 6, 3, 2, 5, 4])
        self.assertEqual(result['recommended_concurrency'], 2)
        self.assertTrue(result['maximum_verified'])
        self.assertEqual(len(calls), 12)

    def test_nonmonotonic_recovery_even_if_lowest_fails(self):
        result, _ = self.run_search(lambda c, _: c == 4)
        self.assertEqual(result['recommended_concurrency'], 4)
        self.assertTrue(result['maximum_verified'])

    def test_verified_search_finds_max_for_every_six_point_truth_table(self):
        for mask in range(64):
            passing = {c for c in range(1, 7) if mask & (1 << (c - 1))}
            result, _ = self.run_search(lambda c, _: c in passing)
            self.assertEqual(result['recommended_concurrency'], max(passing) if passing else None, mask)
            self.assertTrue(result['maximum_verified'], mask)

    def test_fast_binary_is_explicitly_conditional(self):
        p = self.policy();p['strategy'] = 'binary'
        result, _ = self.run_search(lambda c, _: c <= 2, p)
        self.assertEqual(len(result['rounds']), 4)
        self.assertEqual(result['recommended_concurrency'], 2)
        self.assertFalse(result['maximum_verified'])
        self.assertTrue(result['conditional_on_monotonicity'])

    def test_repeats_require_every_trial_to_pass(self):
        result, _ = self.run_search(lambda c, r: c == 1 or (c == 2 and r == 1))
        self.assertEqual(result['recommended_concurrency'], 1)
        round_two = next(r for r in result['rounds'] if r['concurrency'] == 2)
        self.assertEqual([t['status'] for t in round_two['trials']], ['PASS', 'SLO_FAIL'])

    def test_ceiling_passes_and_no_feasible_candidate(self):
        passed, _ = self.run_search(lambda *_: True)
        self.assertEqual(passed['recommended_concurrency'], 6)
        self.assertEqual(passed['stop_reason'], 'upper_bound_passed')
        failed, _ = self.run_search(lambda *_: False)
        self.assertEqual(failed['stop_reason'], 'no_feasible_concurrency')
        self.assertTrue(failed['maximum_verified'])
        self.assertIsNone(failed['recommended_concurrency'])

    def test_round_budget_never_presents_provisional_result_as_max(self):
        p = self.policy();p['max_rounds'] = 1
        result, _ = self.run_search(lambda *_: True, p)
        self.assertEqual(result['best_observed_concurrency'], 1)
        self.assertEqual(result['stop_reason'], 'round_budget')
        self.assertIsNone(result['recommended_concurrency'])

    def test_time_budget_during_cooldown(self):
        now = [0]
        p = self.policy();p.update(max_duration_seconds=5, cooldown_seconds=5)
        result = search(p, lambda *_: {'status': 'PASS'}, clock=lambda: now[0], sleep=lambda _: self.fail('Must stop before sleeping'))
        self.assertEqual(result['trial_count'], 1)
        self.assertEqual(result['stop_reason'], 'time_budget')
        self.assertIsNone(result['best_observed_concurrency'])

    def test_invalid_timeout_and_interrupt_stop_without_recommendation(self):
        for error, reason in [(ValueError('missing report'), 'invalid_trial'),
                              (subprocess.TimeoutExpired('docker', 1), 'trial_timeout'),
                              (KeyboardInterrupt(), 'interrupted')]:
            with self.subTest(reason=reason):
                def measure(*_):
                    raise error
                result = search(self.policy(), measure)
                self.assertEqual(result['stop_reason'], reason)
                self.assertIsNone(result['recommended_concurrency'])
                self.assertEqual(result['trial_count'], 1)

    def test_grid_and_policy_validation(self):
        cfg = load_config(ROOT / 'configs/stacks/mac-slo-search.json')
        p = json.loads((ROOT / 'configs/search/mac-slo.json').read_text())
        validate_policy(p, cfg)
        for change in [{'max_concurrency': 5, 'step': 3}, {'repeats': 0},
                       {'thresholds_ms': {'TTFT': float('nan')}}, {'unknown': 1},
                       {'max_concurrency': 100}, {'trial_timeout_seconds': 0}]:
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):
                validate_policy(dict(p, **change), cfg)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.results = Path(self.temp.name)
        self.folder = self.results / 'run/performances/aisbench-target'
        self.folder.mkdir(parents=True)
        self.common = {'Total Requests': {'total': 12}, 'Success Requests': {'total': 12},
                       'Failed Requests': {'total': 0}, 'Max Concurrency': {'total': 2},
                       'Output Token Throughput': {'total': '100 token/s'}}
        self.csv = 'Performance Parameters,Stage,P99,Min,Max,N\nTTFT,total,200 ms,0 ms,200 ms,12\nTPOT,total,10 ms,0 ms,10 ms,12\nOutputTokens,total,16,16,16,12\n'
        self.benchmark = {'request_count': 12, 'output_length': 16}
        self.policy = {'min_requests': 12, 'statistic': 'P99', 'thresholds_ms': {'TTFT': 200, 'TPOT': 50}}

    def tearDown(self):
        self.temp.cleanup()

    def check(self):
        (self.folder / 'synthetic.json').write_text(json.dumps(self.common))
        (self.folder / 'synthetic.csv').write_text(self.csv)
        return evaluate(self.results, self.benchmark, 2, self.policy)

    def test_exact_threshold_passes_and_violations_are_named(self):
        self.assertEqual(self.check()['status'], 'PASS')
        self.csv = self.csv.replace('200 ms', '201 ms')
        result = self.check()
        self.assertEqual(result['status'], 'SLO_FAIL')
        self.assertEqual(result['violations'], ['TTFT'])

    def test_bad_report_never_becomes_a_latency_failure(self):
        original = self.csv
        for altered in [original.replace('200 ms', 'NaN ms'), original.replace('200 ms', '0.2 s'),
                        original.replace('16,16,16,12', '16,1,16,12'),
                        original.replace('TTFT,total', 'TTFT,steady'),
                        original.replace('200 ms,12', '200 ms,11')]:
            self.csv = altered
            with self.subTest(csv=altered), self.assertRaises(ValueError):
                self.check()
        self.csv = original
        for key, val in [('Max Concurrency', 1), ('Failed Requests', 1), ('Total Requests', 11)]:
            saved = self.common[key]
            self.common[key] = {'total': val}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.check()
            self.common[key] = saved


class LifecycleTests(unittest.TestCase):
    def test_cleanup_refuses_another_trials_container(self):
        session = TrialSession.__new__(TrialSession)
        session.cfg = {'project': 'mine'}
        session.inspect = Mock(return_value={'Config': {'Labels': {'io.llm-benchmark.project': 'mine',
                                                                  'io.llm-benchmark.project.trial': 'different'}}})
        session.call = Mock()
        with self.assertRaisesRegex(RuntimeError, 'ownership'):
            session.remove_owned('some-client', 'expected')
        session.call.assert_not_called()

    def test_expired_deadline_cannot_start_a_runtime_command(self):
        session = TrialSession.__new__(TrialSession)
        session.cfg = {'runtime': ['docker']}
        session.deadline = 0
        with patch('aisbench_trial.subprocess.run') as run, self.assertRaises(TimeoutError):
            session.call('create', 'image')
        run.assert_not_called()

    def test_existing_suite_lock_is_not_replaced(self):
        session = TrialSession.__new__(TrialSession)
        with tempfile.TemporaryDirectory() as tmp:
            session.lock = Path(tmp) / 'suite.lock'
            session.lock.write_text('another active suite')
            session.run_dir = Path(tmp)
            with self.assertRaises(FileExistsError):
                session.__enter__()
            self.assertEqual(session.lock.read_text(), 'another active suite')


if __name__ == '__main__':
    unittest.main()
