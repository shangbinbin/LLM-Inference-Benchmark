import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from aisbench_timing import measured_peak_concurrency
from benchmark_stack import load_config
from run_aisbench_suite import prepare_suite
from slo_search import run_search


class SloSuiteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = load_config(ROOT / 'configs/stacks/mac-slo-search.json')
        self.cfg['storage']['root'] = str(self.root)
        path = ROOT / 'configs/suites/mixed-datasets.json'
        self.suite = prepare_suite(self.cfg, json.loads(path.read_text()), path.parent)
        self.suite.cooldown = 0
        self.policy = json.loads((ROOT / 'configs/search/mac-mixed-datasets.json').read_text())
        self.policy['repeats'] = 2

    def execute(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run_search(self.cfg, self.policy, self.suite)

    def check_search(self, invalid=False):
        calls = []
        def trial(case, concurrency, folder, timeout):
            folder.mkdir()
            calls.append((case.name, concurrency))
            if invalid and case.name == 'support-dialogues':
                raise ValueError('bad measurement')
            return folder / 'results'

        def measure(path, benchmark, concurrency, policy, dataset):
            limit = {'jsonl': 0, 'sharegpt': 1, 'synthetic': 2}[dataset.type if dataset else 'synthetic']
            return {'status': 'PASS' if concurrency <= limit else 'SLO_FAIL',
                    'latencies_ms': {'TTFT': 8, 'TPOT': 4}}

        with patch('slo_search.TrialSession') as session, patch('slo_search.evaluate', side_effect=measure):
            session.return_value.__enter__.return_value.run_trial.side_effect = trial
            code = self.execute()
            self.assertEqual(session.call_count, 1)
        summary_path, = self.root.rglob('suite-results.json')
        summary = json.loads(summary_path.read_text())
        for entry in summary['cases']:
            if entry['status'] != 'skipped':
                self.assertTrue((summary_path.parent / entry['report']).is_file())
        return code, summary, calls

    def test_each_case_has_independent_repeats_and_recommendation(self):
        code, summary, calls = self.check_search()
        self.assertEqual(code, 1)  # First search completed with no feasible candidate.
        self.assertEqual(summary['status'], 'complete')
        self.assertEqual([e['recommended_concurrency'] for e in summary['cases']], [None, 1, 2])
        self.assertEqual(calls, [(c.name, n) for c in self.suite.cases for n in [1, 1, 2, 2]])
        self.assertEqual(summary['cases'][1]['worst_recommended_latencies_ms'], {'TTFT': 8, 'TPOT': 4})

    def test_stops_on_invalid_and_records_remaining_case(self):
        code, summary, _ = self.check_search(invalid=True)
        self.assertEqual(code, 1)
        self.assertEqual([e['status'] for e in summary['cases']], ['complete', 'stopped', 'skipped'])
        self.assertEqual(summary['cases'][1]['stop_reason'], 'invalid_trial')

    def test_continues_after_invalid_when_requested(self):
        self.suite.stop_on_failure = False
        code, summary, _ = self.check_search(invalid=True)
        self.assertEqual(code, 1)
        self.assertEqual(summary['status'], 'stopped')
        self.assertEqual(summary['cases'][-1]['recommended_concurrency'], 2)

    def test_validates_last_case_before_any_trial(self):
        self.suite.cases[-1].cfg['benchmark']['request_rate'] = 5
        with patch('slo_search.TrialSession') as session:
            self.assertEqual(self.execute(), 2)
            session.assert_not_called()
        self.assertFalse(list(self.root.iterdir()))

    def test_multiturn_parallelism_is_bounded_by_independent_conversations(self):
        self.policy['max_concurrency'] = 3
        self.suite.cases = self.suite.cases[1:]
        with patch('slo_search.TrialSession') as session:
            self.assertEqual(self.execute(), 2)
            session.assert_not_called()

    def test_sharegpt_turn_identity_and_timing(self):
        rows = [{'id': i, 'turn_id': t, 'success': True, 'time_points': interval}
                for i, t, interval in [(0, 0, [1, 2]), (1, 0, [1, 2]), (0, 1, [3, 4])]]
        path = self.root/'details.jsonl'

        def write():
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))

        write()
        self.assertEqual(measured_peak_concurrency(path, 3, conversations=2), 2)
        with self.assertRaisesRegex(ValueError, 'Duplicate request'):
            measured_peak_concurrency(path, 3)
        for invalid in (0, 2, None, True):
            rows[2]['turn_id'] = invalid
            write()
            with self.subTest(turn=invalid), self.assertRaises(ValueError):
                measured_peak_concurrency(path, 3, conversations=2)
