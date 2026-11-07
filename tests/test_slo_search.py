import csv
import copy
import sqlite3
import struct
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
        self.details = [{'id': i, 'success': True, 'time_points': [float(i // 2), i // 2 + 0.5]}
                        for i in range(12)]
        self.benchmark = {'request_count': 12, 'output_length': 16}
        self.policy = {'min_requests': 12, 'statistic': 'P99', 'thresholds_ms': {'TTFT': 200, 'TPOT': 50}}

    def tearDown(self):
        self.temp.cleanup()

    def check(self):
        (self.folder / 'synthetic.json').write_text(json.dumps(self.common))
        (self.folder / 'synthetic.csv').write_text(self.csv)
        (self.folder / 'synthetic_details.jsonl').write_text(
            ''.join(json.dumps(item) + '\n' for item in self.details))
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

    def test_configured_concurrency_cannot_prove_observed_concurrency(self):
        # The summary still advertises batch_size=2, but requests ran serially.
        self.details = [{'id': i, 'success': True, 'time_points': [i, i + 1]}
                        for i in range(12)]
        with self.assertRaisesRegex(ValueError, 'Measured peak concurrency 1.*requested 2'):
            self.check()

    def test_concurrency_evidence_is_recorded_and_input_order_does_not_matter(self):
        self.details.reverse()
        result = self.check()
        self.assertEqual(result['configured_concurrency'], 2)
        self.assertEqual(result['measured_peak_concurrency'], 2)
        self.assertTrue(result['concurrency_evidence'].endswith('synthetic_details.jsonl'))

    def test_staggered_requests_are_measured_by_overlap(self):
        self.details = [{'id': i, 'success': True, 'time_points': [i, i + 1.5]}
                        for i in range(12)]
        self.assertEqual(self.check()['measured_peak_concurrency'], 2)

    def test_shared_endpoint_is_not_an_overlap(self):
        self.details = [{'id': i, 'success': True, 'time_points': [i, i + 1]}
                        for i in range(12)]
        with self.assertRaises(ValueError):
            self.check()

    def test_over_target_measurement_is_invalid(self):
        for item in self.details:
            item['time_points'] = [0, 1]
        with self.assertRaisesRegex(ValueError, 'Measured peak concurrency 12.*requested 2'):
            self.check()

    def test_bad_request_evidence_is_invalid(self):
        original = copy.deepcopy(self.details)
        cases = [original[:-1], original + [original[0]],
                 [dict(item, id=0) for item in original]]
        for values in ([0], [1, 1], [2, 1], [0, 2, 1], [0, float('nan')],
                       [0, float('inf')], [-1, 1], [False, 1], ['0', 1], None):
            altered = copy.deepcopy(original)
            altered[0]['time_points'] = values
            cases.append(altered)
        for changes in ({'success': False}, {'id': True}, {'id': None}, {'id': -1}):
            altered = copy.deepcopy(original)
            altered[0].update(changes)
            cases.append(altered)
        for details in cases:
            with self.subTest(details=details), self.assertRaises(ValueError):
                self.details = details
                self.check()

    def test_missing_and_malformed_details_fail_closed(self):
        self.check()
        path = self.folder / 'synthetic_details.jsonl'
        for text in ('{broken json', '[]', '\n'):
            path.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                evaluate(self.results, self.benchmark, 2, self.policy)
        path.unlink()
        with self.assertRaises(ValueError):
            evaluate(self.results, self.benchmark, 2, self.policy)

    @staticmethod
    def npy_blob(points, descr='<f8'):
        # Format emitted by np.save(..., allow_pickle=False), without NumPy on the host.
        header = repr({'descr': descr, 'fortran_order': False, 'shape': (len(points),)})
        header += ' ' * (-(10 + len(header) + 1) % 64) + '\n'
        return (b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header.encode('ascii')
                + struct.pack(descr[0] + 'd' * len(points), *points))

    def write_db_details(self, descr='<f8'):
        db_dir = self.folder / 'db_data'
        db_dir.mkdir(exist_ok=True)
        db = db_dir / 'timings.db'
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE numpy_store (id INTEGER PRIMARY KEY, arr_blob BLOB NOT NULL)')
            for i, item in enumerate(self.details, 1):
                connection.execute('INSERT INTO numpy_store VALUES (?, ?)',
                                   (i, self.npy_blob(item['time_points'], descr)))
                item.update(time_points={'__db_ref__': i}, db_name=db.name)
        return db

    def test_pinned_aisbench_sqlite_numpy_timestamps(self):
        db = self.write_db_details()
        before = db.read_bytes()
        result = self.check()
        self.assertEqual(result['measured_peak_concurrency'], 2)
        self.assertEqual(db.read_bytes(), before)

    def test_big_endian_timestamp_arrays(self):
        self.write_db_details('>f8')
        self.assertEqual(self.check()['measured_peak_concurrency'], 2)

    def test_database_symlink_cannot_escape_report(self):
        db = self.write_db_details()
        outside = self.results / 'outside.db'
        db.rename(outside)
        db.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'inside the report'):
            self.check()

    def test_database_directory_symlink_cannot_escape_report(self):
        db = self.write_db_details()
        outside = self.results / 'outside'
        db.parent.rename(outside)
        db.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'inside the report'):
            self.check()

    def test_corrupt_or_missing_database_schema_is_invalid(self):
        db = self.write_db_details()
        with sqlite3.connect(db) as connection:
            connection.execute('DROP TABLE numpy_store')
        with self.assertRaisesRegex(ValueError, 'Cannot read request timing evidence'):
            self.check()
        db.write_bytes(b'not-a-sqlite-database')
        with self.assertRaisesRegex(ValueError, 'Cannot read request timing evidence'):
            self.check()

    def test_missing_db_is_not_created(self):
        self.details[0].update(time_points={'__db_ref__': 1}, db_name='missing.db')
        with self.assertRaises(ValueError):
            self.check()
        self.assertFalse((self.folder / 'db_data/missing.db').exists())

    def test_bad_database_references_are_invalid(self):
        db = self.write_db_details()
        original = copy.deepcopy(self.details)
        for changes in ({'db_name': '../timings.db'}, {'db_name': str(db.resolve())},
                        {'db_name': None}, {'time_points': {'__db_ref__': 999}},
                        {'time_points': {'__db_ref__': True}},
                        {'time_points': {'__db_ref__': 2}}):
            self.details = copy.deepcopy(original)
            self.details[0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.check()

    def test_corrupt_or_unsupported_numpy_blobs_fail_closed(self):
        db = self.write_db_details()
        valid = self.npy_blob([0, 1])
        bad_blobs = [b'not-npy', valid[:-1], valid + b'X',
                     valid.replace(b'<f8', b'|O8'), valid.replace(b'(2,)', b'(3,)'),
                     valid.replace(b'(2,)', b'(1,2)'), valid.replace(b'False', b'True '),
                     valid[:6] + b'\x02\x00' + valid[8:],
                     valid[:8] + b'\xff\xff' + valid[10:],
                     valid.replace(b'\'descr\'', b'\'broken'),
                     valid[:10] + b'\xff' + valid[11:]]
        for blob in bad_blobs:
            with sqlite3.connect(db) as connection:
                connection.execute('UPDATE numpy_store SET arr_blob=? WHERE id=1', (blob,))
            with self.subTest(blob=blob), self.assertRaises(ValueError):
                self.check()

    def test_invalid_concurrency_stops_search_without_recommendation(self):
        self.details = [{'id': i, 'success': True, 'time_points': [i, i + 1]}
                        for i in range(12)]
        policy = dict(DEFAULTS, min_concurrency=2, max_concurrency=2, repeats=1,
                      min_requests=12, thresholds_ms={'TTFT': 200})
        result = search(policy, lambda *_: self.check())
        self.assertEqual(result['stop_reason'], 'invalid_trial')
        self.assertEqual(result['rounds'][0]['status'], 'INVALID')
        self.assertIsNone(result['recommended_concurrency'])
        self.assertFalse(result['maximum_verified'])


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
