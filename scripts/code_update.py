"""Build/apply source updates to an existing offline bundle without changing images."""
import argparse
import ast
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import uuid

from config_io import read_data


def safe_path(root, name):
    parts = PurePosixPath(name).parts
    if not parts or name != str(PurePosixPath(name)) or parts[0] == '/' or any(p in ('.', '..') for p in parts) or '\n' in name or '\\' in name:
        raise ValueError('Invalid payload path: ' + name)
    path = root.joinpath(*parts)
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('Symlinks are not supported in managed paths: ' + str(parent))
        if parent == root:
            break
    return path


def file_info(path):
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise ValueError('Expected a regular file: ' + str(path))
    return {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'mode': 0o755 if path.stat().st_mode & 0o111 else 0o644}


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if '__pycache__' in path.relative_to(root).parts or path.suffix == '.pyc':
            continue
        safe_path(root, name)
        if path.is_file():
            if PurePosixPath(name).parts[0] in ('.git', 'generated', 'results', '.llm-bench', '.venv'):
                raise ValueError('Runtime data cannot be shipped as managed source: ' + name)
            result[name] = file_info(path)
    return result


def image_contract(root, files=None):
    files = inventory(root) if files is None else files
    protected = {name: file_info(safe_path(root, name)) for name in files
                 if name.startswith('containers/') or name in
                 ('requirements.txt', 'requirements-build.txt', '.dockerignore')}
    source = root / 'scripts/benchmark_stack.py'
    methods = []
    if source.exists():
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.ClassDef) and node.name == 'Stack':
                methods = [ast.dump(n, include_attributes=False) for n in node.body
                           if isinstance(n, ast.FunctionDef) and n.name == 'build_one']
    images = set()
    for name in files:
        if name.startswith(('configs/stacks/', 'configs/sites/')) and Path(name).suffix in ('.yaml', '.yml', '.json'):
            cfg = read_data(root / name)
            if isinstance(cfg, dict):
                images.update(cfg.get('images', {}).values())
    return {'files': protected, 'build_methods': methods, 'images': sorted(images)}


def check_compatibility(base, source):
    if image_contract(base) != image_contract(source):
        raise ValueError('Image inputs/references or requirements changed; build a full offline bundle instead')


def checksum_entries(root):
    result = {}
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match or match[2] in result:
            raise ValueError('Invalid or duplicate SHA256SUMS entry')
        safe_path(root, match[2])
        result[match[2]] = match[1]
    return result


