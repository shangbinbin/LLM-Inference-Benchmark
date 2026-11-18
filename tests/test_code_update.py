import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import code_update as updater
from config_io import write_yaml

BASE = 'a' * 40
NEW = 'b' * 40


class CodeUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / 'base'
        self.base.mkdir()
        (self.base / 'scripts').mkdir()
        for name in ('code_update.py', 'config_io.py', 'apply_code_update.sh', 'build_code_update.sh'):
            shutil.copy2(ROOT / 'scripts' / name, self.base / 'scripts' / name)
        (self.base / 'requirements.txt').write_text('PyYAML==6.0.3\n')
        (self.base / 'requirements-bootstrap.txt').write_text('pip==25.3\n')
        (self.base / 'containers/toolbox').mkdir(parents=True)
        (self.base / 'containers/toolbox/Dockerfile').write_text('FROM python:3.11\n')
        (self.base / 'app.py').write_text('print("old")\n')
        (self.base / 'removed.txt').write_text('removed in new version')
        (self.base / 'configs/sites').mkdir(parents=True)
        write_yaml(self.base / 'configs/sites/example.yaml', {'images': {'toolbox': 'toolbox:1'}})
        self.source = self.root / 'new'
        shutil.copytree(self.base, self.source)
        (self.source / 'app.py').write_text('print("new")\n')
        (self.source / 'removed.txt').unlink()
        (self.source / 'added.txt').write_text('new file')
        self.log = self.root / 'tests.log'
        self.log.write_text('Unit tests passed')
        self.update = self.root / 'update'
        self.target = self.root / 'existing bundle'
        shutil.copytree(self.base, self.target / 'repo')
        (self.target / 'metadata').mkdir()
        (self.target / 'metadata/revision.txt').write_text(BASE + '\n')
        (self.target / 'metadata/offline-python.txt').write_text('3.9\n')
        for name in ('images/archive.tar', 'tokenizer/tokenizer.json', 'wheelhouse/retained.whl',
                     'generated/results/report.txt', 'repo/generated/data.txt', 'repo/configs/sites/custom.yaml'):
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('retained payload')
        # A valid untracked site is preserved without becoming part of the image contract.
        write_yaml(self.target / 'repo/configs/sites/custom.yaml', {'images': {'toolbox': 'custom:2'}})
        write_yaml(self.target / 'site.yaml', {'storage': {'root': str(self.target / 'data')}})
        entries = {str(p.relative_to(self.target)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in self.target.rglob('*') if p.is_file() and not str(p.relative_to(self.target)).startswith('generated/')}
        (self.target / 'SHA256SUMS').write_text(updater.checksum_text(entries))
        self.original = {str(p.relative_to(self.target)): p.read_bytes() for p in self.target.rglob('*') if p.is_file()}

    def pack(self):
        updater.pack(self.base, self.source, self.update, BASE, NEW, self.log)

    def backup_id(self):
        return json.loads((self.target / 'metadata/code-update.json').read_text())['backup_id']

    def assert_integrity(self):
        for name, digest in updater.checksum_entries(self.target).items():
            self.assertEqual(hashlib.sha256((self.target / name).read_bytes()).hexdigest(), digest, name)

    def test_apply_preserves_environment_removes_obsolete_files_and_rolls_back(self):
        self.pack()
        updater.apply_update(self.update, self.target, [])
        self.assertEqual((self.target / 'repo/app.py').read_text(), 'print("new")\n')
        self.assertFalse((self.target / 'repo/removed.txt').exists())
        self.assertEqual((self.target / 'metadata/revision.txt').read_text().strip(), NEW)
        self.assertEqual((self.target / 'metadata/image-revision.txt').read_text().strip(), BASE)
        for name in ('site.yaml', 'images/archive.tar', 'tokenizer/tokenizer.json', 'wheelhouse/retained.whl',
                     'generated/results/report.txt', 'repo/generated/data.txt', 'repo/configs/sites/custom.yaml'):
            self.assertEqual((self.target / name).read_bytes(), self.original[name])
        self.assert_integrity()
        backup = self.backup_id()
        updater.rollback(self.target, backup, [])
        for name, content in self.original.items():
            self.assertEqual((self.target / name).read_bytes(), content, name)
        self.assertFalse((self.target / 'repo/added.txt').exists())
        self.assertFalse((self.target / 'metadata/image-revision.txt').exists())
        self.assert_integrity()

    def test_changed_requirements_container_inputs_and_image_tags_require_full_bundle(self):
        for name, content in (('requirements.txt', 'other-dependency\n'),
                              ('requirements-bootstrap.txt', 'pip==26.0\n'),
                              ('containers/toolbox/Dockerfile', 'FROM python:3.12\n'),
                              ('configs/sites/example.yaml', 'images: {toolbox: "toolbox:2"}\n')):
            with self.subTest(name=name):
                original = (self.source / name).read_text()
                (self.source / name).write_text(content)
                with self.assertRaisesRegex(ValueError, 'full offline bundle'):
                    updater.check_compatibility(self.base, self.source)
                (self.source / name).write_text(original)

    def test_same_size_same_timestamp_python_edit_does_not_reuse_stale_bytecode(self):
        command = [sys.executable, '-c', 'import app']
        self.assertEqual(subprocess.check_output(command, cwd=self.target / 'repo', text=True).strip(), 'old')

        timestamp = (self.target / 'repo/app.py').stat().st_mtime
        os.utime(self.source / 'app.py', (timestamp, timestamp))
        self.pack()
        updater.apply_update(self.update, self.target, [])
        self.assertEqual(subprocess.check_output(command, cwd=self.target / 'repo', text=True).strip(), 'new')
        updater.rollback(self.target, self.backup_id(), [])
        self.assertEqual(subprocess.check_output(command, cwd=self.target / 'repo', text=True).strip(), 'old')

    def test_extracted_shell_entrypoint_applies_and_rolls_back_with_existing_python(self):
        self.pack()
        script = self.update / 'repo/scripts/apply_code_update.sh'
        result = subprocess.run(['bash', str(script), str(self.target)],
                                env=dict(os.environ, PYTHON=sys.executable), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_integrity()
        result = subprocess.run(['bash', str(script), str(self.target), '--rollback', self.backup_id()],
                                env=dict(os.environ, PYTHON=sys.executable), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_integrity()

    def test_wrong_baseline_local_edits_corrupt_payload_and_new_file_collision_are_rejected(self):
        self.pack()
        (self.target / 'metadata/revision.txt').write_text('wrong')
        with self.assertRaisesRegex(ValueError, 'Wrong base revision'):
            updater.apply_update(self.update, self.target, [])
        (self.target / 'metadata/revision.txt').write_text(BASE + '\n')
        (self.target / 'repo/app.py').write_text('local edit')
        with self.assertRaisesRegex(ValueError, 'local changes'):
            updater.apply_update(self.update, self.target, [])
        (self.target / 'repo/app.py').write_bytes(self.original['repo/app.py'])
        (self.target / 'repo/added.txt').write_text('user file')
        with self.assertRaisesRegex(ValueError, 'custom file'):
            updater.apply_update(self.update, self.target, [])
        (self.target / 'repo/added.txt').unlink()
        (self.update / 'repo/app.py').write_text('corrupt payload')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            updater.apply_update(self.update, self.target, [])
        self.assertFalse((self.target / '.code-updates').exists())

    def test_active_external_stack_blocks_apply_and_rollback(self):
        self.pack()
        external = self.root / 'external-state'; external.mkdir()
        cfg = self.root / 'custom-site.yaml'
        write_yaml(cfg, {'storage': {'root': str(external)}})
        (external / 'state.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'OLD llm-bench down'):
            updater.apply_update(self.update, self.target, [cfg])
        (external / 'state.json').unlink()
        updater.apply_update(self.update, self.target, [cfg])
        (external / 'state.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'OLD llm-bench down'):
            updater.rollback(self.target, self.backup_id(), [cfg])

    def test_copy_failure_restores_source_revision_and_integrity_metadata(self):
        self.pack()
        original_replace = updater.replace_file
        calls = 0
        def fail_once(path, source):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('injected copy failure')
            original_replace(path, source)
        with patch.object(updater, 'replace_file', side_effect=fail_once):
            with self.assertRaisesRegex(OSError, 'injected copy failure'):
                updater.apply_update(self.update, self.target, [])
        for name, content in self.original.items():
            self.assertEqual((self.target / name).read_bytes(), content, name)
        self.assertFalse((self.target / 'repo/added.txt').exists())
        self.assert_integrity()

    def test_symlink_escape_is_rejected(self):
        self.pack()
        (self.target / 'repo/app.py').unlink()
        external = self.root / 'external.py'; external.write_text('do not modify')
        (self.target / 'repo/app.py').symlink_to(external)
        with self.assertRaisesRegex(ValueError, 'Symlinks'):
            updater.apply_update(self.update, self.target, [])
        self.assertEqual(external.read_text(), 'do not modify')

    def test_interrupted_update_can_be_recovered_and_rollback_rejects_new_local_edits(self):
        self.pack()
        updater.apply_update(self.update, self.target, [])
        backup = self.backup_id()
        (self.target / 'repo/app.py').write_text('new local work')
        with self.assertRaisesRegex(ValueError, 'local changes'):
            updater.rollback(self.target, backup, [])
        # A power loss can leave a mix of old/new bytes with an applying journal.
        (self.target / 'repo/app.py').write_bytes(self.original['repo/app.py'])
        path = self.target / '.code-updates' / backup / 'journal.json'
        journal = json.loads(path.read_text()); journal['status'] = 'applying'
        path.write_text(json.dumps(journal))
        with self.assertRaisesRegex(ValueError, 'Interrupted update'):
            updater.apply_update(self.update, self.target, [])
        updater.rollback(self.target, backup, [])
        self.assert_integrity()

    def test_online_shell_creates_code_only_package_without_runtime_or_tokenizer(self):
        origin = self.root / 'origin'
        shutil.copytree(self.base, origin)
        (origin / 'tests').mkdir()
        (origin / 'tests/test_ok.py').write_text('import unittest\nclass Test(unittest.TestCase):\n    def test_ok(self): pass\n')
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=origin, text=True).strip()
        git('init', '-q')
        git('symbolic-ref', 'HEAD', 'refs/heads/main')
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'base')
        base = git('rev-parse', 'HEAD')
        (origin / 'app.py').write_text('print("new")\n')
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'new')
        output = self.root / 'delivery'
        # Stub only pip/venv setup; all Git, unit-test, manifest and tar operations are real.
        fakebin = self.root / 'bin'; fakebin.mkdir()
        real_python = sys.executable
        wrapper = fakebin / 'python3'
        wrapper.write_text(f'''#!/bin/bash
if [[ $1 == -m && $2 == venv ]]; then
  mkdir -p "$3/bin"
  cat > "$3/bin/python" <<'PYTHON'
#!/bin/bash
if [[ $1 == -m && $2 == pip ]]; then exit 0; fi
exec {real_python} "$@"
PYTHON
  chmod +x "$3/bin/python"
  exit 0
fi
exec {real_python} "$@"
''')
        wrapper.chmod(0o755)
        for command in ('docker', 'nerdctl'):
            path = fakebin / command; path.write_text('#!/bin/sh\nexit 99\n'); path.chmod(0o755)
        result = subprocess.run(['bash', str(ROOT / 'scripts/build_code_update.sh')],
                                env=dict(os.environ, PATH=str(fakebin)+':'+os.environ['PATH'],
                                         BASE_REF=base, REF='main', REPO_URL=str(origin), OUTPUT_DIR=str(output)),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        archive, = output.glob('*.tar.gz')
        extracted = self.root / 'extracted'; extracted.mkdir()
        subprocess.run(['tar', '-xzf', str(archive), '-C', str(extracted)], check=True)
        package, = extracted.iterdir()
        self.assertEqual(set(p.name for p in package.iterdir()), {'repo', 'metadata', 'SHA256SUMS'})
        subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=package, capture_output=True, check=True)
        self.assertEqual(json.loads((package / 'metadata/code-update.json').read_text())['base_revision'], base)
        for failure in ('tests', 'image'):
            with self.subTest(failure=failure):
                if failure == 'tests':
                    (origin / 'tests/test_ok.py').write_text('raise RuntimeError("test failure")\n')
                else:
                    (origin / 'tests/test_ok.py').write_text('import unittest\n')
                    (origin / 'containers/toolbox/Dockerfile').write_text('FROM python:3.12\n')
                git('add', '.')
                git('-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', failure)
                failed_output = self.root / ('failed-' + failure)
                result = subprocess.run(['bash', str(ROOT / 'scripts/build_code_update.sh')],
                                        env=dict(os.environ, PATH=str(fakebin)+':'+os.environ['PATH'], BASE_REF=base,
                                                 REF='main', REPO_URL=str(origin), OUTPUT_DIR=str(failed_output)),
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(list(failed_output.glob('*.tar.gz')))
                if failure == 'tests':
                    self.assertIn('Code update failed at stage: unit-tests', result.stdout + result.stderr)
                    self.assertIn('RuntimeError: test failure', result.stdout + result.stderr)
