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
sys.exit(17 if os.environ.get('FAIL_STAGE') == sys.argv[1] else 0)
'''.replace('#!/usr/bin/env python3', '#!'+sys.executable))

    def records(self):
        return [json.loads(line) for line in self.commands.read_text().splitlines()] if self.commands.exists() else []

    def checksum(self, bundle):
        paths = [p for directory in ('repo', 'images', 'metadata') for p in (bundle/directory).rglob('*') if p.is_file()]
        (bundle/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+str(p.relative_to(bundle))+'\n' for p in sorted(paths)))

    def offline_bundle(self, runtime):
        bundle = self.base/'offline bundle'
        repo = bundle/'repo'
        shutil.copytree(ROOT/'scripts', repo/'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT/'requirements.txt', repo/'requirements.txt')
        self.fake_cli(repo)
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

    def deploy(self, bundle, **env):
        return subprocess.run(['bash', str(bundle/'repo/scripts/deploy_offline.sh')],
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
        for name in ('config_io.py', 'build_offline_bundle.sh', 'deploy_offline.sh'):
            (repo/'scripts').mkdir(exist_ok=True)
            shutil.copyfile(ROOT/'scripts'/name, repo/'scripts'/name)
        for name in ('configs/stacks/nerdctl-offline-smoke.yaml', 'configs/sites/single-node.example.yaml',
                     'configs/delivery/offline-images.yaml', 'docs/single-node-offline.md'):
            target = repo/name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, target)
        (repo/'requirements.txt').write_text('')
        (repo/'tests').mkdir()
        (repo/'tests/test_fixture.py').write_text('import unittest\nclass Fixture(unittest.TestCase):\n    def test_ok(self): pass\n')
        self.fake_cli(repo)
        (repo/'scripts/prepare_offline_images.py').write_text('''from pathlib import Path
import sys
bundle = Path(sys.argv[sys.argv.index('--output')+1])
(bundle/'images').mkdir()
(bundle/'images/images-linux-amd64.tar').write_text('stub images')
''')
        (repo/'scripts/verify_simulation.py').write_text('''from pathlib import Path
root = Path('generated/delivery-smoke'); root.mkdir(parents=True)
(root/'verification.json').write_text('{"passed":true}')
''')
        for command in (['git', 'init', '-b', 'main'], ['git', 'add', '.'],
                        ['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-m', 'Fixture']):
            subprocess.run(command, cwd=repo, check=True, capture_output=True)
        return repo

    def test_online_publishes_archive_only_after_smoke_and_checksums_payload(self):
        repo = self.online_repo()
        for stage in ('up', ''):
            with self.subTest(stage=stage):
                output = self.base/('failed' if stage else 'success')
                env = dict(self.env, REPO_URL=str(repo), REF='main', OUTPUT_DIR=str(output), FAIL_STAGE=stage)
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
        cli = [r[1] for r in self.records() if r[0] == 'llm-bench']
        self.assertEqual(cli, ['images', 'up', 'down', 'images', 'up', 'down'])
