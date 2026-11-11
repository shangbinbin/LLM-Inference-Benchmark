from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import benchmark_stack as stack
from aisbench_trial import TrialSession
spec = importlib.util.spec_from_file_location('toolbox_auth_test', ROOT / 'containers/toolbox/toolbox.py')
toolbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(toolbox)


class AuthTests(unittest.TestCase):
    def config(self):
        cfg = stack.load_config(ROOT / 'configs/stacks/mac-simulation.json')
        cfg['inference']['api_key_env'] = 'BENCH_TEST_TOKEN'
        return cfg

    def test_environment_and_generated_config_do_not_embed_value(self):
        cfg = self.config()
        for offline in (True, False):
            for api in ('chat', 'completions'):
                cfg.update(offline=offline)
                cfg['inference']['api'] = api
                with patch.dict(os.environ, BENCH_TEST_TOKEN='secret-test-value'):
                    args = stack.aisbench_environment(cfg)
                    code = stack.ais_config(cfg)
                    self.assertIn('BENCH_TEST_TOKEN', args)
                    self.assertNotIn('secret-test-value', repr(args) + code + json.dumps(cfg))
                    compile(code, '<aisbench-config>', 'exec')
                    self.assertIn("api_key_env='BENCH_TEST_TOKEN'", code)
                    self.assertIn('BearerVLLMAPIChat' if api == 'chat' else 'BearerVLLMAPI', code)
        cfg['inference'].pop('api_key_env')
        self.assertNotIn('llm_bench_auth', stack.ais_config(cfg))

    def test_backend_resolves_token_at_construction_only(self):
        # Exercise the mixin against a fake upstream constructor without loading Torch.
        import types
        module = types.ModuleType('ais_bench.benchmark.models')
        class API:
            def __init__(self, **kwargs):
                self.headers = {'Authorization': 'Bearer ' + kwargs['api_key']}
        module.VLLMCustomAPI = module.VLLMCustomAPIChat = API
        spec = importlib.util.spec_from_file_location('auth_extension', ROOT / 'containers/aisbench/llm_bench_auth.py')
        auth = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'ais_bench.benchmark.models': module}):
            spec.loader.exec_module(auth)
        for cls in (auth.BearerVLLMAPI, auth.BearerVLLMAPIChat):
            config = {'api_key_env': 'BENCH_TEST_TOKEN', 'api_key': ''}
            with patch.dict(os.environ, BENCH_TEST_TOKEN='test-secret'):
                client = cls(**config)
            self.assertEqual(client.headers['Authorization'], 'Bearer test-secret')
            self.assertEqual(config['api_key'], '')
            with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
                cls(**config)

    def test_missing_and_invalid_token_fail_without_disclosing_value(self):
        cfg = self.config()
        for value in ('', ' ', 'Bearer secret', 'secret\nvalue'):
            with patch.dict(os.environ, BENCH_TEST_TOKEN=value):
                with self.assertRaises(ValueError) as caught:
                    stack.inference_environment(cfg)
                if value.strip():
                    self.assertNotIn(value, str(caught.exception))
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            stack.inference_environment(cfg)
        cfg['inference']['api_key_env'] = 'BAD=NAME'
        with self.assertRaisesRegex(ValueError, 'variable name'):
            stack.validate_config(cfg)

    def test_stack_probes_scope_token_only_to_inference(self):
        cfg = self.config()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, BENCH_TEST_TOKEN='test-secret'):
            cfg['storage']['root'] = tmp
            instance = stack.Stack(cfg, Path(tmp), 'nvidia')
            instance.call = Mock()
            instance.probe('http://prometheus:9090/-/ready')
            self.assertNotIn('BENCH_TEST_TOKEN', instance.call.call_args.args)
            instance.probe('http://inference/v1/models', 'data', inference=True)
            args = instance.call.call_args.args
            self.assertIn('--api-key-env', args)
            self.assertIn('BENCH_TEST_TOKEN', args)
            self.assertNotIn('test-secret', repr(args))

    def test_search_readiness_authenticates_inference_but_not_metrics(self):
        session = TrialSession.__new__(TrialSession)
        session.cfg = self.config()
        session.state = {'containers': [], 'network': 'test-network'}
        session.probe_name = 'test-probe'
        session.inspect = Mock(return_value=None)
        session.remove_owned = Mock()
        session.call = Mock()
        with patch.dict(os.environ, BENCH_TEST_TOKEN='test-secret'):
            session.ready()
        metrics, inference = session.call.call_args_list
        self.assertNotIn('BENCH_TEST_TOKEN', metrics.args)
        self.assertIn('BENCH_TEST_TOKEN', inference.args)
        self.assertIn('--api-key-env', inference.args)
        self.assertNotIn('test-secret', repr(session.call.call_args_list))

    def test_probe_sends_header_and_fails_on_401(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"data": []}'
        opener = Mock()
        opener.open.return_value = response
        with patch.dict(os.environ, BENCH_TEST_TOKEN='test-secret'), patch.object(
                toolbox.urllib.request, 'build_opener', return_value=opener), redirect_stdout(io.StringIO()):
            toolbox.probe('http://inference/v1/models', 5, 'data', 'BENCH_TEST_TOKEN')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-secret')
        opener.open.side_effect = toolbox.urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, None)
        with patch.dict(os.environ, BENCH_TEST_TOKEN='test-secret'), patch.object(
                toolbox.urllib.request, 'build_opener', return_value=opener), patch.object(
                toolbox.time, 'monotonic', side_effect=[0, 0, 6]), patch.object(toolbox.time, 'sleep'):
            with self.assertRaisesRegex(SystemExit, '401'):
                toolbox.probe(request.full_url, 5, 'data', 'BENCH_TEST_TOKEN')

    def test_authenticated_probe_refuses_redirects(self):
        with self.assertRaisesRegex(ValueError, 'refuses redirects'):
            toolbox.NoAuthRedirect().redirect_request(None, None, 302, '', {}, 'http://other-host/')
