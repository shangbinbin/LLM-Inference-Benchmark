from contextlib import redirect_stdout
import io
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
from log_health import host_files, require_log_files, container_report, check_logs, PROBE
from llm_bench import execute, parser_for_cli


class LogHealthTests(unittest.TestCase):
    def config(self, base):
        cfg = stack.load_config(ROOT/'configs/stacks/mac-offline.json')
        cfg.update(simulation=False, platform='linux/amd64', runtime=['docker'])
        cfg['storage'].update(root=str(base/'state'), logs=str(base/'logs'))
        cfg['log_sources'] = [{'path':'server.log', 'role':'master', 'replica':'replica-0'}]
        return cfg

    def test_wrong_filename_fails_before_runtime_and_does_not_create_log_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);cfg=self.config(base)
            directory,vendor=stack.prepare(cfg)
            self.assertFalse((base/'logs').exists())
            (base/'logs').mkdir();(base/'logs/server_log.log').write_text('real service output\n')
            instance=stack.Stack(cfg,directory,vendor)
            with patch.object(instance,'call') as call, self.assertRaisesRegex(ValueError,'log_sources') as error:
                instance.up(False)
            self.assertIn(str(base/'logs/server.log'),str(error.exception))
            call.assert_not_called()
            self.assertFalse(instance.statefile.exists())
            self.assertEqual(check_logs(cfg,stack.runtime_command)['status'],'error')

    def test_existing_empty_file_and_explicit_delayed_file_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);cfg=self.config(base);(base/'logs').mkdir()
            (base/'logs/server.log').touch()
            require_log_files(cfg)
            self.assertEqual(host_files(cfg)[0]['status'],'empty')
            (base/'logs/server.log').unlink()
            cfg['allow_missing_logs']=True
            require_log_files(cfg)
            self.assertEqual(check_logs(cfg,stack.runtime_command)['status'],'pending')
            cfg['allow_missing_logs']='true'
            with self.assertRaisesRegex(ValueError,'allow_missing_logs'):
                stack.validate_config(cfg)
            cfg.update(simulation=True,allow_missing_logs=False)
            require_log_files(cfg)

    def test_unreadable_container_mount_stops_before_network_or_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);cfg=self.config(base);(base/'logs').mkdir();(base/'logs/server.log').touch()
            directory,vendor=stack.prepare(cfg);instance=stack.Stack(cfg,directory,vendor)
            remote={'files':[{'path':'/logs/server.log','source_file':'server.log','status':'unreadable'}]}
            with patch.object(instance,'call') as call,patch.object(instance,'preflight_images'), \
                    patch.object(instance,'check_image'),patch('benchmark_stack.container_report',return_value=remote):
                with self.assertRaisesRegex(ValueError,'inside /logs'):
                    instance.up(False)
            self.assertFalse(any(c.args[:2]==('network','create') for c in call.call_args_list))
            self.assertFalse(instance.statefile.exists())

    def test_unreadable_host_file_is_decided_by_alloy_container_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);cfg=self.config(base);(base/'logs').mkdir()
            item={'source_file':'server.log','path':str(base/'logs/server.log'),'status':'unreadable'}
            remote={'files':[dict(item,status='readable')], 'alloy':{'ready':True},
                    'loki':{'ready':True,'logs_found':True}}
            with patch('log_health.host_files',return_value=[item]),patch('log_health.container_report',return_value=remote):
                require_log_files(cfg)
                self.assertEqual(check_logs(cfg,stack.runtime_command,'project-net')['status'],'ok')

    def test_container_check_catches_symlink_target_outside_mount_without_showing_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);cfg=self.config(base);(base/'logs').mkdir()
            (base/'private.log').write_text('sensitive log payload\n')
            (base/'logs/server.log').symlink_to(base/'private.log')
            require_log_files(cfg)
            remote={'files':[{'path':'/logs/server.log','source_file':'server.log','status':'missing'}],
                    'alloy':{'ready':True},'loki':{'ready':True,'logs_found':False}}
            with patch('log_health.container_report',return_value=remote):
                report=check_logs(cfg,stack.runtime_command,'project-net')
            self.assertEqual(report['status'],'error')
            self.assertNotIn('sensitive log payload',json.dumps(report))

    def test_readonly_probe_obeys_runtime_security_and_does_not_forward_token(self):
        for runtime in (['docker'],['nerdctl','--namespace','benchmark']):
            with self.subTest(runtime=runtime),tempfile.TemporaryDirectory() as tmp:
                cfg=self.config(Path(tmp));cfg['runtime']=runtime
                cfg['inference']['api_key_env']='MISSING_SECRET'
                reply={'files':[{'status':'readable'}]}
                with patch('log_health.subprocess.run',return_value=subprocess.CompletedProcess([],0,json.dumps(reply),'')) as run:
                    container_report(cfg,stack.runtime_command)
                command=run.call_args.args[0]
                self.assertEqual(command[:len(runtime)],runtime)
                self.assertIn('--pull=never',command)
                self.assertIn('seccomp=unconfined',command)
                self.assertIn('--user',command)
                self.assertIn('0:0',command)
                self.assertIn('dst=/logs,readonly',','.join(command))
                self.assertNotIn('MISSING_SECRET',command)
                self.assertEqual(command[command.index('--network')+1],'none')

    def test_loki_window_without_entries_is_pending_not_a_dashboard_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);cfg=self.config(base);(base/'logs').mkdir();(base/'logs/server.log').write_text('existing line\n')
            remote={'files':host_files(cfg),'alloy':{'ready':True},'loki':{'ready':True,'labels':[],'logs_found':False}}
            with patch('log_health.container_report',return_value=remote):
                report=check_logs(cfg,stack.runtime_command,'project-net')
            self.assertEqual(report['status'],'pending')
            self.assertIn('lookback',report['hint'])

    def test_logs_check_cli_works_before_up_without_gpu_tokenizer_or_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);cfg=self.config(base);site=base/'site.json';site.write_text(json.dumps(cfg))
            result=subprocess.run([str(ROOT/'bin/llm-bench'),'logs','check','--config',str(site)],text=True,capture_output=True)
            self.assertEqual(result.returncode,1,result.stderr)
            report=json.loads(result.stdout)
            self.assertIn('no active stack',report['config_source'])
            self.assertEqual(report['host_files'][0]['status'],'missing')
            (base/'logs').mkdir();(base/'logs/server.log').touch()
            result=subprocess.run([str(ROOT/'bin/llm-bench'),'logs','check','--config',str(site)],text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'],'pending')

    def test_probe_accepts_empty_loki_label_response_without_data_key(self):
        from unittest.mock import MagicMock
        def response(url, **kwargs):
            body = ({'status':'success'} if '/labels?' in url else
                    {'status':'success','data':{'result':[]}} if 'query_range?' in url else None)
            result=MagicMock()
            result.__enter__.return_value.read.return_value=(json.dumps(body) if body else 'ready').encode()
            return result
        opener=MagicMock();opener.open.side_effect=response
        payload={'sources':[],'task':'task-a','query_loki':True,'lookback_minutes':60}
        output=io.StringIO()
        with patch('sys.argv',['probe',json.dumps(payload)]),patch('urllib.request.build_opener',return_value=opener),redirect_stdout(output):
            exec(compile(PROBE,'<log-probe>','exec'),{})
        report=json.loads(output.getvalue())
        self.assertEqual(report['loki']['labels'],[])
        self.assertFalse(report['loki']['logs_found'])
        self.assertNotIn('error',report['loki'])

    def test_logs_check_uses_saved_manifest_and_reports_site_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);cfg=self.config(base);(base/'logs').mkdir();(base/'logs/server.log').touch()
            directory,vendor=stack.prepare(cfg);instance=stack.Stack(cfg,directory,vendor);instance.save('ready')
            site=base/'site.json'
            edited=json.loads(json.dumps(cfg));edited['log_sources'][0]['path']='server_log.log'
            site.write_text(json.dumps(edited))
            remote={'files':host_files(cfg),'alloy':{'ready':True},'loki':{'ready':True,'logs_found':True}}
            output=io.StringIO()
            with patch('log_health.container_report',return_value=remote) as probe,redirect_stdout(output):
                code=execute(parser_for_cli().parse_args(['logs','check','--config',str(site)]))
            self.assertEqual(code,0)
            report=json.loads(output.getvalue())
            self.assertEqual(report['config_source'],'saved running manifest')
            self.assertTrue(report['site_log_settings_differ'])
            self.assertEqual(probe.call_args.args[0]['log_sources'][0]['path'],'server.log')
