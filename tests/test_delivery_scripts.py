"""Execute both shell workflows with local Git and stubbed installer/container commands.

Real container smoke is a separate hardware/environment validation; these tests assert
failure propagation, bundle integrity, namespace consistency and deployment without load.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from config_io import read_object, write_yaml


class DeliveryScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.commands = self.base/'commands.jsonl'
        self.fakebin = self.base/'fakebin'
        self.fakebin.mkdir()
        self.env = dict(os.environ, PATH=str(self.fakebin)+':'+os.environ['PATH'],
                        COMMAND_LOG=str(self.commands))
        self.executable(self.fakebin/'docker', '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['COMMAND_LOG'], 'a') as f: f.write(json.dumps(['docker', *sys.argv[1:]])+'\\n')
sys.exit(23 if os.environ.get('FAIL_STAGE') == 'load' and 'load' in sys.argv else 0)
'''.replace('#!/usr/bin/env python3', '#!'+sys.executable))
        # A hermetic venv bootstrap: forward Python execution, stub pip's network/install operations.
        self.venv_python = self.base/'venv-python'
        self.executable(self.venv_python, f'''#!/usr/bin/env bash
if [[ ${{1:-}} == -m && ${{2:-}} == pip ]]; then exit 0; fi
exec {shlex.quote(sys.executable)} "$@"
''')
        self.executable(self.fakebin/'python3', f'''#!/usr/bin/env bash
if [[ ${{1:-}} == -m && ${{2:-}} == venv ]]; then
  mkdir -p "$3/bin"
  cp {shlex.quote(str(self.venv_python))} "$3/bin/python"
  exit 0
fi
exec {shlex.quote(sys.executable)} "$@"
''')

    def executable(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o755)

    def fake_cli(self, repo):
        self.executable(repo/'bin/llm-bench', '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['COMMAND_LOG'], 'a') as f: f.write(json.dumps(['llm-bench', *sys.argv[1:]])+'\\n')
if os.environ.get('FAIL_STAGE') == sys.argv[1]: sys.exit(17)
if sys.argv[1] == 'verify':
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
    from config_io import read_object
    cfg = read_object(sys.argv[sys.argv.index('--config')+1])
    assert cfg['benchmark']['dataset_type'] == 'tokenid'
    assert Path(cfg['storage']['tokenizer'], 'tokenizer.json').is_file()
    root = Path(cfg['storage']['root']); root.mkdir(parents=True, exist_ok=True)
    (root/'verification.json').write_text('{"passed":true}')
'''.replace('#!/usr/bin/env python3', '#!'+sys.executable))

    def records(self):
        return [json.loads(line) for line in self.commands.read_text().splitlines()] if self.commands.exists() else []

    def checksum(self, bundle):
        paths = [p for directory in ('repo', 'images', 'metadata', 'tokenizer') for p in (bundle/directory).rglob('*') if p.is_file()]
        (bundle/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(bundle))+'\n' for p in sorted(paths)))

    def offline_bundle(self, runtime):
        bundle = self.base/'offline bundle'
        repo = bundle/'repo'
        shutil.copytree(ROOT/'scripts', repo/'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT/'requirements.txt', repo/'requirements.txt')
        self.fake_cli(repo)
        template = repo/'configs/stacks/nerdctl-offline-smoke.yaml'
        template.parent.mkdir(parents=True)
        shutil.copyfile(ROOT/'configs/stacks/nerdctl-offline-smoke.yaml', template)
        shutil.copytree(ROOT/'configs/fixtures/mock-tokenizer', bundle/'tokenizer')
        (bundle/'metadata').mkdir()
        (bundle/'wheelhouse').mkdir()
        (bundle/'images').mkdir()
        (bundle/'metadata/offline-python.txt').write_text(f'{sys.version_info.major}.{sys.version_info.minor}\n')
        (bundle/'images/images-linux-amd64.tar').write_text('test image payload')
        cfg = read_object(ROOT/'configs/sites/single-node.example.yaml')
        cfg['runtime'] = runtime
        cfg['storage'].update(root=str(bundle/'data'), logs=str(bundle/'logs'),
                              tokenizer=str(ROOT/'configs/fixtures/mock-tokenizer'))
        write_yaml(bundle/'site.yaml', cfg)
        self.checksum(bundle)
        return bundle

    def deploy(self, bundle, *args, **env):
        return subprocess.run(['bash', str(bundle/'repo/scripts/deploy_offline.sh'), *args],
                              env=dict(self.env, PYTHON=str(self.fakebin/'python3'), **env),
                              text=True, capture_output=True)

    def test_offline_uses_same_namespace_and_never_runs_benchmark(self):
        bundle = self.offline_bundle(['docker'])
        for runtime in (['docker'], ['nerdctl', '--namespace', 'isolated']):
            with self.subTest(runtime=runtime):
                shutil.copy2(self.fakebin/'docker', self.fakebin/'nerdctl')
                cfg = read_object(bundle/'site.yaml'); cfg['runtime'] = runtime
                write_yaml(bundle/'site.yaml', cfg)
                self.commands.unlink(missing_ok=True)
                result = self.deploy(bundle)
                self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
                records = self.records()
                self.assertIn(['docker', *runtime[1:], 'load', '-i', str(bundle/'images/images-linux-amd64.tar')], records)
                cli = [r[1:] for r in records if r[0] == 'llm-bench']
                self.assertEqual([r[:2] for r in cli], [['images', 'check'], ['up', '--config'], ['status', '--config']])
                self.assertIn('--no-benchmark', cli[1])
                self.assertTrue((bundle/'env.sh').exists())

    def test_corrupt_bundle_or_wrong_python_fails_before_import(self):
        bundle = self.offline_bundle(['docker'])
        (bundle/'images/images-linux-amd64.tar').write_text('corrupt')
        self.assertNotEqual(self.deploy(bundle).returncode, 0)
        self.assertEqual(self.records(), [])

        self.checksum(bundle)
        (bundle/'metadata/offline-python.txt').write_text('0.0\n')
        self.checksum(bundle)
        result = self.deploy(bundle)
        self.assertIn('requires CPython', result.stderr)
        self.assertEqual(self.records(), [])

    def test_simulation_import_needs_no_real_service_and_starts_no_containers(self):
        bundle = self.offline_bundle(['nerdctl', '--namespace', 'cpu-test'])
        shutil.copy2(self.fakebin/'docker', self.fakebin/'nerdctl')
        # Deliberately leave the real site unusable; simulation reads images/runtime only.
        cfg = read_object(bundle/'site.yaml')
        cfg['storage']['tokenizer'] = '/not-present'
        cfg['inference']['base_url'] = 'http://REPLACE_HOST:8000'
        write_yaml(bundle/'site.yaml', cfg)
        result = self.deploy(bundle, '--simulation')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        smoke = read_object(bundle/'offline-simulation.yaml')
        self.assertEqual(smoke['runtime'], ['nerdctl', '--namespace', 'cpu-test'])
        self.assertTrue(smoke['simulation'] and smoke['offline'])
        self.assertEqual(smoke['host_metrics'], 'container')
        self.assertEqual(smoke['storage']['tokenizer'], str(bundle/'tokenizer'))
        self.assertEqual(smoke['inference']['base_url'], 'http://fixture:18000/')
        cli = [r[1:] for r in self.records() if r[0] == 'llm-bench']
        self.assertEqual(cli, [['images', 'check', '--config', str(bundle/'offline-simulation.yaml')]])
        (bundle/'generated/offline-smoke').mkdir(parents=True)
        (bundle/'generated/offline-smoke/state.json').write_text('{}')
        self.commands.unlink()
        self.assertNotEqual(self.deploy(bundle, '--simulation').returncode, 0)
        self.assertEqual(self.records(), [])

    def test_corrupt_bundled_tokenizer_fails_before_import(self):
        bundle = self.offline_bundle(['docker'])
        (bundle/'tokenizer/tokenizer.json').write_text('tampered')
        self.assertNotEqual(self.deploy(bundle, '--simulation').returncode, 0)
        self.assertEqual(self.records(), [])

    def test_import_failure_never_starts_stack(self):
        bundle = self.offline_bundle(['docker'])
        self.assertNotEqual(self.deploy(bundle, FAIL_STAGE='load').returncode, 0)
        self.assertFalse(any(r[0] == 'llm-bench' for r in self.records()))

    def test_offline_refuses_active_stack_before_import(self):
        bundle = self.offline_bundle(['docker'])
        (bundle/'data').mkdir(); (bundle/'data/state.json').write_text('{}')
        result = self.deploy(bundle)
        self.assertIn('active stack exists', result.stderr)
        self.assertEqual(self.records(), [])

    def online_repo(self):
        repo = self.base/'origin'; repo.mkdir()
        shutil.copytree(ROOT/'scripts', repo/'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('configs/stacks/nerdctl-offline-smoke.yaml', 'configs/sites/single-node.example.yaml',
                     'configs/delivery/offline-images.yaml', 'docs/单机共节点离线部署与分级压测.md'):
            target = repo/name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, target)
        (repo/'requirements.txt').write_text('')
        (repo/'requirements-build.txt').write_text('')
        sdk = repo/'scripts/modelscope/hub'; sdk.mkdir(parents=True)
        (repo/'scripts/modelscope/__init__.py').touch(); (sdk/'__init__.py').touch()
        (sdk/'snapshot_download.py').write_text('''import json, os, shutil
from pathlib import Path
def snapshot_download(**kwargs):
    with open(os.environ['COMMAND_LOG'], 'a') as f: f.write(json.dumps(['download', kwargs])+'\\n')
    if os.environ.get('FAIL_STAGE') == 'download': raise RuntimeError('Download failed')
    shutil.copytree(os.environ['TEST_TOKENIZER'], kwargs['local_dir'])
''')
        (repo/'tests').mkdir()
        (repo/'tests/test_fixture.py').write_text('import unittest\nclass Fixture(unittest.TestCase):\n    def test_ok(self): pass\n')
        self.fake_cli(repo)
        (repo/'scripts/prepare_offline_images.py').write_text('''from pathlib import Path
import json, os, sys
with open(os.environ['COMMAND_LOG'], 'a') as f: f.write(json.dumps(['prepare-images', *sys.argv[1:]])+'\\n')
if '--pull-only' not in sys.argv:
    bundle = Path(sys.argv[sys.argv.index('--output')+1])
    (bundle/'images').mkdir()
    (bundle/'images/images-linux-amd64.tar').write_text('stub images')
''')
        for command in (['git', 'init', '-b', 'main'], ['git', 'add', '.'],
                        ['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-m', 'Fixture']):
            subprocess.run(command, cwd=repo, check=True, capture_output=True)
        return repo

    def test_online_publishes_archive_only_after_smoke_and_checksums_payload(self):
        repo = self.online_repo()
        for stage in ('download', 'up', 'verify', ''):
            with self.subTest(stage=stage):
                output = self.base/(stage or 'success')
                env = dict(self.env, REPO_URL=str(repo), REF='main', OUTPUT_DIR=str(output), FAIL_STAGE=stage,
                           OFFLINE_PYTHON=f'{sys.version_info.major}.{sys.version_info.minor}',
                           TOKENIZER_DIR='', TEST_TOKENIZER=str(ROOT/'configs/fixtures/mock-tokenizer'))
                result = subprocess.run(['bash', str(ROOT/'scripts/build_offline_bundle.sh')], env=env, text=True, capture_output=True)
                archives = list(output.glob('*.tar.gz'))
                if stage:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(archives)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr+result.stdout)
                    self.assertEqual(len(archives), 1)
                    (self.base/'extracted').mkdir()
                    subprocess.run(['tar', '-xzf', str(archives[0]), '-C', str(self.base/'extracted')], check=True)
                    bundle, = (self.base/'extracted').iterdir()
                    subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=bundle, check=True, capture_output=True)
                    self.assertTrue((bundle/'metadata/online-smoke.json').exists())
                    self.assertTrue((bundle/'repo/scripts/deploy_offline.sh').exists())
                    self.assertTrue((bundle/'tokenizer/tokenizer.json').exists())
                    self.assertFalse((bundle/'.tokenizer-download').exists())
                    meta = read_object(bundle/'metadata/tokenizer.json')
                    self.assertEqual(meta['model'], 'Qwen/Qwen3-0.6B')
                    self.assertIn('tokenizer.json', meta['files'])
                    cfg = read_object(bundle/'metadata/online-smoke.yaml')
                    self.assertTrue(cfg['offline'])
                    self.assertEqual(cfg['benchmark']['dataset_type'], 'tokenid')
                    self.assertEqual(cfg['benchmark']['warmups'], 0)
                    imported = self.deploy(bundle, '--simulation')
                    self.assertEqual(imported.returncode, 0, imported.stderr + imported.stdout)
                    relocated = read_object(bundle/'offline-simulation.yaml')
                    self.assertEqual(relocated['storage']['tokenizer'], str(bundle/'tokenizer'))
                records = self.records()
                exports = [i for i, r in enumerate(records) if r[0] == 'prepare-images' and '--local-only' in r]
                if stage:
                    self.assertFalse(exports)
                else:
                    verify = next(i for i, r in enumerate(records) if r[:2] == ['llm-bench', 'verify'] and str(output) in ' '.join(r))
                    self.assertEqual(len(exports), 1)
                    self.assertLess(verify, exports[0])

    def test_explicit_tokenizer_skips_download_and_is_delivered(self):
        repo = self.online_repo()
        output = self.base / 'local-tokenizer-delivery'
        env = dict(self.env, REPO_URL=str(repo), REF='main', OUTPUT_DIR=str(output),
                   TOKENIZER_DIR=str(ROOT/'configs/fixtures/mock-tokenizer'), FAIL_STAGE='download')
        result = subprocess.run(['bash', str(ROOT/'scripts/build_offline_bundle.sh')],
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(len(list(output.glob('*.tar.gz'))), 1)
        self.assertFalse(any(r[0] == 'download' for r in self.records()))
        metadata, = output.glob('.build-*/llm-bench-offline-*/metadata/tokenizer.json')
        self.assertEqual(read_object(metadata)['provider'], 'local')

    def test_offline_python_defaults_to_39_and_accepts_explicit_newer_version(self):
        repo = self.online_repo()
        for version in ('', '3.9', '3.11'):
            with self.subTest(version=version):
                output = self.base / ('python-' + (version or 'default'))
                env = dict(self.env, REPO_URL=str(repo), OUTPUT_DIR=str(output),
                           OFFLINE_PYTHON=version, TOKENIZER_DIR=str(ROOT/'configs/fixtures/mock-tokenizer'))
                result = subprocess.run(['bash', str(ROOT/'scripts/build_offline_bundle.sh')],
                                        env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                metadata, = output.glob('.build-*/llm-bench-offline-*/metadata/offline-python.txt')
                self.assertEqual(metadata.read_text().strip(), version or '3.9')
                self.assertEqual(len(list(output.glob('*.tar.gz'))), 1)

    def test_unsupported_or_malformed_offline_python_fails_before_build(self):
        for version in ('3.8', '3.9.25', '3.09', '4.0', 'invalid'):
            with self.subTest(version=version):
                output = self.base / ('rejected-' + version)
                result = subprocess.run(['bash', str(ROOT/'scripts/build_offline_bundle.sh')],
                                        env=dict(self.env, OFFLINE_PYTHON=version, OUTPUT_DIR=str(output)),
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
                self.assertIn('OFFLINE_PYTHON must be 3.9 or newer', result.stderr)
                self.assertFalse(output.exists())
        self.assertEqual(self.records(), [])
