import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from create_site_config import create_site, DEFAULT_SOURCE
from benchmark_stack import metrics_settings, validate_config
from llm_bench import parser_for_cli, running_stack


class SiteConfigTests(unittest.TestCase):
    def test_generation_preserves_gpu41_workload_paths_and_gateway(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'site/site.json'
            cfg=create_site(DEFAULT_SOURCE,output)
            self.assertEqual(json.loads(output.read_text()),json.loads(DEFAULT_SOURCE.read_text()))
            self.assertEqual(cfg['runtime'],['docker'])
            self.assertEqual(cfg['storage']['root'],'generated/qwen-real')
            self.assertEqual(cfg['log_sources'][0]['path'],'server_log.log')
            bench=cfg['benchmark']
            self.assertEqual((bench['dataset_type'],bench['input_length'],bench['output_length'],bench['request_count'],bench['concurrency']),('tokenid',1024,1024,1000,16))
            metrics=metrics_settings(cfg)
            self.assertEqual(metrics['path'],'/publishaddress/inference/qwen306b/metrics')
            self.assertEqual(metrics['api_key_env'],'INFERENCE_API_KEY')

    def test_existing_site_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'site.json';output.write_text('local edits')
            before=DEFAULT_SOURCE.read_bytes()
            with self.assertRaises(FileExistsError):
                create_site(DEFAULT_SOURCE,output,runtime='docker')
            self.assertEqual(output.read_text(),'local edits')
            self.assertEqual(DEFAULT_SOURCE.read_bytes(),before)

    def test_runtime_override_preserves_all_other_fields(self):
        original=json.loads(DEFAULT_SOURCE.read_text())
        for runtime,namespace,expected in [('docker',None,['docker']),('nerdctl','k8s.io',['nerdctl','--namespace','k8s.io'])]:
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory() as tmp:
                cfg=create_site(DEFAULT_SOURCE,Path(tmp)/'site.json',runtime,namespace)
                self.assertEqual(cfg.pop('runtime'),expected)
                other=copy.deepcopy(original);other.pop('runtime')
                self.assertEqual(cfg,other)
        with tempfile.TemporaryDirectory() as tmp:
            for runtime,namespace in [('nerdctl','bad/name'),('docker','benchmark')]:
                with self.assertRaises(ValueError):
                    create_site(DEFAULT_SOURCE,Path(tmp)/'invalid.json',runtime,namespace)
            self.assertFalse((Path(tmp)/'invalid.json').exists())

    def test_generated_site_can_plan_without_token_or_live_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);output=base/'site.json';create_site(DEFAULT_SOURCE,output,'nerdctl','benchmark')
            env=dict(os.environ);env.pop('INFERENCE_API_KEY',None)
            result=subprocess.run([str(ROOT/'bin/llm-bench'),'plan','--config',str(output),
                '--data-dir',str(base/'data'),'--log-dir',str(base/'logs'),
                '--tokenizer',str(ROOT/'configs/fixtures/mock-tokenizer')],text=True,capture_output=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            plan=json.loads(result.stdout)
            self.assertEqual(plan['runtime_scope']['namespace'],'benchmark')
            directory=Path(plan['config_dir'])
            prom=json.loads((directory/'prometheus/prometheus.yml').read_text())
            application=prom['scrape_configs'][0]
            self.assertEqual(application['metrics_path'],'/publishaddress/inference/qwen306b/metrics')
            self.assertEqual(application['authorization']['credentials_file'],'/run/benchmark-metrics.token')
            alloy=(directory/'alloy/config.alloy').read_text()
            self.assertIn('server_log.log',alloy)
            self.assertIn('"task" = "qwen3-0.6b"',alloy)
            self.assertFalse((base/'data/.credentials/metrics.token').exists())

    def test_generation_defers_tokenizer_existence_but_startup_validation_keeps_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);source=base/'source.json'
            cfg=json.loads(DEFAULT_SOURCE.read_text())
            cfg['storage']['tokenizer']=str(base/'missing-tokenizer')
            source.write_text(json.dumps(cfg))
            created=create_site(source,base/'site.json')
            with self.assertRaisesRegex(ValueError,'local tokenizer_config.json'):
                validate_config(created)

    def test_cli_runtime_choice_preserves_source_and_workload(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);source=base/'site.json';create_site(DEFAULT_SOURCE,source)
            original=source.read_bytes()
            for flags,expected in [(['--runtime','docker'],{'kind':'docker','command':['docker']}),
                                   (['--runtime','nerdctl','--namespace','custom'],
                                    {'kind':'nerdctl','command':['nerdctl','--namespace','custom'],'namespace':'custom'}),
                                   (['--namespace','custom'],
                                    {'kind':'nerdctl','command':['nerdctl','--namespace','custom'],'namespace':'custom'})]:
                with self.subTest(flags=flags):
                    result=subprocess.run([str(ROOT/'bin/llm-bench'),'plan','--config',str(source),
                        '--data-dir',str(base/'data'),'--log-dir',str(base/'logs'),
                        '--tokenizer',str(ROOT/'configs/fixtures/mock-tokenizer'),*flags],text=True,capture_output=True)
                    self.assertEqual(result.returncode,0,result.stderr)
                    plan=json.loads(result.stdout)
                    self.assertEqual(plan['runtime_scope'],expected)
                    self.assertEqual(plan['config']['benchmark'],json.loads(original)['benchmark'])
                    self.assertEqual(plan['config']['inference']['model'],'Qwen3-0.6B')
                    self.assertEqual(source.read_bytes(),original)

    def test_existing_stack_uses_saved_runtime_after_site_file_changes(self):
        for saved_runtime in ('docker','nerdctl'):
            with self.subTest(runtime=saved_runtime), tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp);source=base/'site.json'
                cfg=create_site(DEFAULT_SOURCE,source,saved_runtime)
                cfg['storage'].update(root=str(base/'data'),logs=str(base/'logs'),
                    tokenizer=str(ROOT/'configs/fixtures/mock-tokenizer'))
                source.write_text(json.dumps(cfg))
                result=subprocess.run([str(ROOT/'bin/llm-bench'),'plan','--config',str(source)],
                    text=True,capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr)
                plan=json.loads(result.stdout)
                (base/'data/state.json').write_text(json.dumps({'project':cfg['project'],
                    'config_dir':plan['config_dir']}))
                cfg['runtime']=['nerdctl','--namespace','other'] if saved_runtime=='docker' else ['docker']
                source.write_text(json.dumps(cfg))
                for command in (['status'],['down'],['bench','run']):
                    args=parser_for_cli().parse_args([*command,'--config',str(source)])
                    instance,_=running_stack(args)
                    self.assertEqual(instance.cfg['runtime'],plan['config']['runtime'])
