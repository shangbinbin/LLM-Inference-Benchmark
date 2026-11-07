import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from benchmark_stack import ais_config, load_config
from run_aisbench_suite import prepare_suite


class SuiteDatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.cfg = load_config(ROOT / 'configs/stacks/mac-offline.json')

    def case(self, spec, **benchmark):
        raw = {'cases': [{'name': 'example', 'dataset': spec, 'benchmark': benchmark}]}
        return prepare_suite(self.cfg, raw, self.directory).cases[0]

    def test_mixed_example_and_legacy_suites(self):
        directory = ROOT / 'configs/suites'
        for filename, expected in [('mixed-datasets.json', [2, 3, 4]), ('mac-smoke.json', [4, 4, 4])]:
            raw = json.loads((directory / filename).read_text())
            cases = prepare_suite(self.cfg, raw, directory).cases
            self.assertEqual([case.cfg['benchmark']['request_count'] for case in cases], expected)
            self.assertEqual([case.name for case in cases], [r['name'] for r in raw['cases']])
            for case in cases:
                compile(ais_config(case.cfg, case.dataset), 'aisbench.py', 'exec')
        self.assertEqual(self.cfg['benchmark']['request_count'], 8)
        self.assertEqual(self.cfg['inference']['api'], 'completions')

    def test_local_path_and_prefix_selection(self):
        path = self.directory / 'data, with spaces.jsonl'
        path.write_text('{"prompt":"a"}\n{"prompt":"b"}\n')
        case = self.case({'type': 'jsonl', 'path': path.name}, request_count=1)
        self.assertEqual(case.dataset.records, 1)
        self.assertEqual(case.dataset.prompt_field, 'prompt')
        self.assertEqual(next(csv.reader([case.dataset.mount()])), [
            'type=bind', 'src=' + str(path.resolve()), 'dst=/datasets/input.jsonl', 'readonly'])
        self.assertNotIn('input_length', case.cfg['benchmark'])
        with self.assertRaisesRegex(ValueError, 'exceeds 2'):
            self.case({'type': 'jsonl', 'path': path.name}, request_count=3)

    def test_conversations_are_never_split(self):
        path = ROOT / 'configs/fixtures/datasets/support-chat.json'
        spec = {'type': 'sharegpt', 'path': str(path)}
        case = self.case(spec, request_count=2)
        self.assertEqual((case.dataset.records, case.dataset.requests), (1, 2))
        self.assertEqual(case.cfg['inference']['api'], 'chat')
        self.assertIn('"[:1]"', ais_config(case.cfg, case.dataset))
        with self.assertRaisesRegex(ValueError, 'splits a ShareGPT conversation'):
            self.case(spec, request_count=1)

    def test_invalid_sources_are_rejected(self):
        path = self.directory / 'input'
        for kind, contents, error in [
                ('jsonl', '', 'empty dataset'),
                ('jsonl', '{"prompt":"valid"}\n[]\n', 'input:2'),
                ('jsonl', '{"prompt":3}\n', 'string field'),
                ('jsonl', 'broken', 'invalid JSONL'),
                ('sharegpt', '{}', 'JSON array'),
                ('sharegpt', '[{}]', 'string id'),
                ('sharegpt', '[{"id":"a","conversations":[]}]', 'complete human/gpt'),
                ('sharegpt', '[{"id":"a","conversations":[{},{}]}]', 'turn 1')]:
            with self.subTest(kind=kind, contents=contents):
                path.write_text(contents)
                with self.assertRaisesRegex(ValueError, error):
                    self.case({'type': kind, 'path': path.name})
        with self.assertRaisesRegex(ValueError, 'only to synthetic'):
            self.case({'type': 'sharegpt', 'path': path.name}, input_length=32)
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            self.case({'type': 'sharegpt', 'path': path.name, 'prompt_field': 'prompt'})

    def test_up_validates_last_case_before_starting_stack(self):
        cases = self.directory / 'cases.json'
        cases.write_text(json.dumps({'cases': [
            {'name': 'valid'}, {'name': 'missing-data', 'dataset': {'type': 'jsonl', 'path': 'missing.jsonl'}}]}))
        state = self.directory / 'state'
        result = subprocess.run([ROOT / 'bin/llm-bench', 'up', '--profile', 'mac-offline',
                                 '--cases', cases, '--data-dir', state], cwd=self.directory,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing-data: dataset file does not exist', result.stderr)
        self.assertFalse(state.exists())
