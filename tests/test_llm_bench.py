import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'bin/llm-bench'


class CommandLineTests(unittest.TestCase):
    def cli(self, cwd, *args):
        return subprocess.run([CLI, *args], cwd=cwd, text=True, capture_output=True)

    def test_linux_flags_resolve_paths_and_complete_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'tokenizer').symlink_to(ROOT/'configs/fixtures/mock-tokenizer')
            result = self.cli(tmp, 'plan', '--profile', 'linux-single', '--model', 'served-model',
                              '--endpoint', 'http://inference.internal:8080', '--host-address', 'host.internal',
                              '--gpu-vendor', 'hygon', '--gpus', '8', '--tokenizer', 'tokenizer',
                              '--data-dir', 'data', '--log-dir', 'logs', '--offline')
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            cfg = plan['config']
            self.assertEqual(cfg['storage']['root'], str((path/'data').resolve()))
            self.assertEqual(cfg['platform'], 'linux/amd64')
            self.assertTrue(cfg['legacy_seccomp_unconfined'])
            self.assertEqual(cfg['topology']['metrics_endpoints'][0]['target'], 'inference.internal:8080')
            self.assertEqual(cfg['node_exporter_target'], 'host.internal:39100')
            self.assertEqual(plan['gpu_vendor'], 'hygon')
            self.assertIn('gpu-exporter', plan['services'])

    def test_overlay_does_not_discard_other_settings_or_override_explicit_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)/'overlay.json'
            site.write_text(json.dumps({'legacy_seccomp_unconfined': False, 'benchmark': {'input_length': 48, 'concurrency': 3},
                'topology': {'metrics_endpoints': [{'target': 'metrics:9000', 'role': 'worker'}]}}))
            result = self.cli(tmp, 'plan', '--profile', 'mac-offline', '--config', site, '--concurrency', '4',
                              '--endpoint', 'http://fixture:18000', '--data-dir', 'state', '--log-dir', 'logs',
                              '--cases', ROOT/'configs/suites/mac-smoke.json')
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            cfg = plan['config']
            self.assertTrue(cfg['offline'])
            self.assertFalse(cfg['legacy_seccomp_unconfined'])
            self.assertEqual(cfg['benchmark']['input_length'], 48)
            self.assertEqual(cfg['benchmark']['concurrency'], 4)
            self.assertEqual(cfg['topology']['metrics_endpoints'][0]['target'], 'metrics:9000')
            self.assertEqual(plan['cases'][0]['dataset'], {'type': 'synthetic'})
            self.assertEqual(plan['workloads'], ['short-string', 'random-tokenid', 'sustained-pressure'])

    def test_ambiguous_working_directory_requires_explicit_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            markers = folder/'.llm-bench'
            markers.mkdir()
            for name in ('first', 'second'):
                root = folder/name
                root.mkdir()
                (root/'state.json').write_text(json.dumps({'project': name}))
                (markers/(name+'.json')).write_text(json.dumps({'root': str(root), 'project': name}))
            result = self.cli(tmp, 'down')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('2 stacks', result.stderr)
            self.assertTrue(all((folder/name/'state.json').exists() for name in ('first', 'second')))
