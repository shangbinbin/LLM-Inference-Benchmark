import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from bundle_support import prepare_tokenizer, simulation_config, write_simulation
from benchmark_stack import Stack, validate_config
from config_io import read_object


class BundleSupportTests(unittest.TestCase):
    def test_verifier_rejects_stack_without_completed_default_run(self):
        from config_io import write_yaml
        from verify_simulation import main as verify
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            site = read_object(ROOT / 'configs/sites/single-node.example.yaml')
            cfg = simulation_config(site, ROOT / 'configs/fixtures/mock-tokenizer', base / 'data',
                                    base / 'logs', project='offline-test', port_base=44000)
            write_yaml(base / 'smoke.yaml', cfg)
            (base / 'data').mkdir()
            (base / 'data/state.json').write_text(json.dumps({'status': 'ready', 'containers': []}))
            with self.assertRaisesRegex(ValueError, 'completed default AISBench'):
                verify(['--config', str(base / 'smoke.yaml')])

    def test_local_assets_filter_weights_and_keep_templates_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / 'source'
            shutil.copytree(ROOT / 'configs/fixtures/mock-tokenizer', source)
            (source / 'model.safetensors').write_bytes(b'not a tokenizer')
            (source / 'chat_templates').mkdir()
            (source / 'chat_templates/tool.jinja').write_text('{{ messages }}')
            prepare_tokenizer(base / 'bundle/tokenizer', base / 'bundle/metadata/tokenizer.json', source)
            delivered = base / 'bundle/tokenizer'
            self.assertFalse((delivered / 'model.safetensors').exists())
            self.assertTrue((delivered / 'chat_templates/tool.jinja').is_file())
            metadata = read_object(base / 'bundle/metadata/tokenizer.json')
            self.assertEqual(metadata['provider'], 'local')
            self.assertEqual(metadata['files']['tokenizer.json'],
                             hashlib.sha256((source / 'tokenizer.json').read_bytes()).hexdigest())
            with self.assertRaises(FileExistsError):
                prepare_tokenizer(delivered, base / 'other.json', source)

    def test_incomplete_or_malformed_assets_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / 'source'; source.mkdir()
            (source / 'tokenizer_config.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'tokenizer.json'):
                prepare_tokenizer(base / 'one', base / 'meta.json', source)
            (source / 'tokenizer.json').write_text('malformed')
            with self.assertRaises(ValueError):
                prepare_tokenizer(base / 'two', base / 'meta.json', source)
            self.assertFalse((base / 'meta.json').exists())

    def test_relocated_simulation_is_cpu_only_and_rejects_active_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            site = read_object(ROOT / 'configs/sites/single-node.example.yaml')
            site.update(runtime=['nerdctl', '--namespace', 'offline'])
            site['inference'].update(api_key_env='REAL_SECRET', base_url='http://real-service:8000/')
            cfg = simulation_config(site, ROOT / 'configs/fixtures/mock-tokenizer', base / 'data',
                                    base / 'logs', project='offline-test', port_base=44000)
            cfg = validate_config(cfg)
            self.assertEqual(cfg['runtime'], site['runtime'])
            self.assertNotIn('REAL_SECRET', json.dumps(cfg))
            self.assertEqual(cfg['inference']['model'], 'mock-model')
            self.assertTrue(cfg['offline'])
            self.assertEqual(cfg['benchmark']['dataset_type'], 'tokenid')
            specs = Stack(cfg, base / 'rendered', 'nvidia').specs()
            self.assertEqual(len(specs), 7)
            self.assertFalse(any(s['name'] == 'gpu-exporter' for s in specs))
            path = base / 'simulation.yaml'
            write_simulation(path, cfg)
            (base / 'data').mkdir(); (base / 'data/state.json').write_text('{}')
            # Also reject replacing a config whose old stack is still active.
            cfg['storage']['root'] = str(base / 'new-data')
            with self.assertRaisesRegex(ValueError, 'active simulation'):
                write_simulation(path, cfg)
            with self.assertRaisesRegex(ValueError, 'SMOKE_PORT_BASE'):
                simulation_config(site, 'tokenizer', 'data', 'logs', project='test', port_base=65530)
