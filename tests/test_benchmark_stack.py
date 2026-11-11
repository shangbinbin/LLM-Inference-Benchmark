import importlib.util
import configparser
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
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
            exporter = next(s for s in specs if s['name'] == 'gpu-exporter')
            self.assertIn('--runtime=nvidia', exporter['args'])
            self.assertIn('NVIDIA_VISIBLE_DEVICES=all', exporter['args'])
            self.assertIn('NVIDIA_DRIVER_CAPABILITIES=compute,utility', exporter['args'])
            self.assertIn('SYS_ADMIN', exporter['args'])
            self.assertNotIn('--gpus', exporter['args'])
            for spec in specs:
                if spec['name'] != 'gpu-exporter':
                    self.assertNotIn('--runtime=nvidia', spec['args'])

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

    def test_offline_aggregates_all_missing_before_creating_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp);cfg['offline']=True
            folder,vendor=stack.prepare(cfg);s=stack.Stack(cfg,folder,vendor)
            missing={cfg['images'][k] for k in ('toolbox','aisbench','grafana')}
            inspected=[]
            def call(*args, **kwargs):
                if args == ('info',):
                    return subprocess.CompletedProcess(args,0,'','')
                self.assertEqual(args[:2],('image','inspect'))
                inspected.append(args[2])
                if args[2] in missing:
                    return subprocess.CompletedProcess(args,1,'','No such image: '+args[2])
                return subprocess.CompletedProcess(args,0,'[{"Os":"linux","Architecture":"arm64"}]','')
            with patch.object(s,'call',side_effect=call), patch.object(s,'build_one') as build:
                with self.assertRaises(ValueError) as error:
                    s.up()
                for image in missing:
                    self.assertIn(image,str(error.exception))
                self.assertIn('Missing images (3)',str(error.exception))
                self.assertEqual(len(inspected),7)
                build.assert_not_called()
            self.assertFalse(s.statefile.exists())

    def test_offline_preflight_respects_selected_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp);cfg.update(offline=True,simulation=False)
            cfg['gpu']['vendor']='hygon';cfg['platform']='linux/amd64'
            folder,vendor=stack.prepare(cfg);s=stack.Stack(cfg,folder,vendor)
            result=subprocess.CompletedProcess([],0,'[{"Os":"linux","Architecture":"amd64"}]','')
            with patch.object(s,'call',return_value=result) as call:
                s.preflight_images(False)
                images=[c.args[2] for c in call.call_args_list]
                self.assertIn(cfg['images']['hygon_exporter'],images)
                self.assertNotIn(cfg['images']['nvidia_exporter'],images)
                self.assertNotIn(cfg['images']['aisbench'],images)
            cfg['gpu'].update(deployment='external')
            with patch.object(s,'call',return_value=result) as call:
                s.preflight_images(False)
                self.assertNotIn(cfg['images']['hygon_exporter'],[c.args[2] for c in call.call_args_list])

    def test_offline_distinguishes_inspection_error_and_wrong_architecture(self):
        cfg={'platform':'linux/amd64'}
        results=[subprocess.CompletedProcess([],1,'','daemon unavailable'),
                 subprocess.CompletedProcess([],0,'[{"Os":"linux","Architecture":"arm64"}]','')]
        with self.assertRaises(ValueError) as error:
            stack.check_local_images(cfg,{'one':['a'],'two':['b']},lambda *a,**k: results.pop(0))
        self.assertIn('Image inspection errors',str(error.exception))
        self.assertIn('Image architecture mismatch',str(error.exception))
        self.assertNotIn('Missing images',str(error.exception))

    def test_offline_build_never_downloads_even_with_empty_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp);cfg['offline']=True
            folder,vendor=stack.prepare(cfg);s=stack.Stack(cfg,folder,vendor)
            with patch('benchmark_stack.urllib.request.urlopen') as download, patch.object(s,'call') as call:
                for name in ('toolbox','aisbench'):
                    with self.assertRaisesRegex(ValueError,'Offline mode forbids'):
                        s.build_one(name)
                download.assert_not_called();call.assert_not_called()
                self.assertFalse((s.root/'build').exists())

    def test_offline_policy_and_component_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp);cfg['offline']=True
            folder,vendor=stack.prepare(cfg);s=stack.Stack(cfg,folder,vendor)
            ini=configparser.ConfigParser();ini.read(folder/'grafana/grafana.ini')
            for key in ('reporting_enabled','check_for_updates','check_for_plugin_updates'):
                self.assertFalse(ini.getboolean('analytics',key))
            self.assertTrue(ini.getboolean('plugins','public_key_retrieval_disabled'))
            self.assertTrue(ini.getboolean('plugins','preinstall_disabled'))
            self.assertFalse(ini.getboolean('news','news_feed_enabled'))
            alloy=next(spec for spec in s.specs() if spec['name']=='alloy')
            self.assertIn('--disable-reporting',alloy['command'])
            self.assertIn('HF_HUB_DISABLE_TELEMETRY=1',stack.aisbench_environment(cfg))
            cfg['runtime']=['nerdctl','--namespace','benchmark']
            for action in ('run','create'):
                cmd=stack.runtime_command(cfg,[action,'some-image'])
                self.assertEqual(cmd,['nerdctl','--namespace','benchmark',action,'--pull=never','some-image'])
            for action in ('build','pull'):
                with self.assertRaisesRegex(ValueError,'Offline mode forbids'):
                    stack.runtime_command(cfg,[action,'some-image'])
            cfg['offline']=False
            self.assertNotIn('--pull=never',stack.runtime_command(cfg,['run','some-image']))
            folder,_=stack.prepare(cfg)
            self.assertNotIn('preinstall_disabled',(folder/'grafana/grafana.ini').read_text())

    def test_offline_requires_boolean(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=json.loads((ROOT/'configs/stacks/mac-simulation.json').read_text());cfg['offline']='true'
            path=Path(tmp)/'cfg.json';path.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError,'JSON boolean'):
                stack.load_config(path)


if __name__=='__main__':unittest.main()
