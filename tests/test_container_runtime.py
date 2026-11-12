import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import benchmark_stack as stack
from container_runtime import (adapt_command, namespace, runtime_kind, runtime_scope,
                               service_network, validate_runtime)


class ContainerRuntimeTests(unittest.TestCase):
    def config(self, root):
        cfg = stack.load_config(ROOT/'configs/stacks/mac-offline.json')
        cfg.update(runtime=['nerdctl','--namespace','benchmark'], platform='linux/amd64')
        cfg['storage'].update(root=str(Path(root)/'state'), logs=str(Path(root)/'logs'))
        cfg['images']['toolbox']='llm-benchmark-toolbox:local-amd64'
        cfg['images']['aisbench']='llm-benchmark-aisbench:e330dc1-amd64'
        return stack.validate_config(cfg)

    def test_namespace_must_be_explicit_and_unambiguous(self):
        for command in (['nerdctl'], ['nerdctl','--namespace'], ['nerdctl','--namespace',''],
                        ['nerdctl','--namespace','a','-n','b'], ['nerdctl','--namespace','bad/name']):
            with self.subTest(command=command), self.assertRaises(ValueError):
                validate_runtime({'runtime':command})
        for command in (['nerdctl','--namespace=bench'], ['/usr/local/bin/nerdctl','-n','bench'],
                        ['sudo','-n','nerdctl','--namespace','bench']):
            validate_runtime({'runtime':command})
            self.assertEqual(namespace({'runtime':command}),'bench')
        for command in ([], ['docker', None], 'nerdctl', ['ctr'], ['ctrcli']):
            with self.subTest(command=command),self.assertRaises(ValueError):
                validate_runtime({'runtime':command})
        with self.assertRaises(ValueError):
            validate_runtime({'runtime':['docker'],'runtime_kind':'containerd'})

    def test_wrapper_can_explicitly_select_nerdctl(self):
        cfg={'runtime':['python3','/path/adapter.py','--namespace','bench'],'runtime_kind':'nerdctl'}
        validate_runtime(cfg)
        self.assertEqual(runtime_kind(cfg),'nerdctl')
        self.assertEqual(runtime_scope(cfg)['namespace'],'bench')

    def test_inspection_modes_and_platform_are_explicit(self):
        cfg={'runtime':['nerdctl','--namespace','bench'],'platform':'linux/amd64'}
        self.assertEqual(adapt_command(cfg,['inspect','owned']),['inspect','--mode=dockercompat','owned'])
        self.assertEqual(adapt_command(cfg,['network','inspect','net']),['network','inspect','--mode=dockercompat','net'])
        self.assertEqual(adapt_command(cfg,['image','inspect','image']),
                         ['image','inspect','--mode=dockercompat','--platform=linux/amd64','image'])
        cfg['runtime']=['docker']
        self.assertEqual(adapt_command(cfg,['inspect','owned']),['inspect','owned'])

    def test_nerdctl_hostname_and_docker_network_alias(self):
        cfg={'runtime':['nerdctl','--namespace','bench']}
        self.assertEqual(service_network(cfg,'project-net','loki'),['--network','project-net','--hostname','loki'])
        self.assertEqual(service_network(cfg,'project-net','node-exporter',True),['--network','host'])
        cfg['runtime']=['docker']
        self.assertEqual(service_network(cfg,'project-net','loki'),['--network','project-net','--network-alias','loki'])

    def test_nvidia_devices_use_nerdctl_gpus_only_for_exporter(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp);cfg['simulation']=False;cfg['gpu']['vendor']='nvidia'
            directory,vendor=stack.prepare(cfg)
            specs=stack.Stack(cfg,directory,vendor).specs()
            exporter=next(s for s in specs if s['name']=='gpu-exporter')
            self.assertEqual(exporter['args'][:2],['--gpus','all'])
            self.assertNotIn('--runtime=nvidia',exporter['args'])
            self.assertIn('SYS_ADMIN',exporter['args'])
            self.assertIn('NVIDIA_DRIVER_CAPABILITIES=compute,utility',exporter['args'])
            self.assertTrue(all('--gpus' not in s['args'] for s in specs if s['name']!='gpu-exporter'))
            for simulation,deployment,vendor in ((True,'local','nvidia'),(False,'external','nvidia'),(False,'local','hygon')):
                cfg['simulation']=simulation;cfg['gpu']['deployment']=deployment
                self.assertNotIn('--gpus',str(stack.Stack(cfg,directory,vendor).specs()))

    def test_all_launches_keep_offline_and_security_policies(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp)
            for action in ('run','create'):
                cmd=stack.runtime_command(cfg,[action,'image'])
                self.assertEqual(cmd[:3],cfg['runtime'])
                self.assertIn('--pull=never',cmd)
                self.assertIn('--log-driver=json-file',cmd)
                self.assertIn('seccomp=unconfined',cmd)
            for action in ('build','pull'):
                with self.assertRaisesRegex(ValueError,'Offline mode forbids'):
                    stack.runtime_command(cfg,[action,'image'])

    def test_down_refuses_other_namespace_and_accepts_legacy_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp);directory,vendor=stack.prepare(cfg);instance=stack.Stack(cfg,directory,vendor)
            instance.save('ready')
            original=json.loads(instance.statefile.read_text())
            instance.check_state_runtime(original)
            legacy=copy.deepcopy(original);legacy.pop('runtime_scope')
            instance.check_state_runtime(legacy)
            cfg['runtime']=['nerdctl','--namespace','other']
            with patch.object(instance,'call') as call,self.assertRaisesRegex(ValueError,'Runtime/namespace differs'):
                instance.down()
            call.assert_not_called()
            self.assertTrue(instance.statefile.exists())

    def test_failed_inspection_never_masquerades_as_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=self.config(tmp);directory,vendor=stack.prepare(cfg);instance=stack.Stack(cfg,directory,vendor)
            missing=subprocess.CompletedProcess([],1,'','no such object owned')
            with patch.object(instance,'call',return_value=missing):
                self.assertIsNone(instance.inspect('owned'))
            error=subprocess.CompletedProcess([],1,'','dial unix /run/containerd/containerd.sock: permission denied')
            with patch.object(instance,'call',return_value=error),self.assertRaisesRegex(RuntimeError,'inspection failed'):
                instance.inspect('owned')
            instance.save('ready')
            with patch.object(instance,'call',return_value=error),self.assertRaisesRegex(RuntimeError,'state retained'):
                instance.down()
            self.assertTrue(instance.statefile.exists())
