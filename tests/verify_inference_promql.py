#!/usr/bin/env python3
"""Opt-in Prometheus semantic checks. No network/pulls or running service required.

python3 tests/verify_inference_promql.py [--image prom/prometheus:v3.5.0]
Use --promtool /path/to/promtool to run without Docker.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from config_io import write_yaml
from task_dashboard import build_dashboard


def resolved(expr, instance='a:8000|b:8000'):
    values = {'${task:regex}': 'task-a', '${inference_instance:regex}': instance,
              '${host:regex}': '.*', '${net_device:regex}': '.*', '${gpu_node:regex}': '.*',
              '${gpu_model:regex}': '.*', '${gpu_index:regex}': '.*', '$__rate_interval': '5m'}
    for key, value in values.items():
        expr = expr.replace(key, value)
    return expr


def fixture():
    series = []
    base = {'task': 'task-a', 'job': 'inference', 'instance': 'a:8000', 'model_name': 'm', 'engine': '0'}

    def labels(extra=None):
        return {**base, **(extra or {})}

    def rendered(metric, extra=None):
        return metric + '{' + ','.join(k+'="'+v+'"' for k, v in sorted(labels(extra).items())) + '}'

    def add(metric, values, extra=None):
        series.append({'series': rendered(metric, extra), 'values': values})

    def counter(metric, increment, extra=None):
        add(metric, f'0+{increment}x5', extra)

    def histogram(metric, count, total, buckets, extra=None):
        counter(metric+'_count', count, extra)
        counter(metric+'_sum', total, extra)
        for bound, value in buckets.items():
            counter(metric+'_bucket', value, {**(extra or {}), 'le': bound})

    # Uneven traffic makes accidental averaging of means/ratios/quantiles detectable.
    for instance, count, total, buckets in (
        ('a:8000', 2, .4, {'1': 2, '10': 2, '+Inf': 2}),
        ('b:8000', 8, 40, {'1': 0, '10': 8, '+Inf': 8}),
        ('idle:8000', 0, 0, {'1': 0, '10': 0, '+Inf': 0}),
    ):
        histogram('vllm:time_to_first_token_seconds', count, total, buckets, {'instance': instance})
    histogram('vllm:request_time_per_output_token_seconds', 2, .4, {'1': 2, '+Inf': 2})
    histogram('vllm:request_time_per_output_token_seconds', 8, 8, {'1': 8, '+Inf': 8}, {'instance': 'b:8000'})
    histogram('vllm:inter_token_latency_seconds', 10, .1, {'1': 10, '+Inf': 10})
    # A legacy inter-output metric must never be mixed into request-level TPOT.
    histogram('vllm:time_per_output_token_seconds', 10, 100, {'10': 10, '+Inf': 10})
    for instance, prompt, hits, queries in (('a:8000', 60, 60, 60), ('b:8000', 540, 0, 540)):
        extra = {'instance': instance}
        counter('vllm:prompt_tokens_total', prompt, extra)
        counter('vllm:prefix_cache_hits_total', hits, extra)
        counter('vllm:prefix_cache_queries_total', queries, extra)
    # Scrape scopes must reject both another task and a copy from another job.
    counter('vllm:prompt_tokens_total', 6000, {'task': 'other'})
    counter('vllm:prompt_tokens_total', 6000, {'job': 'other'})
    for reason, count in (('stop', 60), ('length', 30), ('abort', 6)):
        counter('vllm:request_success_total', count, {'finished_reason': reason})
    add('vllm:kv_cache_usage_perc', '.8+0x5')
    add('vllm:gpu_cache_usage_perc', '.9+0x5')
    add('vllm:gpu_cache_usage_perc', '.6+0x5', {'instance': 'b:8000'})
    add('sglang:num_retracted_reqs', '5 7 3 8 2 4')
    counter('sglang:num_requests_total', 60)
    counter('sglang:num_requests_total', 120, {'instance': 'b:8000'})
    counter('sglang:num_aborted_requests_total', 6)
    counter('sglang:prompt_tokens_total', 60)  # Cache-hit label series is created lazily upstream.
    histogram('sglang:per_stage_req_latency_seconds', 2, .4, {'1': 2, '10': 2, '+Inf': 2}, {'stage': 'queue'})
    histogram('sglang:per_stage_req_latency_seconds', 2, 10, {'1': 0, '10': 2, '+Inf': 2}, {'stage': 'decode'})
    for instance, status, count in (('a:8000', '200', 90), ('a:8000', '500', 30),
                                    ('healthy:8000', '2xx', 120), ('idle:8000', '2xx', 0)):
        counter('http_requests_total', count, {'instance': instance, 'method': 'POST',
                                               'handler': '/v1/chat/completions', 'status': status})
    counter('http_requests_total', 6000, {'method': 'GET', 'handler': '/v1/chat/completions', 'status': '500'})
    counter('http_requests_total', 6000, {'method': 'POST', 'handler': '/metrics', 'status': '500'})

    def sample(value, extra=None, metric=None, keep_base=False):
        fields = labels(extra) if keep_base else (extra or {})
        if metric:
            fields = {'__name__': metric, **fields}
        return {'labels': '{'+','.join(k+'="'+v+'"' for k, v in sorted(fields.items()))+'}', 'value': value}

    panels = {p['title']: p for p in build_dashboard('nvidia')['panels']}
    assertions = []

    def check(title, index, expected, instance='a:8000|b:8000'):
        assertions.append({'expr': resolved(panels[title]['targets'][index]['expr'], instance),
                           'eval_time': '5m', 'exp_samples': expected})

    check('vLLM Token 吞吐', 0, [sample(10)])
    check('vLLM 前缀缓存命中率', 0, [sample(.1)])
    check('vLLM TTFT', 1, [sample(9.4375)])
    check('vLLM TTFT', 2, [sample(9.8875)])
    check('vLLM TTFT', 3, [sample(4.04)])
    check('vLLM 实例 TTFT P99', 0, [sample(.99, {'instance': 'a:8000'}), sample(9.91, {'instance': 'b:8000'})])
    check('vLLM 请求级 TPOT', 3, [sample(.84)])
    check('vLLM 输出间隔 ITL', 3, [sample(.01)])
    check('vLLM 请求结束速率', 0, [sample(value, {'finished_reason': reason})
                                            for reason, value in (('stop', 1), ('length', .5), ('abort', .1))])
    check('vLLM KV Cache 使用率', 0, [sample(.8, metric='vllm:kv_cache_usage_perc', keep_base=True),
                                       sample(.6, {'instance': 'b:8000'}, metric='vllm:gpu_cache_usage_perc', keep_base=True)])
    check('SGLang 周期重调度请求', 0, [sample(4, metric='sglang:num_retracted_reqs', keep_base=True)])
    check('SGLang 请求完成速率', 0, [sample(3)])
    check('SGLang 请求中止速率', 0, [sample(.1)])
    check('SGLang 分阶段耗时 P95', 0, [sample(.95, {'instance': 'a:8000', 'stage': 'queue'}),
                                          sample(9.55, {'instance': 'a:8000', 'stage': 'decode'})])
    check('HTTP 4xx/5xx 比例', 0, [sample(.25)], instance='a:8000')
    check('HTTP 4xx/5xx 比例', 0, [sample(0)], instance='healthy:8000')
    for instance in ('idle:8000', 'absent:8000'):
        check('vLLM TTFT', 2, [], instance)
        check('vLLM TTFT', 3, [], instance)
        check('HTTP 4xx/5xx 比例', 0, [], instance)
    check('SGLang 输入 Token 分布', 2, [])  # Optional histogram remains absent, never fabricated.
    check('SGLang 完成请求缓存命中率', 0, [])  # No counter series does not prove a measured zero.
    return series, assertions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--promtool', help='local promtool binary; otherwise use Docker with --pull=never')
    parser.add_argument('--image', default='prom/prometheus:v3.5.0')
    args = parser.parse_args()
    # Parse every distinct PromQL expression, including optional PD and profiling sections.
    expressions = set()
    for vendor in ('nvidia', 'hygon'):
        dashboard = build_dashboard(vendor, {'topology': {'kind': 'pd'}, 'gpu': {'nvidia_metrics_profile': 'full'}})
        for panel in dashboard['panels']:
            if panel.get('datasource', {}).get('type') == 'loki':
                continue
            for target in panel.get('targets', []):
                expressions.add(resolved(target['expr']))
    series, assertions = fixture()
    with tempfile.TemporaryDirectory(prefix='llm-bench-promql-') as tmp:
        root = Path(tmp)
        write_yaml(root/'queries.rules.yaml', {'groups': [{'name': 'dashboard', 'rules': [
            {'record': f'dashboard_query_{index}', 'expr': expr} for index, expr in enumerate(sorted(expressions))]}]})
        write_yaml(root/'queries.test.yaml', {'rule_files': [], 'evaluation_interval': '1m', 'fuzzy_compare': True,
                                             'tests': [{'interval': '1m', 'input_series': series, 'promql_expr_test': assertions}]})
        prefix = ([args.promtool] if args.promtool else ['docker', 'run', '--rm', '--pull=never', '--user', '0',
                  '--network', 'none', '--entrypoint', '/bin/promtool', '--mount', f'type=bind,src={root},dst=/checks,readonly', args.image])
        for command, filename in (('check', 'queries.rules.yaml'), ('test', 'queries.test.yaml')):
            path = str(root/filename) if args.promtool else '/checks/'+filename
            subprocess.run([*prefix, command, 'rules', path], check=True)
    print(f'PASS: {len(expressions)} distinct PromQL queries parsed; {len(assertions)} semantic assertions passed.')


if __name__ == '__main__':
    main()
