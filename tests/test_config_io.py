import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from config_io import read_data, read_object, write_yaml
from benchmark_stack import validate_config, prepare
from create_site_config import create_site


class YAMLConfigTests(unittest.TestCase):
    def test_comments_unicode_string_boolean_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '配置.yaml'
            path.write_text('# 核心修改项\nproject: 单机\noffline: true\nlabel: "off"\nvalue: 1.0e-5\n')
            value = read_object(path)
            self.assertIs(value['offline'], True)
            self.assertEqual(value['label'], 'off')
            self.assertEqual(value['value'], 1e-5)
            write_yaml(path, value)
            self.assertEqual(read_object(path), value)
            self.assertFalse(path.read_text().startswith('{'))

    def test_rejects_duplicate_keys_malformed_yaml_and_python_objects(self):
        for text in ('a: 1\na: 2\n', 'a: {b: 1, b: 2}', 'a: [',
                     '!!python/object/apply:os.system ["false"]', 'a: 1\n---\nb: 2'):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'bad.yaml'
                path.write_text(text)
                with self.assertRaises(ValueError):
                    read_object(path)

    def test_legacy_json_and_non_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'legacy.json'
            path.write_text('{"offline": true, "rate": 1e-5}')
            self.assertEqual(read_object(path), {'offline': True, 'rate': 1e-5})
            path = path.with_suffix('.yml')
            path.write_text('- one\n- two')
            self.assertEqual(read_data(path), ['one', 'two'])
            with self.assertRaisesRegex(ValueError, 'mapping'):
                read_object(path)

    def test_all_repository_configs_parse_and_only_data_stays_json(self):
        for path in (ROOT / 'configs').rglob('*.yaml'):
            with self.subTest(path=path):
                self.assertIsInstance(read_object(path), dict)
        for path in (ROOT / 'configs').rglob('*.json'):
            self.assertIn('fixtures', path.parts, str(path))

    def test_single_node_example_renders_without_gpu_or_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = read_object(ROOT / 'configs/sites/single-node.example.yaml')
            self.assertFalse(cfg['simulation'])
            self.assertTrue(cfg['offline'])
            self.assertEqual(cfg['gpu']['nodes'][0]['name'], cfg['log_sources'][0]['node'])
            cfg['storage'].update(root=str(Path(tmp)/'state'), tokenizer=str(ROOT/'configs/fixtures/mock-tokenizer'))
            folder, _ = prepare(validate_config(cfg))
            self.assertTrue((folder/'prometheus/prometheus.yml').read_text().startswith('global:'))
            self.assertIsInstance(json.loads((folder/'grafana/dashboards/task-overview.json').read_text()), dict)

    def test_site_creation_reads_legacy_json_but_defaults_to_yaml_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = read_object(ROOT / 'configs/sites/single-node.example.yaml')
            source, output = Path(tmp)/'old.json', Path(tmp)/'site.yaml'
            source.write_text(json.dumps(cfg))
            create_site(source, output)
            self.assertEqual(read_object(output), cfg)
            self.assertTrue(output.read_text().startswith('schema_version:'))
