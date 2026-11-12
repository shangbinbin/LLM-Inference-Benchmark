import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import benchmark_stack as stack
from observability_labels import log_sources, task_name
from task_dashboard import build_dashboard


class TaskObservabilityTests(unittest.TestCase):
    def config(self, tmp, vendor='nvidia'):
        cfg = stack.load_config(ROOT/'configs/stacks/mac-simulation.json')
        cfg.update(task='model-service-a', simulation=False, platform='linux/amd64')
        cfg['gpu'].update(vendor=vendor, deployment='external')
        cfg['gpu']['nodes'] = [
            {'name':'gpu-node-a','gpus':2,'target':'192.0.2.1:9400','model':'configured-model'},
            {'name':'gpu-node-b','gpus':1,'target':'192.0.2.2:9400'},
        ]
        cfg['storage'].update(root=str(Path(tmp)/'state'), logs=str(Path(tmp)/'logs'))
        cfg['inference']['engine'] = 'vllm'
        cfg['topology']['metrics_endpoints'] = [
            {'target':'replica-a:8000','role':'prefill','replica':'replica-a'},
            {'target':'replica-b:8000','role':'decode','replica':'replica-b'},
        ]
        cfg['log_sources'] = [
            {'path':'prefill/server.log','role':'prefill','replica':'replica-a','node':'gpu-node-a'},
            {'path':'decode/server.log','role':'decode','replica':'replica-b','node':'gpu-node-b'},
        ]
        return cfg

    def test_task_roles_replicas_apply_to_metrics_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            directory, vendor = stack.prepare(cfg)
            prom = json.loads((directory/'prometheus/prometheus.yml').read_text())
            application = prom['scrape_configs'][0]
            self.assertEqual(application['job_name'], 'inference')
            for endpoint, target in zip(cfg['topology']['metrics_endpoints'], application['static_configs']):
                labels = target['labels']
                self.assertEqual(labels['task'], cfg['task'])
                self.assertEqual(labels['instance_role'], endpoint['role'])
                self.assertEqual(labels['replica'], endpoint['replica'])
                self.assertEqual(labels['deployment'], cfg['project'])
            self.assertIn('(vllm|sglang):.*', json.dumps(application['metric_relabel_configs']))
            self.assertTrue(all(any(r.get('target_label')=='task' for r in job['relabel_configs']) for job in prom['scrape_configs']))
            text=(directory/'alloy/config.alloy').read_text()
            for value in ('"task" = "model-service-a"', '"instance_role" = "decode"',
                          '"replica" = "replica-b"', '"source_file" = "prefill/server.log"'):
                self.assertIn(value,text)
            self.assertNotIn('/logs/server.log', text)  # log_sources takes precedence over legacy log_files.
            rotate=(directory/'logrotate/inference.conf').read_text()
            self.assertIn('/logs/prefill/server.log /logs/decode/server.log',rotate)
            self.assertNotIn('/logs/server.log ',rotate)

    def test_gpu_normalization_preserves_native_labels_and_vendor_isolation(self):
        for vendor, native_index in [('nvidia','gpu'),('hygon','minor_number')]:
            with self.subTest(vendor=vendor), tempfile.TemporaryDirectory() as tmp:
                cfg=self.config(tmp,vendor)
                directory,chosen=stack.prepare(cfg)
                prom=json.loads((directory/'prometheus/prometheus.yml').read_text())
                gpu=prom['scrape_configs'][1]
                for node,target in zip(cfg['gpu']['nodes'],gpu['static_configs']):
                    labels=target['labels']
                    self.assertEqual(labels['task'],cfg['task'])
                    self.assertEqual(labels['gpu_node'],node['name'])
                    self.assertEqual(labels['gpu_vendor'],vendor)
                rules=gpu['metric_relabel_configs']
                self.assertTrue(any(r.get('source_labels')==[native_index] and r.get('target_label')=='gpu_index' for r in rules))
                self.assertFalse(any(r.get('action') in ('labeldrop','labelkeep') for r in rules))
                self.assertEqual(gpu['static_configs'][0]['labels']['gpu_model'],'configured-model')
                dashboard=json.loads((directory/'grafana/dashboards/task-overview.json').read_text())
                self.assertEqual(dashboard['uid'],'inference-task-overview')
                text=json.dumps(dashboard)
                self.assertIn('DCGM_FI_DEV_GPU_UTIL' if vendor=='nvidia' else 'hcu_memorycap_bytes',text)
                self.assertNotIn('hcu_' if vendor=='nvidia' else 'DCGM_',text)
                self.assertFalse((directory/'grafana/dashboards/sglang.json').exists())
                self.assertFalse((directory/'grafana/dashboards/gpu.json').exists())

    def test_dashboard_filter_dependencies_and_log_volume(self):
        for vendor in ('nvidia','hygon'):
            dashboard=build_dashboard(vendor,{'task':'a','gpu':{'nvidia_metrics_profile':'basic'}})
            variables={v['name']:v for v in dashboard['templating']['list']}
            for name,parents in [('gpu_node',['task']),('gpu_model',['task','gpu_node']),
                                 ('gpu_index',['task','gpu_node','gpu_model']),('instance_role',['task']),
                                 ('replica',['task','instance_role']),('source_file',['task','instance_role','replica'])]:
                definition=variables[name]['definition']
                for parent in parents:
                    self.assertTrue('${'+parent+':' in definition or '$'+parent in definition)
            self.assertEqual(dashboard['refresh'],'5s')
            # Raw query strings preserve regex escapes in dots, plus signs and model names.
            for panel in dashboard['panels']:
                for target in panel.get('targets',[]):
                    expression=target['expr']
                    for variable in re.findall(r'\$\{(\w+):regex\}',expression):
                        self.assertIn('`${'+variable+':regex}`',expression)
            self.assertIn('${log_search:doublequote}',json.dumps(dashboard))
            volume=next(p for p in dashboard['panels'] if p.get('title')=='Logs Volume')
            self.assertIn('count_over_time',volume['targets'][0]['expr'])
            self.assertEqual(volume['datasource']['uid'],'$loki_datasource')
            logs=next(p for p in dashboard['panels'] if p['type']=='logs')
            for key in ('task','instance_role','replica'):
                self.assertTrue('${'+key+':' in logs['targets'][0]['expr'] or '$'+key in logs['targets'][0]['expr'])
            self.assertIn('vllm:',json.dumps(dashboard))
            self.assertIn('sglang:',json.dumps(dashboard))
            self.assertNotIn('DCGM_FI_PROF_',json.dumps(dashboard))
            if vendor=='hygon':
                for panel in dashboard['panels']:
                    expressions=' '.join(t.get('expr','') for t in panel.get('targets',[]))
                    if 'hcu_power_usage' in expressions or 'hcu_utilizationrate' in expressions:
                        self.assertEqual(panel['fieldConfig']['defaults']['unit'],'short')

    def test_full_nvidia_profile_includes_profiling_only_when_selected(self):
        dashboard=build_dashboard('nvidia',{'gpu':{'nvidia_metrics_profile':'full'}})
        self.assertIn('DCGM_FI_PROF_',json.dumps(dashboard))

    def test_prebuilt_dashboards_match_generator(self):
        for vendor in ('nvidia','hygon'):
            snapshot=json.loads((ROOT/f'configs/common/grafana/dashboards/task-overview-{vendor}.json').read_text())
            self.assertEqual(snapshot,build_dashboard(vendor))

    def test_legacy_log_files_and_project_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp)
            cfg.pop('task');cfg.pop('log_sources')
            cfg['log_files']=['server.log']
            self.assertEqual(task_name(cfg),cfg['project'])
            self.assertEqual(log_sources(cfg)[0]['path'],'server.log')
            directory,_=stack.prepare(cfg)
            self.assertIn('"task" = "'+cfg['project']+'"',(directory/'alloy/config.alloy').read_text())

    def test_invalid_labels_and_log_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for changes in ({'task':''},{'task':'bad\nlabel'}, {'task':'bad`label'}, {'task':'bad"label'}, {'task':'bad\\label'},
                {'log_sources':[{'path':'../outside.log'}]}, {'log_sources':[{'path':'/absolute.log'}]},
                {'log_sources':[{'path':'a.log'},{'path':'a.log'}]}, {'log_sources':[]},
                {'log_sources':[{'path':'a.log','role':''}]}, {'log_sources':[{'path':'a.log','replica':0}]}):
                cfg=self.config(tmp);cfg.update(changes)
                with self.subTest(changes=changes),self.assertRaises(ValueError):
                    stack.validate_config(cfg)
            cfg=self.config(tmp);cfg['gpu']['index_label']='bad-label'
            with self.assertRaisesRegex(ValueError,'index_label'):
                stack.validate_config(cfg)

    def test_auto_vendor_detection_controls_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            from unittest.mock import patch
            cfg=self.config(tmp);cfg['gpu']['vendor']='auto'
            with patch('benchmark_stack.shutil.which',side_effect=lambda cmd: '/usr/bin/'+cmd if cmd=='hy-smi' else None), patch('benchmark_stack.subprocess.run') as command:
                command.return_value.returncode=0;command.return_value.stdout='Hygon'
                directory,vendor=stack.prepare(cfg)
            self.assertEqual(vendor,'hygon')
            self.assertIn('海光',json.loads((directory/'grafana/dashboards/task-overview.json').read_text())['title'])
