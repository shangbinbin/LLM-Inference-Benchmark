import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_offline_images import DEFAULT_CONFIG, DEFAULT_SITE, image_plan, prepare_images
from benchmark_stack import Stack, load_config, prepare


class OfflineImageTests(unittest.TestCase):
    def setUp(self):
        self.site = json.loads(DEFAULT_SITE.read_text())
        self.delivery = json.loads(DEFAULT_CONFIG.read_text())

    def test_default_delivery_contains_both_exporters_independently_of_runtime_vendor(self):
        for vendor in ('nvidia', 'hygon', 'auto'):
            self.site['gpu']['vendor'] = vendor
            plan = image_plan(self.site, self.delivery)
            self.assertEqual(plan['gpu_exporters'], ['nvidia', 'hygon'])
            self.assertEqual(len(plan['images']), 9)
            self.assertEqual({e['image'] for e in plan['images']}, set(self.site['images'].values()))
            self.assertEqual(sum(e['pull'] for e in plan['images']), 7)

    def test_exporter_selection_extension_and_deduplication(self):
        plan = image_plan(self.site, self.delivery, ['hygon'])
        self.assertEqual(len(plan['images']), 8)
        self.assertNotIn(self.site['images']['nvidia_exporter'], [e['image'] for e in plan['images']])
        self.delivery['gpu_exporters']['other'] = 'other_exporter'
        self.site['images']['other_exporter'] = 'internal/other:1'
        self.delivery['additional_images'] = ['internal/sidecar:1', 'internal/sidecar:1']
        plan = image_plan(self.site, self.delivery, ['other', 'other'])
        self.assertEqual(plan['gpu_exporters'], ['other'])
        self.assertEqual(len(plan['images']), 9)
        self.assertEqual([e['image'] for e in plan['images']][-2:], ['internal/other:1', 'internal/sidecar:1'])
        for exporters in (['dcu'], ['unknown'], 'nvidia'):
            with self.assertRaises(ValueError):
                image_plan(self.site, self.delivery, exporters)
        self.site['images'].pop('hygon_exporter')
        with self.assertRaisesRegex(ValueError, 'hygon_exporter'):
            image_plan(self.site, self.delivery)

    def test_prepare_keeps_built_images_local_and_architecture_failure_blocks_archive(self):
        plan = image_plan(self.site, self.delivery)
        info = {'Os':'linux', 'Architecture':'amd64', 'Id':'sha256:test', 'RepoDigests':[]}
        with tempfile.TemporaryDirectory() as tmp, patch('prepare_offline_images.subprocess.run') as run, \
                patch('prepare_offline_images.subprocess.check_output', return_value=json.dumps([info])):
            metadata = prepare_images(plan, Path(tmp))
            pulls = [c.args[0][-1] for c in run.call_args_list if c.args[0][1] == 'pull']
            self.assertEqual(set(pulls), {e['image'] for e in plan['images'] if e['pull']})
            self.assertEqual(len(metadata), 9)
            self.assertEqual(json.loads((Path(tmp)/'metadata/image-plan.json').read_text()), plan)
            self.assertEqual(run.call_args_list[-1].args[0][1:3], ['image', 'save'])
        info['Architecture'] = 'arm64'
        with tempfile.TemporaryDirectory() as tmp, patch('prepare_offline_images.subprocess.run') as run, \
                patch('prepare_offline_images.subprocess.check_output', return_value=json.dumps([info])):
            with self.assertRaisesRegex(ValueError, 'Wrong architecture'):
                prepare_images(plan, Path(tmp))
            run.assert_not_called()
            self.assertFalse((Path(tmp)/'metadata/images.json').exists())

    def test_each_runtime_starts_and_requires_only_the_selected_exporter(self):
        for runtime in (['docker'], ['nerdctl', '--namespace', 'benchmark']):
            for vendor, other in (('nvidia','hygon'), ('hygon','nvidia')):
                with self.subTest(runtime=runtime, vendor=vendor), tempfile.TemporaryDirectory() as tmp:
                    cfg = load_config(ROOT/'configs/stacks/mac-offline.json')
                    cfg.update(runtime=runtime, simulation=False, platform='linux/amd64')
                    cfg['gpu']['vendor'] = vendor
                    cfg['images'] = copy.deepcopy(self.site['images'])
                    cfg['images'].pop(other+'_exporter')
                    cfg['storage'].update(root=str(Path(tmp)/'state'), logs=str(Path(tmp)/'logs'))
                    directory, selected = prepare(cfg)
                    instance = Stack(cfg, directory, selected)
                    specs = instance.specs()
                    self.assertEqual([s['image'] for s in specs if s['name']=='gpu-exporter'],
                                     [cfg['images'][vendor+'_exporter']])
                    with patch('benchmark_stack.check_local_images') as check:
                        instance.preflight_images()
                        self.assertNotIn(self.site['images'][other+'_exporter'], check.call_args.args[1])

    def test_plan_runs_without_docker_gpu_tokenizer_or_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, str(ROOT/'scripts/prepare_offline_images.py'), '--plan'],
                                    cwd=tmp, text=True, capture_output=True, env={'PATH':''})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)['images']), 9)
            self.assertEqual(list(Path(tmp).iterdir()), [])
