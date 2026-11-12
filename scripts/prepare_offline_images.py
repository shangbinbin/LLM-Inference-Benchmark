#!/usr/bin/env python3
"""Prepare a GPU-independent offline image bundle using Docker on an online CPU host."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
COMMON_IMAGES = ('toolbox', 'aisbench', 'prometheus', 'grafana', 'loki', 'alloy', 'node-exporter')
BUILT_IMAGES = {'toolbox', 'aisbench'}
DEFAULT_CONFIG = ROOT / 'configs/delivery/offline-images.json'
DEFAULT_SITE = ROOT / 'configs/sites/gpu41-qwen3-0.6b-random-1k.json'


def image_plan(site, delivery, exporters=None):
    if delivery.get('schema_version') != 1:
        raise ValueError('Delivery schema_version must be 1')
    catalog = delivery.get('gpu_exporters')
    selected = delivery.get('selected_gpu_exporters') if exporters is None else exporters
    additional = delivery.get('additional_images', [])
    if not isinstance(catalog, dict) or any(not isinstance(k, str) or not k or
            not isinstance(v, str) or not v for k, v in catalog.items()):
        raise ValueError('gpu_exporters must map vendor names to site.images keys')
    if not isinstance(selected, list) or any(not isinstance(v, str) or v not in catalog for v in selected):
        raise ValueError('Select exporters from the delivery gpu_exporters catalog')
    if not isinstance(additional, list):
        raise ValueError('additional_images must be an array of image references')
    refs = site.get('images')
    if not isinstance(refs, dict):
        raise ValueError('The site configuration must contain images')
    selected = list(dict.fromkeys(selected))
    images = {}

    def include(role, ref):
        if not isinstance(ref, str) or not ref or ref.startswith('-') or any(c.isspace() for c in ref):
            raise ValueError('Invalid or missing image reference for ' + role)
        entry = images.setdefault(ref, {'image': ref, 'roles': [], 'pull': True})
        if role not in entry['roles']:
            entry['roles'].append(role)
        if role in BUILT_IMAGES:
            entry['pull'] = False

    for role in (*COMMON_IMAGES, *(catalog[v] for v in selected)):
        include(role, refs.get(role))
    for ref in additional:
        include('additional', ref)
    return {'platform': 'linux/amd64', 'gpu_exporters': selected, 'images': list(images.values())}


def prepare_images(plan, bundle):
    metadata = []
    # Validate every selected image before writing an archive or successful metadata.
    for entry in plan['images']:
        ref = entry['image']
        if entry['pull']:
            subprocess.run(['docker', 'pull', '--platform', plan['platform'], ref], check=True)
        info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', ref], text=True))[0]
        if (info['Os'], info['Architecture']) != ('linux', 'amd64'):
            raise ValueError('Wrong architecture: ' + ref)
        metadata.append({'image': ref, 'id': info['Id'], 'digests': info.get('RepoDigests', []),
                         'platform': plan['platform']})
    (bundle / 'images').mkdir(parents=True, exist_ok=True)
    (bundle / 'metadata').mkdir(parents=True, exist_ok=True)
    subprocess.run(['docker', 'image', 'save', '-o', str(bundle / 'images/images-linux-amd64.tar'),
                    *(entry['image'] for entry in plan['images'])], check=True)
    for name, value in (('images.json', metadata), ('image-plan.json', plan)):
        (bundle / 'metadata' / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--site', type=Path, default=DEFAULT_SITE, help='read image names only; no GPU/tokenizer/service validation')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG, help='delivery image selection JSON')
    parser.add_argument('--exporters', nargs='+', help='override selected_gpu_exporters, e.g. nvidia hygon')
    parser.add_argument('--output', type=Path, default=Path('generated/offline-bundle'))
    parser.add_argument('--plan', action='store_true', help='print image selection without contacting Docker or pulling images')
    args = parser.parse_args()
    try:
        plan = image_plan(json.loads(args.site.read_text()), json.loads(args.config.read_text()), args.exporters)
        if args.plan:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        else:
            prepare_images(plan, args.output)
            print('Exported ' + str(len(plan['images'])) + ' images to ' + str(args.output.resolve()))
    except (ValueError, KeyError, TypeError, IndexError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, 'ERROR: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
