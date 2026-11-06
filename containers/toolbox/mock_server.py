"""Mac-only test fixture: synthetic SSE inference and synthetic device telemetry."""
import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
REQUESTS = 0
ACTIVE = 0
CAPACITY = int(os.environ.get('MOCK_CAPACITY', '0'))
OVERLOAD_DELAY = float(os.environ.get('MOCK_OVERLOAD_DELAY_SECONDS', '0'))
VENDOR = os.environ.get('MOCK_GPU_VENDOR', 'nvidia')
LOG = Path('/logs/server.log')


class Handler(BaseHTTPRequestHandler):
    def reply(self, body, content_type='application/json'):
        data = body.encode()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/v1/models':
            self.reply(json.dumps({'object': 'list', 'data': [{'id': 'mock-model', 'object': 'model'}]}))
        elif self.path == '/metrics':
            self.reply(f'# TYPE sglang:num_running_reqs gauge\nsglang:num_running_reqs{{model_name="mock-model"}} 0\n# TYPE benchmark_mock_requests_total counter\nbenchmark_mock_requests_total {REQUESTS}\n', 'text/plain')
        elif self.path == '/gpu-metrics':
            labels = 'gpu="0",UUID="MOCK-ONLY",modelName="SIMULATED",Hostname="mock"'
            if VENDOR == 'nvidia':
                values = {'GPU_UTIL': 25, 'FB_USED': 1024, 'FB_FREE': 7168, 'GPU_TEMP': 42,
                          'POWER_USAGE': 80, 'SM_CLOCK': 1200, 'MEM_CLOCK': 1500, 'XID_ERRORS': 0}
                body = ''.join(f'DCGM_FI_DEV_{k}{{{labels}}} {v}\n' for k, v in values.items())
            else:
                values = {'hcu_utilizationrate': 25, 'hcu_usedmemory_bytes': 1073741824,
                          'hcu_memorycap_bytes': 8589934592, 'hcu_vram_percent': 12.5,
                          'hcu_temp_mem': 42, 'hcu_power_usage': 80}
                body = ''.join(f'{k}{{device_id="MOCK-ONLY",minor_number="0"}} {v}\n' for k, v in values.items())
            self.reply(body, 'text/plain')
        else:
            self.send_error(404)

    def do_POST(self):
        global REQUESTS, ACTIVE
        if self.path not in ('/v1/completions', '/v1/chat/completions'):
            self.send_error(404)
            return
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        prompt = data.get('prompt') or data.get('messages', [{}])[-1].get('content', '')
        count = min(int(data.get('max_tokens', 16)), 4096)
        with LOCK:
            REQUESTS += 1
            ACTIVE += 1
            active = ACTIVE
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open('a') as f:
                f.write(json.dumps({'simulation': True, 'request': REQUESTS, 'output_tokens': count,
                                    'active_at_start': active})+'\n')
        chat = self.path.endswith('/chat/completions')
        try:
            # A controlled overload knee exercises the search using real measured HTTP latency.
            # It is independent of the searcher's thresholds and does not fabricate AISBench reports.
            if CAPACITY and active > CAPACITY:
                time.sleep(OVERLOAD_DELAY)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            for index in range(count):
                time.sleep(0.005)
                choice = {'index': 0, 'finish_reason': None}
                choice['delta' if chat else 'text'] = {'content': ' A'} if chat else ' A'
                chunk = {'id': 'mock', 'object': 'chat.completion.chunk' if chat else 'text_completion',
                         'created': int(time.time()), 'model': 'mock-model', 'choices': [choice]}
                self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode()); self.wfile.flush()
            chunk = {'choices': [], 'usage': {'prompt_tokens': len(prompt.split()),
                     'completion_tokens': count, 'total_tokens': len(prompt.split())+count}}
            self.wfile.write(('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with LOCK:
                ACTIVE -= 1


ThreadingHTTPServer.request_queue_size = 128
ThreadingHTTPServer(('0.0.0.0', 18000), Handler).serve_forever()
