import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('render', ROOT / 'scripts/render_observability.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RenderTests(unittest.TestCase):
    def render(self, name, out, preview=False):
        module.render(ROOT / f'configs/environments/{name}/environment.json', out, preview)

    def test_h200_complete_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            self.render('nvidia-h200-3node-8gpu', out)
            prom = json.loads((out / 'prometheus/prometheus.yml').read_text())
            jobs = prom['scrape_configs']
            self.assertEqual(len(jobs), 3)
            self.assertEqual(len(jobs[0]['static_configs'][0]['targets']), 1)
            self.assertEqual(len(jobs[1]['static_configs']), 3)
            self.assertEqual(jobs[2]['dns_sd_configs'][0]['type'], 'A')
            for rank in range(3):
                log = f'glm52-3node-8gpu-run-01-rank-{rank}.log'
                self.assertIn(log, (out / 'alloy/config.alloy').read_text())
                self.assertIn(log, (out / 'logrotate/inference.conf').read_text())
            self.assertNotIn('@@', (out / 'alloy/config.alloy').read_text())
            self.assertEqual(len(list((out / 'grafana/dashboards').glob('*.json'))), 3)
            self.assertNotIn('!= 10', (out / 'prometheus/rules/network.yml').read_text())
            with self.assertRaises(ValueError):
                self.render('nvidia-h200-3node-8gpu', out)

    def test_hygon_template_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            with self.assertRaisesRegex(ValueError, 'Unfinished template'):
                self.render('hygon-template', out)
            self.assertFalse(out.exists())

    def test_hygon_preview_has_no_nvidia_or_ib(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            self.render('hygon-template', out, True)
            text = '\n'.join(p.read_text() for p in out.rglob('*') if p.is_file())
            self.assertNotIn('DCGM_', text)
            self.assertNotIn('nvidia', text.lower())
            self.assertFalse((out / 'grafana/dashboards/network.json').exists())
            self.assertFalse((out / 'prometheus/rules').exists())
            self.assertIn('TEMPLATE PREVIEW ONLY', text)

    def test_log_validation_and_vendor_boundary(self):
        env = json.loads((ROOT / 'configs/environments/nvidia-h200-3node-8gpu/environment.json').read_text())
        profile = {'vendor': 'nvidia', 'status': 'reference'}
        adapter = {'vendor': 'hygon', 'status': 'reference'}
        with self.assertRaisesRegex(ValueError, 'vendors differ'):
            module.validate(env, profile, adapter, False)
        adapter['vendor'] = 'nvidia'
        env['inference']['log_files'][0] = '/logs/*.log'
        with self.assertRaisesRegex(ValueError, 'Log files'):
            module.validate(env, profile, adapter, False)


if __name__ == '__main__':
    unittest.main()
