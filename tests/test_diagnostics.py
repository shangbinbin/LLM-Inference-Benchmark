import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stderr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from benchmark_stack import LABEL
from diagnostics import collect, collect_targets, failure_detail, run_diagnosed


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        logs = self.root/'logs'
        logs.mkdir()
        self.cfg = {'project': 'site', 'storage': {'root': str(self.root), 'logs': str(logs)},
                    'offline': True, 'runtime': ['nerdctl', '--namespace', 'bench']}
        self.owner = 'a' * 12
        self.foreign = 'b' * 12
        self.calls = []

    def runtime(self, argv, **kw):
        self.assertEqual(argv[:3], self.cfg['runtime'])
        args = argv[3:]
        self.calls.append(args)
        if args[0] == 'version':
            out = 'local engine'
        elif args[0] == 'ps':
            self.assertIn('label=' + LABEL + '=site', args)
            out = self.owner + '\n' + self.foreign
        elif args[0] == 'inspect':
            label = 'site' if args[1] == self.owner else 'other'
            out = json.dumps([{'Name': '/site-alloy', 'Config': {'Labels': {LABEL: label},
                       'Env': ['OPENAI_API_KEY=secret-test-value'], 'Image': 'alloy:local'},
                       'State': {'Status': 'exited', 'ExitCode': 137, 'OOMKilled': True}}])
        elif args[0] == 'logs':
            self.assertEqual(args[-1], self.owner)
            kw['stdout'].write(b'API_KEY=secret-test-value\nOOM detected')
            out = ''
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(argv, 0, out.encode())

    def test_scope_redaction_and_result_selection(self):
        self.cfg['log_files'] = ['server.log', 'leak.log', '../outside']
        (self.root/'logs/server.log').write_text('old line\nAuthorization: Bearer secret-test-value\nnew line')
        outside = self.root/'sensitive'
        outside.write_text('private-prompt-do-not-copy')
        (self.root/'logs/leak.log').symlink_to(outside)
        source = self.root/'data/results/suites/20260102'
        folder = source/'case/performances/model'
        folder.mkdir(parents=True)
        (source/'suite-results.json').write_text(json.dumps([{'benchmark': {'generation_kwargs':
            {'custom': 'private-prompt-do-not-copy'}}, 'error': 'timeout'}]))
        (folder/'jsonl_details.jsonl').write_text(json.dumps({'success': False, 'id': 7, 'error_info': '503',
                                                 'input': 'private-prompt-do-not-copy'}) + '\n')
        (folder/'raw.db').write_bytes(b'private-prompt-do-not-copy')
        runner = source/'logs/infer/model/task.out'
        runner.parent.mkdir(parents=True)
        runner.write_text('old line\nserver returned 503\nworker exited')
        other = self.root/'data/results/suites/20260103'
        other.mkdir()
        (other/'client.log').write_text('different run')
        with patch('diagnostics.subprocess.run', side_effect=self.runtime):
            archive = collect(self.cfg, selected=Path('suites/20260102'), lines=2)
        with tarfile.open(archive) as bundle:
            contents = {m.name: bundle.extractfile(m).read().decode() for m in bundle}
        text = '\n'.join(contents.values())
        for secret in ('private-prompt-do-not-copy', 'secret-test-value', 'old line', 'different run'):
            self.assertNotIn(secret, text)
        self.assertTrue(any(k.endswith('task.out') and 'server returned 503' in v for k, v in contents.items()))
        self.assertIn('new line', contents['inference/server.log'])
        self.assertEqual(json.loads(contents['containers/site-alloy/state.json'])['ExitCode'], 137)
        self.assertIn('OOM detected', contents['containers/site-alloy/container.log'])
        self.assertIn('project label mismatch', text)
        errors = next(v for k, v in contents.items() if k.endswith('jsonl_failures.json'))
        self.assertEqual(json.loads(errors), [{'id': 7, 'error_info': '503'}])
        self.assertFalse(any(k.endswith(('_details.jsonl', '.db', 'leak.log')) for k in contents))
        self.assertEqual(archive.stat().st_mode & 0o777, 0o600)

    def test_partial_bundle_without_engine_or_state_and_no_overwrite(self):
        with patch('diagnostics.subprocess.run', side_effect=FileNotFoundError('engine unavailable')):
            path = collect(self.cfg)
            with tarfile.open(path) as t:
                notes = json.load(t.extractfile('manifest.json'))['collection_notes']
                self.assertTrue(any('engine unavailable' in n for n in notes))
            with self.assertRaises(FileExistsError):
                collect(self.cfg, output=path)
            with self.assertRaises(ValueError):
                collect(self.cfg, selected=Path('../sensitive'))

    def test_archive_failure_preserves_original_failure(self):
        stack = Mock(root=self.root, cfg=self.cfg)
        err = subprocess.CalledProcessError(7, ['docker', 'run'], stderr='probe failed API_KEY=secret-test-value')
        with redirect_stderr(io.StringIO()), patch('diagnostics.collect', side_effect=OSError('full')) as write:
            with self.assertRaises(subprocess.CalledProcessError) as failure:
                run_diagnosed(stack, 'up', Mock(side_effect=err))
        self.assertIs(failure.exception, err)
        self.assertIn('probe failed', write.call_args.kwargs['error']['message'])
        self.assertNotIn('secret-test-value', failure_detail(err))
        self.assertEqual(json.loads((self.root/'diagnostics/events.jsonl').read_text().splitlines()[-1])['status'], 'failed')

    def test_targets_use_published_binding(self):
        bundle = Mock()
        collect_targets(bundle, {'NetworkSettings': {'Ports': {}}})
        bundle.add.assert_not_called()
        item = {'scrapeUrl': 'http://worker:9400/metrics', 'health': 'down', 'lastError': 'timeout'}
        with patch('diagnostics.http.client.HTTPConnection') as connect:
            response = connect.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = json.dumps({'data': {'activeTargets': [item]}}).encode()
            collect_targets(bundle, {'NetworkSettings': {'Ports': {'9090/tcp': [
                            {'HostIp': '127.0.0.1', 'HostPort': '39090'}]}}})
            connect.assert_called_once_with('127.0.0.1', 39090, timeout=3)
            self.assertEqual(bundle.add.call_args.args[1][0]['lastError'], 'timeout')

    def test_targets_support_wildcard_ipv6_and_explicit_bind_address(self):
        for address, host in [('0.0.0.0', '127.0.0.1'), ('::', '::1'), ('192.0.2.10','192.0.2.10')]:
            with self.subTest(address=address), patch('diagnostics.http.client.HTTPConnection') as connect:
                response = connect.return_value.getresponse.return_value
                response.status = 200
                response.read.return_value = b'{"data":{"activeTargets":[]}}'
                collect_targets(Mock(), {'NetworkSettings':{'Ports':{'9090/tcp':[{'HostIp':address,'HostPort':'45000'}]}}})
                connect.assert_called_once_with(host,45000,timeout=3)