def checksum_text(entries):
    return ''.join(f'{value}  {name}\n' for name, value in sorted(entries.items()))


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def pack(base, source, output, base_revision, revision, test_log):
    check_compatibility(base, source)
    if not all(re.fullmatch('[0-9a-f]{40}', value) for value in (base_revision, revision)) or base_revision == revision:
        raise ValueError('Expected different full base/target Git commit IDs')
    old, new = inventory(base), inventory(source)
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    shutil.copytree(source, output / 'repo', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (output / 'metadata').mkdir()
    manifest = {'schema_version': 1, 'kind': 'code-update', 'base_revision': base_revision,
                'revision': revision, 'base_files': old, 'files': new,
                'image_contract': image_contract(source)}
    (output / 'metadata/code-update.json').write_text(json_text(manifest))
    shutil.copyfile(test_log, output / 'metadata/unit-tests.log')
    entries = {name: info['sha256'] for folder in ('repo', 'metadata')
               for name, info in ((str(p.relative_to(output)), file_info(p))
                                  for p in (output / folder).rglob('*') if p.is_file())}
    (output / 'SHA256SUMS').write_text(checksum_text(entries))


@contextmanager
def stopped_environment(target, configs):
    """Serialize updates and lock all known stack roots against concurrent lifecycle calls."""
    with ExitStack() as stack:
        lock = stack.enter_context(safe_path(target, '.code-update.lock').open('a'))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        roots = set()
        for path in [target / 'site.yaml', target / 'offline-simulation.yaml', *configs]:
            if path.exists():
                cfg = read_data(path)
                if isinstance(cfg, dict) and cfg.get('storage', {}).get('root'):
                    roots.add((target / 'repo' / Path(cfg['storage']['root']).expanduser()).resolve())
            elif path in configs:
                raise FileNotFoundError(path)
        for folder in (target, target / 'repo'):
            for path in (folder / '.llm-bench').glob('*.json'):
                roots.add(Path(json.loads(path.read_text())['root']).resolve())
            for path in (folder / 'generated').glob('*/state.json'):
                roots.add(path.parent)
        for root in sorted(roots):
            if root.is_dir():
                handle = stack.enter_context((root / '.llm-bench.lock').open('a'))
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if any((root / name).exists() for name in ('state.json', 'suite.lock')):
                raise ValueError('Active stack/workload found in ' + str(root) + '; use the OLD llm-bench down first')
        yield


def verify_files(root, files):
    conflicts = [name for name, info in files.items() if file_info(safe_path(root, name)) != info]
    if conflicts:
        raise ValueError('Managed files differ from expected version; preserve/reconcile local changes first: ' + ', '.join(conflicts[:12]))


def replace_file(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.code-update-')
    os.close(fd)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def invalidate_bytecode(target, names):
    # Archive timestamps can match old timestamps at one-second resolution. A
    # same-size source edit must not accidentally reuse old timestamp-based pyc.
    for name in names:
        if name.startswith('repo/') and name.endswith('.py'):
            source = safe_path(target, name)
            cache = safe_path(target, str(source.parent.relative_to(target) / '__pycache__'))
            for path in cache.glob(source.stem + '.*.pyc'):
                safe_path(target, str(path.relative_to(target))).unlink()


def restore(target, backup, journal):
    # Remove added files before restoring old files and integrity metadata.
    for name, info in journal['before'].items():
        if info is None:
            safe_path(target, name).unlink(missing_ok=True)
    for name, info in journal['before'].items():
        if info is not None:
            replace_file(safe_path(target, name), safe_path(backup / 'files', name))
    invalidate_bytecode(target, journal['before'])
    journal['status'] = 'rolled-back'
    (backup / 'journal.json').write_text(json_text(journal))


def apply_update(update, target, configs):
    if update == target or not (target / 'repo').is_dir() or not (target / 'metadata/revision.txt').is_file():
        raise ValueError('TARGET must be an existing full offline bundle, separate from the update package')
    checksums = checksum_entries(update)
    for name, digest in checksums.items():
        info = file_info(safe_path(update, name))
        if info is None or info['sha256'] != digest:
            raise ValueError('Update checksum mismatch: ' + name)
    manifest = json.loads((update / 'metadata/code-update.json').read_text())
    if manifest.get('kind') != 'code-update' or manifest.get('schema_version') != 1:
        raise ValueError('Unsupported code update manifest')
    if inventory(update / 'repo') != manifest['files']:
        raise ValueError('Source payload differs from update manifest')
    expected = {'repo/' + name for name in manifest['files']} | {'metadata/code-update.json', 'metadata/unit-tests.log'}
    if set(checksums) != expected:
        raise ValueError('Update checksum list must cover exactly the source and metadata payload')
    with stopped_environment(target, configs):
        for journal_path in (target / '.code-updates').glob('*/journal.json'):
            if json.loads(journal_path.read_text()).get('status') == 'applying':
                raise ValueError('Interrupted update found; rollback backup ' + journal_path.parent.name + ' first')
        base_revision = (target / 'metadata/revision.txt').read_text().strip()
        if base_revision != manifest['base_revision']:
            raise ValueError('Wrong base revision: target=' + base_revision + ', required=' + manifest['base_revision'])
        verify_files(target / 'repo', manifest['base_files'])
        if image_contract(target / 'repo', manifest['base_files']) != image_contract(update / 'repo'):
            raise ValueError('Image inputs/references or requirements changed; use a full offline bundle')
        if image_contract(update / 'repo') != manifest['image_contract']:
            raise ValueError('Image compatibility manifest mismatch')
        old, new = manifest['base_files'], manifest['files']
        changed = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
        for name in new.keys() - old.keys():
            if safe_path(target / 'repo', name).exists():
                raise ValueError('New managed file would overwrite a custom file: ' + name)
        backup_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
        backup = safe_path(target, '.code-updates/' + backup_id)
        staged = backup / 'staged'
        staged.mkdir(parents=True)
        writes = {}
        for name in changed:
            writes['repo/' + name] = update / 'repo' / name if name in new else None
        metadata = {
            'metadata/revision.txt': manifest['revision'] + '\n',
            'metadata/image-revision.txt': ((target / 'metadata/image-revision.txt').read_text()
                                            if (target / 'metadata/image-revision.txt').exists() else base_revision + '\n'),
            'metadata/code-update.json': json_text({'base_revision': base_revision, 'revision': manifest['revision'],
                                                    'backup_id': backup_id, 'container_smoke_run': False}),
            'metadata/code-update-tests.log': (update / 'metadata/unit-tests.log').read_text(),
        }
        for name, content in metadata.items():
            path = safe_path(staged, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            writes[name] = path
        entries = {name: digest for name, digest in checksum_entries(target).items()
                   if not name.startswith('repo/') and name not in metadata}
        entries.update({'repo/' + name: info['sha256'] for name, info in new.items()})
        entries.update({name: file_info(path)['sha256'] for name, path in writes.items() if name in metadata})
        (staged / 'SHA256SUMS').write_text(checksum_text(entries))
        writes['SHA256SUMS'] = staged / 'SHA256SUMS'
        before = {name: file_info(safe_path(target, name)) for name in writes}
        after = {name: file_info(source) if source else None for name, source in writes.items()}
        for name, info in before.items():
            if info is not None:
                saved = safe_path(backup / 'files', name)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(safe_path(target, name), saved)
        journal = {'status': 'applying', 'base_revision': base_revision, 'revision': manifest['revision'],
                   'before': before, 'after': after}
        (backup / 'journal.json').write_text(json_text(journal))
        try:
            for name, source in writes.items():
                path = safe_path(target, name)
                if source is None:
                    path.unlink()
                else:
                    replace_file(path, source)
            invalidate_bytecode(target, writes)
            verify_files(target / 'repo', new)
            journal['status'] = 'applied'
            (backup / 'journal.json').write_text(json_text(journal))
        except BaseException:
            restore(target, backup, journal)
            raise
        print('Code update applied: ' + base_revision + ' -> ' + manifest['revision'])
        print('Backup ID: ' + backup_id + '; no containers or workloads were started')


def rollback(target, backup_id, configs):
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', backup_id):
        raise ValueError('Invalid backup ID')
    backup = safe_path(target, '.code-updates/' + backup_id)
    journal = json.loads((backup / 'journal.json').read_text())
    with stopped_environment(target, configs):
        if journal['status'] not in ('applied', 'applying'):
            raise ValueError('Backup has already been rolled back')
        if journal['status'] == 'applied':
            verify_files(target, journal['after'])
        else:
            for name in journal['before']:
                info = file_info(safe_path(target, name))
                if info != journal['before'][name] and info != journal['after'][name]:
                    raise ValueError('Interrupted update has conflicting local edits: ' + name)
        verify_files(backup / 'files', {n: v for n, v in journal['before'].items() if v is not None})
        restore(target, backup, journal)
        print('Code update rolled back to ' + journal['base_revision'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'pack'):
        sub = commands.add_parser(name)
        sub.add_argument('--base', type=Path, required=True)
        sub.add_argument('--source', type=Path, required=True)
        if name == 'pack':
            for field in ('base-revision', 'revision', 'test-log', 'output'):
                sub.add_argument('--' + field, required=True, type=Path if field in ('test-log', 'output') else str)
    for name in ('apply', 'rollback'):
        sub = commands.add_parser(name)
        sub.add_argument('--target', type=Path, required=True)
        sub.add_argument('--config', action='append', default=[], type=Path)
        sub.add_argument('--update' if name == 'apply' else '--backup', required=True,
                         type=Path if name == 'apply' else str)
    args = parser.parse_args()
    if sys.version_info < (3, 9):
        parser.error('Requires Python 3.9 or newer')
    try:
        if args.command == 'check':
            check_compatibility(args.base.resolve(), args.source.resolve())
        elif args.command == 'pack':
            pack(args.base.resolve(), args.source.resolve(), args.output.resolve(), args.base_revision, args.revision, Path(args.test_log))
        elif args.command == 'apply':
            apply_update(args.update.resolve(), args.target.resolve(), [p.resolve() for p in args.config])
        else:
            rollback(args.target.resolve(), args.backup, [p.resolve() for p in args.config])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, 'ERROR: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
