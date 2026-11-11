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

    def test_local_nvidia_uses_basic_collectors_and_full_mode_is_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            cfg['simulation'] = False
            directory, vendor = stack.prepare(cfg)
            instance = stack.Stack(cfg, directory, vendor)
            exporter = next(s for s in instance.specs() if s['name'] == 'gpu-exporter')
            self.assertEqual(exporter['command'], ['--collectors', '/etc/dcgm-exporter/basic.csv'])
            self.assertIn('src='+str(ROOT/'configs/gpu/nvidia/dcgm/basic.csv')+',dst=/etc/dcgm-exporter/basic.csv,readonly',
                          repr(exporter['args']))
            fields = [line.split(',')[0] for line in (ROOT/'configs/gpu/nvidia/dcgm/basic.csv').read_text().splitlines()]
            self.assertEqual(len(fields), 8)
            self.assertIn('DCGM_FI_DEV_GPU_UTIL', fields)
            self.assertFalse(any(field.startswith('DCGM_FI_PROF_') for field in fields))
            cfg['gpu']['nvidia_metrics_profile'] = 'full'
            exporter = next(s for s in instance.specs() if s['name'] == 'gpu-exporter')
            self.assertEqual(exporter['command'], [])
            self.assertNotIn('basic.csv', repr(exporter['args']))
            cfg['gpu']['nvidia_metrics_profile'] = 'basic'
            exporter = next(s for s in stack.Stack(cfg, directory, 'hygon').specs() if s['name'] == 'gpu-exporter')
            self.assertNotIn('basic.csv', repr(exporter))
            for simulation, deployment in [(True, 'local'), (False, 'external')]:
                cfg['simulation'] = simulation
                cfg['gpu']['deployment'] = deployment
                self.assertFalse(any(s['name'] == 'gpu-exporter' for s in instance.specs()))

    def test_invalid_nvidia_collector_profile_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            cfg['storage']['tokenizer'] = str(ROOT/'configs/fixtures/mock-tokenizer')
            cfg['gpu']['nvidia_metrics_profile'] = 'unsupported'
            with self.assertRaisesRegex(ValueError, 'nvidia_metrics_profile'):
                stack.validate_config(cfg)

    def test_client_thread_limits_apply_online_and_offline_without_changing_concurrency(self):
        expected = {'OPENBLAS_NUM_THREADS=1', 'OMP_NUM_THREADS=1', 'MKL_NUM_THREADS=1',
                    'NUMEXPR_NUM_THREADS=1', 'TOKENIZERS_PARALLELISM=false'}
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            concurrency = cfg['benchmark']['concurrency']
            for offline in (False, True):
                cfg['offline'] = offline
                with patch.dict(os.environ, OPENBLAS_NUM_THREADS='48', OMP_NUM_THREADS='48'):
                    args = stack.aisbench_environment(cfg)
                env = [args[i + 1] for i, arg in enumerate(args) if arg == '-e']
                self.assertTrue(expected.issubset(env))
                self.assertEqual(len(env), len({value.split('=', 1)[0] for value in env}))
                self.assertEqual('HF_HUB_OFFLINE=1' in env, offline)
                self.assertEqual(cfg['benchmark']['concurrency'], concurrency)

    def test_seccomp_defaults_follow_offline_and_allow_explicit_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            for offline in (False, True):
                cfg['offline'] = offline
                for enabled in (None, False, True):
                    if enabled is None:
                        cfg.pop('legacy_seccomp_unconfined', None)
                    else:
                        cfg['legacy_seccomp_unconfined'] = enabled
                    expected = offline if enabled is None else enabled
                    directory, vendor = stack.prepare(cfg)
                    for simulation in (True, False):
                        cfg['simulation'] = simulation
                        specs = stack.Stack(cfg, directory, vendor).specs()
                        for spec in specs:
                            cmd = stack.runtime_command(cfg, ['run', *spec['args'], spec['image']])
                            self.assertEqual(cmd.count('seccomp=unconfined'), int(expected))
                            self.assertEqual('--pull=never' in cmd, offline)
                    cfg['simulation'] = True
                    # Shared paths cover one-click clients, suite clients, SLO trials and probes.
                    for launch in (['create', *stack.aisbench_environment(cfg), 'client-image'],
                                   ['run', *stack.aisbench_environment(cfg), 'client-image'],
                                   ['run', '--rm', 'toolbox-image', 'probe', 'http://fixture/']):
                        cmd = stack.runtime_command(cfg, launch)
                        self.assertEqual(cmd.count('seccomp=unconfined'), int(expected))
                        self.assertEqual('--pull=never' in cmd, offline)
                    for action in ('inspect', 'exec', 'start', 'restart', 'rm', 'network'):
                        cmd = stack.runtime_command(cfg, [action, 'resource'])
                        self.assertNotIn('--security-opt', cmd)

    def test_validation_materializes_security_default_without_overriding_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            for offline in (False, True):
                cfg = self.config(tmp)
                cfg['offline'] = offline
                cfg['storage']['tokenizer'] = str(ROOT / 'configs/fixtures/mock-tokenizer')
                cfg.pop('legacy_seccomp_unconfined', None)
                self.assertEqual(stack.validate_config(cfg)['legacy_seccomp_unconfined'], offline)
                cfg['legacy_seccomp_unconfined'] = False
                self.assertFalse(stack.validate_config(cfg)['legacy_seccomp_unconfined'])

    def test_invalid_seccomp_compatibility_values_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for value in ('true', 'false', 1, 0, None, []):
                cfg = self.config(tmp)
                cfg['legacy_seccomp_unconfined'] = value
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'JSON boolean'):
                    stack.validate_config(cfg)
                with self.assertRaisesRegex(ValueError, 'JSON boolean'):
                    stack.runtime_command(cfg, ['run', 'image'])

    def test_gateway_metrics_path_and_bearer_file_are_scoped_to_application(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            cfg['topology']['metrics_path'] = '/publishaddress/inference/qwen306b/metrics'
            cfg['topology']['metrics_api_key_env'] = 'TEST_METRICS_TOKEN'
            directory, vendor = stack.prepare(cfg)
            prom = json.loads((directory/'prometheus/prometheus.yml').read_text())
            application = prom['scrape_configs'][0]
            self.assertEqual(application['metrics_path'], cfg['topology']['metrics_path'])
            self.assertEqual(application['authorization'], {'type': 'Bearer', 'credentials_file': '/run/benchmark-metrics.token'})
            self.assertTrue(all('authorization' not in job for job in prom['scrape_configs'][1:]))
            instance = stack.Stack(cfg, directory, vendor)
            with patch.dict(os.environ, TEST_METRICS_TOKEN='dummy-metrics-secret'):
                instance.write_metrics_token()
            tokenfile = instance.metrics_token_file()
            self.assertEqual(tokenfile.read_text(), 'dummy-metrics-secret')
            self.assertEqual(tokenfile.stat().st_mode & 0o777, 0o600)
            self.assertEqual(tokenfile.parent.stat().st_mode & 0o777, 0o700)
            for artifact in directory.rglob('*'):
                if artifact.is_file():
                    self.assertNotIn('dummy-metrics-secret', artifact.read_text())
            for spec in instance.specs():
                args = repr(spec['args'])
                self.assertNotIn('dummy-metrics-secret', args)
                self.assertEqual('/run/benchmark-metrics.token' in args, spec['name'] == 'prometheus')
            tokenfile.unlink()
            with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, 'unset or empty'):
                instance.write_metrics_token()
            self.assertFalse(tokenfile.exists())
            outside = Path(tmp)/'outside'
            outside.write_text('untouched')
            tokenfile.symlink_to(outside)
            with patch.dict(os.environ, TEST_METRICS_TOKEN='dummy'), self.assertRaises(OSError):
                instance.write_metrics_token()
            self.assertEqual(outside.read_text(), 'untouched')

    def test_down_removes_metrics_credential_after_stopping_owned_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            cfg['topology']['metrics_api_key_env'] = 'TEST_METRICS_TOKEN'
            directory, vendor = stack.prepare(cfg)
            instance = stack.Stack(cfg, directory, vendor)
            with patch.dict(os.environ, TEST_METRICS_TOKEN='test-secret'):
                instance.write_metrics_token()
            instance.statefile.write_text(json.dumps({'containers': [], 'network': instance.network}))
            result = subprocess.CompletedProcess([], 1, '', 'not found')
            with patch.object(instance, 'call', return_value=result):
                instance.down()
            self.assertFalse(instance.metrics_token_file().exists())
            self.assertFalse(instance.statefile.exists())

    def test_one_base_url_drives_chat_metrics_and_model_readiness_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            cfg['storage']['tokenizer'] = str(ROOT / 'configs/fixtures/mock-tokenizer')
            cfg['topology'].pop('metrics_endpoints')
            cfg['inference'].update(api='chat', api_key_env='GATEWAY_TOKEN')
            for base, expected_path in [
                ('http://gateway:38000/publishaddress/inference/model-a', '/publishaddress/inference/model-a/metrics'),
                ('https://gateway:443/nested/model-b/', '/nested/model-b/metrics'),
                ('http://gateway:8000/', '/metrics'),
            ]:
                cfg['inference']['base_url'] = base
                checked = stack.validate_config(cfg)
                settings = stack.metrics_settings(checked)
                self.assertEqual(settings['path'], expected_path)
                self.assertEqual(settings['api_key_env'], 'GATEWAY_TOKEN')
                self.assertEqual(settings['targets'][0]['target'], base.split('/')[2])
                directory, vendor = stack.prepare(checked)
                application = json.loads((directory/'prometheus/prometheus.yml').read_text())['scrape_configs'][0]
                self.assertEqual(application['metrics_path'], expected_path)
                self.assertEqual(application['scheme'], base.split(':')[0])
                self.assertEqual(application['static_configs'][0]['targets'], [base.split('/')[2]])
                self.assertIn('authorization', application)
                self.assertIn(repr(base.rstrip('/') + '/'), stack.ais_config(checked))
                instance = stack.Stack(checked, directory, vendor)
                with patch.object(instance, 'call') as call, patch.dict(os.environ, GATEWAY_TOKEN='dummy'):
                    instance.probe(checked['inference']['base_url'].rstrip('/')+'/v1/models', 'data', inference=True)
                self.assertIn(base.rstrip('/')+'/v1/models', call.call_args.args)

    def test_metrics_overrides_and_anonymous_scraping_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            cfg['inference'].update(base_url='https://gateway/prefix/', api_key_env='GATEWAY_TOKEN')
            cfg['topology'].update(metrics_endpoints=[{'target':'other:9100','role':'worker'}],
                                   metrics_path='/custom/metrics', metrics_scheme='http', metrics_api_key_env=None)
            settings = stack.metrics_settings(cfg)
            self.assertEqual(settings, {'targets':[{'target':'other:9100','role':'worker'}],
                                        'path':'/custom/metrics','scheme':'http','api_key_env':None})
            directory, vendor = stack.prepare(cfg)
            application = json.loads((directory/'prometheus/prometheus.yml').read_text())['scrape_configs'][0]
            self.assertNotIn('authorization', application)
            self.assertEqual(application['metrics_path'], '/custom/metrics')
            self.assertEqual(application['scheme'], 'http')

    def test_gateway_metrics_fields_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            for key, value in [('metrics_path', 'http://host/metrics'), ('metrics_path', '/metrics?token=secret'),
                               ('metrics_path', '/bad\npath'), ('metrics_api_key_env', 'INVALID=NAME')]:
                cfg = self.config(tmp)
                cfg['topology'][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    stack.validate_config(cfg)

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
            self.assertEqual(prom['scrape_configs'][-1]['static_configs'][0]['targets'],['192.0.2.10:39100'])

    def test_service_ports_are_published_on_all_interfaces_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            folder, vendor = stack.prepare(cfg)
            for simulation in (True, False):
                cfg['simulation'] = simulation
                specs = stack.Stack(cfg, folder, vendor).specs()
                internal = {'fixture':18000,'alloy':12345,'node-exporter':9100,'gpu-exporter':9400,
                            'prometheus':9090,'loki':3100,'grafana':3000}
                for spec in specs:
                    if spec['name'] == 'logrotate':
                        self.assertNotIn('-p', spec['args'])
                    else:
                        expected = '0.0.0.0:%s:%s' % (cfg['ports'][spec['name']], internal[spec['name']])
                        self.assertIn(expected, spec['args'])
                cfg['port_bind_address'] = '192.0.2.10'
                cfg['ports']['grafana'] = 45000
                specs = stack.Stack(cfg, folder, vendor).specs()
                self.assertIn('192.0.2.10:45000:3000', next(s for s in specs if s['name']=='grafana')['args'])
                cfg['port_bind_address'] = '0.0.0.0'

    def test_port_disabling_ipv6_and_hygon_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            folder, vendor = stack.prepare(cfg)
            cfg['ports']['grafana'] = None
            cfg['port_bind_address'] = '::1'
            specs = stack.Stack(cfg, folder, vendor).specs()
            self.assertNotIn('-p', next(s for s in specs if s['name']=='grafana')['args'])
            self.assertIn('[::1]:%s:12345' % cfg['ports']['alloy'], next(s for s in specs if s['name']=='alloy')['args'])
            cfg['publish_ports'] = False
            self.assertTrue(all('-p' not in s['args'] for s in stack.Stack(cfg, folder, vendor).specs()))
            cfg.update(simulation=False, publish_ports=True)
            specs = stack.Stack(cfg, folder, 'hygon').specs()
            self.assertIn('[::1]:%s:16080' % cfg['ports']['gpu-exporter'], next(s for s in specs if s['name']=='gpu-exporter')['args'])

    def test_custom_host_exporter_port_updates_listener_and_scrape_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(tmp)
            cfg.update(simulation=False,host_metrics='host',node_exporter_target='192.0.2.10:9100')
            cfg['ports']['node-exporter'] = 45010
            folder, vendor = stack.prepare(cfg)
            node = next(s for s in stack.Stack(cfg, folder, vendor).specs() if s['name']=='node-exporter')
            self.assertIn('--web.listen-address=0.0.0.0:45010', node['command'])
            self.assertNotIn('-p', node['args'])
            self.assertIn('http://192.0.2.10:45010/metrics', node['ready'])
            prom=json.loads((folder/'prometheus/prometheus.yml').read_text())
            self.assertEqual(prom['scrape_configs'][-1]['static_configs'][0]['targets'], ['192.0.2.10:45010'])

    def test_port_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            for changes in ({'ports':{'grafana':True}}, {'ports':{'grafana':65536}},
                            {'ports':{'unknown':40000}}, {'ports':{'grafana':40000,'loki':40000}},
                            {'port_bind_address':'not-an-ip'}, {'publish_ports':'false'}):
                cfg=self.config(tmp)
                cfg['storage']['tokenizer']=str(ROOT/'configs/fixtures/mock-tokenizer')
                cfg.update(changes)
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    stack.validate_config(cfg)

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
