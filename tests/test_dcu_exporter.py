import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import benchmark_stack as stack
from gpu_metrics import DEVICE_METRICS
from task_dashboard import build_dashboard


class DCUExporterTests(unittest.TestCase):
    def config(self, base):
        cfg=stack.load_config(ROOT/'configs/stacks/mac-offline.json')
        cfg.update(simulation=False,platform='linux/amd64')
        cfg['storage'].update(root=str(base/'state'),logs=str(base/'logs'))
        cfg['gpu'].update(vendor='hygon',deployment='local',nodes=[{'name':'dcu-node','gpus':8}])
        return cfg

    def test_every_current_template_uses_official_v241_image(self):
        for path in [*ROOT.glob('configs/stacks/*.json'),*ROOT.glob('configs/sites/*.json')]:
            cfg=json.loads(path.read_text())
            self.assertEqual(cfg['images']['hygon_exporter'],
                'harbor.sourcefind.cn:5443/hcu/admin/base/dcu-exporter:v2.4.1',str(path))

    def test_port_relabeling_readiness_and_no_hcu_switches_in_both_runtimes(self):
        for runtime in (['docker'],['nerdctl','--namespace','benchmark']):
            with self.subTest(runtime=runtime),tempfile.TemporaryDirectory() as tmp:
                cfg=self.config(Path(tmp));cfg['runtime']=runtime
                cfg['gpu'].update(hygon_exporter_port=8000,hygon_exporter_env={'CUSTOM_LISTEN':'8000'})
                cfg=stack.validate_config(cfg)
                folder,vendor=stack.prepare(cfg)
                instance=stack.Stack(cfg,folder,vendor)
                spec=next(s for s in instance.specs() if s['name']=='gpu-exporter')
                self.assertEqual(spec['ready'],('http://gpu-exporter:8000/metrics','dcu_memorycap_bytes'))
                self.assertIn('CUSTOM_LISTEN=8000',spec['args'])
                self.assertIn('0.0.0.0:39403:8000',spec['args'])
                self.assertNotIn('METRICS_LEVEL=low',spec['args'])
                self.assertNotIn('CONNECT_K8S=false',spec['args'])
                self.assertNotIn('--gpus',spec['args'])
                self.assertNotIn('--runtime=nvidia',spec['args'])
                gpu=json.loads((folder/'prometheus/prometheus.yml').read_text())['scrape_configs'][1]
                self.assertEqual(gpu['job_name'],'hygon-dcu-exporter')
                self.assertEqual(gpu['static_configs'][0]['targets'],['gpu-exporter:8000'])
                relabel=gpu['metric_relabel_configs']
                self.assertTrue(any(r.get('source_labels')==['gpu_model','name'] for r in relabel))
                self.assertTrue(any(r.get('source_labels')==['minor_number'] and r['target_label']=='gpu_index' for r in relabel))

    def test_external_target_stays_explicit_and_does_not_launch_exporter(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(Path(tmp));cfg['gpu']['deployment']='external'
            cfg['gpu']['nodes'][0]['target']='192.0.2.10:8000'
            folder,vendor=stack.prepare(cfg);instance=stack.Stack(cfg,folder,vendor)
            self.assertNotIn('gpu-exporter',[s['name'] for s in instance.specs()])
            gpu=json.loads((folder/'prometheus/prometheus.yml').read_text())['scrape_configs'][1]
            self.assertEqual(gpu['static_configs'][0]['targets'],['192.0.2.10:8000'])
            self.assertEqual(DEVICE_METRICS['hygon'],'dcu_memorycap_bytes')

    def test_contract_and_dashboard_match_official_metric_names(self):
        base=ROOT/'configs/gpu/hygon/dcu-exporter-v2.4.1'
        contract=json.loads((base/'metric-contract.json').read_text())
        dashboard=build_dashboard('hygon')
        expressions=[t['expr'] for p in dashboard['panels'] for t in p.get('targets',[])]
        text='\n'.join(expressions)
        self.assertNotIn('hcu_',text)
        self.assertNotIn('vdcu_',text)
        for name in contract['metrics']:
            self.assertIn(name,text)
        self.assertIn('100 * max by',text)
        self.assertIn('max by (task,gpu_node,gpu_model,gpu_index)',text)
        self.assertNotIn('sum(dcu_',text)
        standalone=(base/'grafana/gpu.json').read_text()
        self.assertNotIn('hcu_',standalone)
        self.assertIn('minor_number',standalone)

    def test_exporter_overrides_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            for value in (0,-1,65536,True,'8000'):
                cfg=self.config(Path(tmp));cfg['gpu']['hygon_exporter_port']=value
                with self.assertRaisesRegex(ValueError,'hygon_exporter_port'):
                    stack.validate_config(cfg)
            for value in ([],{'BAD-NAME':'value'},{'VALUE':123}):
                cfg=self.config(Path(tmp));cfg['gpu']['hygon_exporter_env']=value
                with self.assertRaisesRegex(ValueError,'hygon_exporter_env'):
                    stack.validate_config(cfg)

    def test_cached_toolbox_simulation_mounts_current_dcu_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(Path(tmp));cfg['simulation']=True
            folder,vendor=stack.prepare(cfg)
            fixture=next(s for s in stack.Stack(cfg,folder,vendor).specs() if s['name']=='fixture')
            self.assertIn('type=bind,src='+str(ROOT/'containers/toolbox/mock_server.py')+',dst=/opt/benchmark/mock_server.py,readonly',fixture['args'])
