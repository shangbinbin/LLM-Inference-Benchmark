"""Tokenizer assets and relocatable CPU simulation settings for delivery bundles."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from config_io import read_object, write_yaml

ROOT = Path(__file__).resolve().parents[1]
TOKENIZER_MODEL = 'Qwen/Qwen3-0.6B'
TOKENIZER_PATTERNS = ('tokenizer*', 'special_tokens_map.json', 'added_tokens.json',
                      'vocab*', 'merges.txt', '*.model', 'config.json', 'chat_template*')


def prepare_tokenizer(destination, metadata, source=None, revision='master'):
    """Download only tokenizer assets, or copy an explicit local tokenizer, then hash them."""
    destination, metadata = Path(destination), Path(metadata)
    if destination.exists():
        raise FileExistsError('Tokenizer destination already exists: ' + str(destination))
    if source is None:
        from modelscope.hub.snapshot_download import snapshot_download
        # Keep SDK caches outside the delivered tokenizer directory.
        source = destination.parent.parent / '.tokenizer-download'
        snapshot_download(model_id=TOKENIZER_MODEL, revision=revision, local_dir=str(source),
                          allow_file_pattern=list(TOKENIZER_PATTERNS))
        origin = {'provider': 'modelscope', 'model': TOKENIZER_MODEL, 'revision': revision}
    else:
        source = Path(source).expanduser().resolve()
        origin = {'provider': 'local', 'source': str(source)}
    for name in ('tokenizer_config.json', 'tokenizer.json'):
        if not (source / name).is_file():
            raise ValueError('Tokenizer needs ' + str(source / name))
    destination.mkdir(parents=True)
    for path in source.iterdir():
        if path.is_file() and any(path.match(pattern) for pattern in TOKENIZER_PATTERNS):
            shutil.copyfile(path, destination / path.name)
        elif path.is_dir() and path.name == 'chat_templates':
            shutil.copytree(path, destination / path.name)
    # Reject malformed assets before spending time building images. AISBench performs
    # the actual offline tokenizer load/tokenid generation during the mandatory smoke.
    for name in ('tokenizer_config.json', 'tokenizer.json'):
        read_object(destination / name)
    origin['files'] = {str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted(destination.rglob('*')) if p.is_file()}
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(json.dumps(origin, ensure_ascii=False, indent=2) + '\n')


def simulation_config(site, tokenizer, root, logs, *, project, port_base, online=False):
    """Inherit only images/runtime; never copy real endpoints, credentials or log paths."""
    if not 1024 <= port_base <= 65529:
        raise ValueError('SMOKE_PORT_BASE must be 1024..65529')
    cfg = read_object(ROOT / 'configs/stacks/nerdctl-offline-smoke.yaml')
    cfg.update(project=project, task=project, runtime=['docker'] if online else site['runtime'],
               offline=not online)
    cfg['images'] = site['images']
    cfg['storage'].update(root=str(Path(root).resolve()), logs=str(Path(logs).resolve()),
                          tokenizer=str(Path(tokenizer).resolve()))
    cfg['ports'] = {key: port_base + i for i, key in enumerate(cfg['ports'])}
    cfg['benchmark'].update(dataset_type='tokenid', prefix_length=0, input_length=64,
                            output_length=16, request_count=8, concurrency=2, warmups=0, retry=1)
    return cfg


def write_simulation(path, cfg):
    path = Path(path)
    roots = [Path(cfg['storage']['root'])]
    if path.exists():
        roots.append(Path(read_object(path)['storage']['root']))
    if any((root / 'state.json').exists() for root in roots):
        raise ValueError('An active simulation stack exists; use llm-bench down --config before regenerating')
    write_yaml(path, cfg)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--revision', default='master')
    args = parser.parse_args()
    prepare_tokenizer(args.output, args.metadata, args.source, args.revision)


if __name__ == '__main__':
    main()
