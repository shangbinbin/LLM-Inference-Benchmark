import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import benchmark_stack as stack


class StackTests(unittest.TestCase):
    def config(self, tmp):
        cfg=stack.load_config(ROOT/'configs/stacks/mac-simulation.json')
        cfg['storage']={'root':str(Path(tmp)/'state'),'logs':str(Path(tmp)/'logs')}
        return cfg

    def test_vendor_selection_and_topology(self):
        for vendor,metric,job in [('nvidia','DCGM_FI_DEV','nvidia-dcgm-exporter'),('hygon','hcu_','hygon-hcu-exporter')]:
            with self.subTest(vendor=vendor), tempfile.TemporaryDirectory() as tmp:
                cfg=self.config(tmp);cfg['gpu']['vendor']=vendor
                cfg['topology']['kind']='pd'
                cfg['topology']['metrics_endpoints']=[{'target':'router:30000','role':'router'}, {'target':'prefill:30000','role':'prefill'}, {'target':'decode:30000','role':'decode'}]
                cfg['log_files']=['prefill/rank-0.log','decode/rank-0.log']
                folder,chosen=stack.prepare(cfg)
                prom=json.loads((folder/'prometheus/prometheus.yml').read_text())
                self.assertEqual(chosen,vendor)
                self.assertEqual(len(prom['scrape_configs'][0]['static_configs']),3)
                self.assertEqual(prom['scrape_configs'][1]['job_name'],job)
                self.assertIn(metric,(folder/'grafana/dashboards/gpu.json').read_text())
                self.assertIn('/logs/decode/rank-0.log',(folder/'alloy/config.alloy').read_text())
                specs=stack.Stack(cfg,folder,vendor).specs()
                self.assertFalse(any(s['name']=='gpu-exporter' for s in specs))
                self.assertNotIn('docker.sock',str(specs))
                self.assertEqual([s['name'] for s in specs][-3:],['alloy','prometheus','grafana'])
                compile((folder/'aisbench.py').read_text(), 'aisbench.py','exec')

    def test_auto_detection_does_not_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp);cfg['simulation']=False;cfg['gpu']['vendor']='auto'
            with patch('benchmark_stack.shutil.which',return_value=None):
                with self.assertRaisesRegex(ValueError,'Cannot uniquely detect'):
                    stack.detect_vendor(cfg)

    def test_no_inference_server_in_production_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp);cfg['simulation']=False
            directory,vendor=stack.prepare(cfg)
            specs=stack.Stack(cfg,directory,vendor).specs()
            self.assertNotIn('fixture',[s['name'] for s in specs])
            self.assertIn('--gpus',specs[0]['args'])

    def test_failed_readiness_never_starts_benchmark(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp);folder,vendor=stack.prepare(cfg);s=stack.Stack(cfg,folder,vendor)
            result=type('Result',(),{'returncode':1})()
            with patch.object(s,'call',return_value=result), patch.object(s,'build_one'), patch.object(s,'inspect',return_value=None), patch.object(s,'probe',side_effect=RuntimeError('not ready')), patch.object(s,'bench') as bench:
                with self.assertRaisesRegex(RuntimeError,'not ready'):
                    s.up()
                bench.assert_not_called()
                self.assertEqual(json.loads(s.statefile.read_text())['status'],'failed')

    def test_down_refuses_unowned_containers(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp);folder,vendor=stack.prepare(cfg);s=stack.Stack(cfg,folder,vendor)
            s.created=['someone-elses'];s.save('failed')
            with patch.object(s,'inspect',return_value={'Config':{'Labels':{}}}), patch.object(s,'call') as call:
                with self.assertRaisesRegex(ValueError,'unowned'):
                    s.down()
                call.assert_not_called()

    def test_wrong_image_architecture_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp);cfg['platform']='linux/amd64'
            folder,vendor=stack.prepare(cfg);s=stack.Stack(cfg,folder,vendor)
            result=type('Result',(),{'returncode':0,'stdout':'[{"Os":"linux","Architecture":"arm64"}]'})()
            with patch.object(s,'call',return_value=result):
                with self.assertRaisesRegex(ValueError,'architecture mismatch'):
                    s.check_image('test-image')

    def test_host_metrics_uses_host_network_and_real_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp);cfg.update(simulation=False,host_metrics='host',node_exporter_target='192.0.2.10:9100')
            folder,vendor=stack.prepare(cfg)
            specs=stack.Stack(cfg,folder,vendor).specs()
            node=next(s for s in specs if s['name']=='node-exporter')
            self.assertTrue(node['host_network'])
            self.assertIn('--pid',node['args'])
            prom=json.loads((folder/'prometheus/prometheus.yml').read_text())
            self.assertEqual(prom['scrape_configs'][-1]['static_configs'][0]['targets'],['192.0.2.10:9100'])

    def test_sweep_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=stack.load_config(ROOT/'configs/stacks/mac-simulation.json')
            cfg['benchmark'].update(concurrency_levels=[1,2],requests_per_level=[4])
            path=Path(tmp)/'config.json';path.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError,'paired'):
                stack.load_config(path)
            cfg['benchmark']['requests_per_level']=[4,8]
            path.write_text(json.dumps(cfg));stack.load_config(path)
            cfg['benchmark']['retry']=0
            path.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError,'attempt count'):
                stack.load_config(path)


if __name__=='__main__':unittest.main()
