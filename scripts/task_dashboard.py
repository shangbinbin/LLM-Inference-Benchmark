"""Build an offline Grafana task dashboard with exactly one hardware vendor section."""
import argparse
import json
from pathlib import Path

PROM = {'type': 'prometheus', 'uid': '$datasource'}
LOKI = {'type': 'loki', 'uid': '$loki_datasource'}
TASK = 'task=~`${task:regex}`'
GPU = TASK + ',gpu_node=~`${gpu_node:regex}`,gpu_model=~`${gpu_model:regex}`,gpu_index=~`${gpu_index:regex}`'
# Loki's default formatter escapes multi-select values for a quoted LogQL regex.
LOG_TASK = 'task=${task:doublequote}'
LOG = '{' + LOG_TASK + ',instance_role=~"$instance_role",replica=~"$replica",source_file=~"$source_file"} |= ${log_search:doublequote}'


def prom_variable(name, title, selector, label, current=None, multi=True):
    query = f'label_values({selector}, {label})'
    return {'name': name, 'label': title, 'type': 'query', 'datasource': PROM,
            'definition': query, 'query': {'query': query, 'refId': 'StandardVariableQuery'},
            'refresh': 1, 'sort': 1, 'multi': multi, 'includeAll': multi,
            'allValue': '.*' if multi else None,
            'current': current or ({'text': 'All', 'value': '$__all'} if multi else {})}


def loki_variable(name, title, stream, label):
    return {'name': name, 'label': title, 'type': 'query', 'datasource': LOKI,
            'definition': f'label_values({stream}, {label})',
            'query': {'type': 1, 'label': label, 'stream': stream,
                      'refId': 'LokiVariableQueryEditor-VariableQuery'},
            'refresh': 1, 'sort': 1, 'multi': True, 'includeAll': True, 'allValue': '.*',
            'current': {'text': 'All', 'value': '$__all'}}


def build_dashboard(vendor, cfg=None):
    if vendor not in ('nvidia', 'hygon'):
        raise ValueError('Dashboard vendor must be nvidia/hygon')
    cfg = cfg or {}
    panels, y = [], 0
    def row(title):
        nonlocal y
        panels.append({'id': len(panels)+1, 'type': 'row', 'title': title, 'collapsed': False,
                       'gridPos': {'x': 0, 'y': y, 'w': 24, 'h': 1}, 'panels': []})
        y += 1
    def chart(title, queries, unit='short', description='', kind='timeseries', width=8, height=7):
        nonlocal y
        # Place up to three charts in each line; rows/full-width panels reset the layout.
        previous = panels[-1] if panels else None
        x = previous['gridPos']['x'] + previous['gridPos']['w'] if previous and previous['type'] != 'row' and previous['gridPos']['w'] < 24 else 0
        py = previous['gridPos']['y'] if x and x + width <= 24 else y
        if not x or x + width > 24:
            x = 0
            py = y
            y += height
        targets = [{'refId': chr(65+i), 'expr': expr, 'legendFormat': legend, 'editorMode': 'code',
                    'range': True, 'datasource': LOKI if kind == 'logs' else PROM}
                   for i, (legend, expr) in enumerate(queries)]
        panel = {'id': len(panels)+1, 'title': title, 'description': description, 'type': kind,
                 'datasource': LOKI if kind == 'logs' else PROM, 'targets': targets,
                 'gridPos': {'x': x, 'y': py, 'w': width, 'h': height},
                 'fieldConfig': {'defaults': {'unit': unit, 'min': 0}, 'overrides': []},
                 'options': {'legend': {'displayMode': 'list', 'placement': 'bottom'},
                             'tooltip': {'mode': 'multi', 'sort': 'desc'}}}
        if kind == 'stat':
            panel['options'] = {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
                                'colorMode': 'value', 'graphMode': 'area'}
        if kind == 'logs':
            panel['options'] = {'showTime': True, 'showLabels': False, 'showCommonLabels': True,
                                'wrapLogMessage': True, 'prettifyLogMessage': False,
                                'enableLogDetails': True, 'dedupStrategy': 'none', 'sortOrder': 'Descending'}
            panel['targets'][0].update(datasource=LOKI, queryType='range', maxLines=1000)
            panel['links'] = [{'title': 'Explore 中可用 Live 持续跟踪', 'url': '/explore'}]
        panels.append(panel)
        return panel
    def metric(name):
        return name + '{' + TASK + '}'
    def rate(name):
        return 'sum(rate(' + metric(name) + '[$__rate_interval]))'
    def quantiles(name):
        return [(f'P{int(q*100)}', 'histogram_quantile(' + str(q) + ', sum by (le) (rate(' + metric(name+'_bucket') + '[$__rate_interval])))') for q in (.5, .95, .99)]

    row('推理任务采集状态')
    chart('推理实例健康', [('{{replica}} / {{instance_role}}', 'up{' + TASK + ',job="inference"}')], description='1 为采集正常，0 为失败；任务内各实例独立显示。', kind='stat')
    chart('运行实例数', [('UP', 'sum(up{' + TASK + ',job="inference"})')], kind='stat')
    chart('GPU Exporter 健康', [('{{gpu_node}}', 'up{' + TASK + ',gpu_vendor="' + vendor + '",job="' + ('nvidia-dcgm-exporter' if vendor == 'nvidia' else 'hygon-hcu-exporter') + '"}')], kind='stat')

    row('vLLM 核心性能（只按任务筛选）')
    chart('vLLM 成功请求完成速率', [('请求/s', rate('vllm:request_success_total'))], 'reqps', '不包含被网关提前拒绝的请求；不是压测发送 QPS。')
    chart('vLLM Token 吞吐', [('输入 Token/s', rate('vllm:prompt_tokens_total')), ('输出 Token/s', rate('vllm:generation_tokens_total'))])
    chart('vLLM 运行/排队请求', [('运行', 'sum('+metric('vllm:num_requests_running')+')'), ('排队', 'sum('+metric('vllm:num_requests_waiting')+')')])
    chart('vLLM TTFT', quantiles('vllm:time_to_first_token_seconds'), 's')
    chart('vLLM 端到端延迟', quantiles('vllm:e2e_request_latency_seconds'), 's')
    chart('vLLM KV Cache 使用率', [('最大实例', 'max('+metric('vllm:kv_cache_usage_perc')+' or '+metric('vllm:gpu_cache_usage_perc')+')')], 'percentunit')
    chart('vLLM 每 Token 延迟（版本相关）', [('ITL P95', quantiles('vllm:inter_token_latency_seconds')[1][1]), ('TPOT/旧版间隔 P95', quantiles('vllm:time_per_output_token_seconds')[1][1])], 's', '分别查询两种名称，不相加；精确语义以实际 vLLM 版本为准。')
    chart('vLLM 排队时间', quantiles('vllm:request_queue_time_seconds'), 's')
    chart('vLLM 抢占速率', [('抢占/s', rate('vllm:num_preemptions_total'))], 'ops')

    row('SGLang 核心性能（只按任务筛选）')
    chart('SGLang 运行/排队请求', [('运行', 'sum('+metric('sglang:num_running_reqs')+')'), ('排队', 'sum('+metric('sglang:num_queue_reqs')+')')])
    chart('SGLang 输出 Token 吞吐', [('Token/s', 'sum('+metric('sglang:gen_throughput')+')')])
    chart('SGLang 缓存命中率（实例）', [('{{instance}}', metric('sglang:cache_hit_rate'))], description='保留原始值，按实际部署版本核对比率单位；不跨实例简单平均。')
    chart('SGLang TTFT', quantiles('sglang:time_to_first_token_seconds'), 's')
    chart('SGLang 端到端延迟', quantiles('sglang:e2e_request_latency_seconds'), 's')
    chart('SGLang 每 Token 延迟（版本相关）', quantiles('sglang:inter_token_latency_seconds'), 's', '旧版本未暴露此指标时无数据；不会伪造为 0。')

    row(('NVIDIA GPU' if vendor == 'nvidia' else '海光 HCU') + ' · 任务 → 节点 → 型号 → 卡序号')
    legend = '{{gpu_node}} / {{gpu_model}} / 卡{{gpu_index}}'
    def gpu(name):
        return name + '{' + GPU + '}'
    if vendor == 'nvidia':
        chart('GPU 利用率', [(legend, gpu('DCGM_FI_DEV_GPU_UTIL'))], 'percent')
        chart('GPU 显存占用', [(legend, '100 * '+gpu('DCGM_FI_DEV_FB_USED')+' / ('+gpu('DCGM_FI_DEV_FB_USED')+' + '+gpu('DCGM_FI_DEV_FB_FREE')+')')], 'percent')
        chart('GPU 已用显存', [(legend, gpu('DCGM_FI_DEV_FB_USED')+' * 1048576')], 'bytes')
        chart('GPU 功率', [(legend, gpu('DCGM_FI_DEV_POWER_USAGE'))], 'watt')
        chart('GPU 温度', [(legend, gpu('DCGM_FI_DEV_GPU_TEMP'))], 'celsius')
        chart('GPU 时钟', [('SM '+legend, gpu('DCGM_FI_DEV_SM_CLOCK')), ('显存 '+legend, gpu('DCGM_FI_DEV_MEM_CLOCK'))], 'suffix:MHz')
        chart('最近 XID 错误代码', [(legend, gpu('DCGM_FI_DEV_XID_ERRORS'))], description='这是错误代码，不是错误计数；0 表示当前没有上报 XID。')
        if cfg.get('gpu', {}).get('nvidia_metrics_profile', 'basic') == 'full':
            chart('GPU profiling 活跃率（需支持）', [('SM '+legend, gpu('DCGM_FI_PROF_GR_ENGINE_ACTIVE')), ('Tensor '+legend, gpu('DCGM_FI_PROF_PIPE_TENSOR_ACTIVE'))], 'percentunit')
            chart('GPU profiling DRAM 活跃率（需支持）', [(legend, gpu('DCGM_FI_PROF_DRAM_ACTIVE'))], 'percentunit')
        else:
            chart('GPU 空闲显存', [(legend, gpu('DCGM_FI_DEV_FB_FREE')+' * 1048576')], 'bytes')
            chart('可见 GPU 卡数', [('卡数', 'count('+gpu('DCGM_FI_DEV_GPU_UTIL')+')')], kind='stat')
    else:
        chart('海光计算利用率（原始值）', [(legend, gpu('hcu_utilizationrate'))], description='官方 HCU v3 文档未明确量纲；不预设百分比或缩放，需现场核对。')
        chart('海光已用显存', [(legend, gpu('hcu_usedmemory_bytes'))], 'bytes')
        chart('海光显存容量', [(legend, gpu('hcu_memorycap_bytes'))], 'bytes')
        chart('海光显存使用率', [(legend, gpu('hcu_vram_percent'))], 'percent')
        chart('海光显存温度', [(legend, gpu('hcu_temp_mem'))], 'celsius')
        chart('海光当前功耗（原始值）', [(legend, gpu('hcu_power_usage'))], description='文档未明确功率单位；不标为 W、不预设缩放。尚需真实硬件验证。')

    row('推理实例日志 · 任务 → 角色 → 副本')
    volume = chart('Logs Volume', [('{{log_level}}', 'sum by (log_level) (count_over_time('+LOG+' | label_format log_level=`{{if .detected_level}}{{.detected_level}}{{else}}unknown{{end}}` [$__interval]))')], width=24, height=6, description='每个时间窗的日志条数；无级别信息归 unknown，图例求和不代替精确全时段日志计数。')
    volume['datasource'] = LOKI
    volume['targets'][0].update(datasource=LOKI, queryType='range')
    volume['interval'] = '5s'
    volume['fieldConfig']['defaults']['custom'] = {'drawStyle': 'bars', 'fillOpacity': 100, 'lineWidth': 0, 'stacking': {'mode': 'normal', 'group': 'logs'}}
    for level, color in [('error','red'), ('warning','yellow'), ('warn','yellow'), ('info','green'), ('debug','blue'), ('unknown','gray')]:
        volume['fieldConfig']['overrides'].append({'matcher': {'id':'byName','options':level}, 'properties':[{'id':'color','value':{'mode':'fixed','fixedColor':color}}]})
    chart('推理日志（每 5 秒自动刷新）', [('日志', LOG)], kind='logs', width=24, height=12,
          description='Dashboard 是轮询刷新；需要真正的 Live 流式跟踪时，在 Explore 选 Benchmark Loki 使用同一查询并点击 Live。')

    task = cfg.get('task', cfg.get('project'))
    variables = [
        {'name':'datasource','label':'指标数据源','type':'datasource','query':'prometheus','current':{'text':'Benchmark Prometheus','value':'benchmark-prometheus'}},
        {'name':'loki_datasource','label':'日志数据源','type':'datasource','query':'loki','current':{'text':'Benchmark Loki','value':'benchmark-loki'}},
        prom_variable('task','推理任务','up{task!=""}','task',{'text':task,'value':task} if task else None,False),
    ]
    device_metric = 'DCGM_FI_DEV_GPU_UTIL' if vendor == 'nvidia' else 'hcu_memorycap_bytes'
    variables += [
        prom_variable('gpu_node','GPU 节点', device_metric+'{'+TASK+'}', 'gpu_node'),
        prom_variable('gpu_model','GPU 型号', device_metric+'{'+TASK+',gpu_node=~`${gpu_node:regex}`}', 'gpu_model'),
        prom_variable('gpu_index','GPU 卡序号', device_metric+'{'+TASK+',gpu_node=~`${gpu_node:regex}`,gpu_model=~`${gpu_model:regex}`}', 'gpu_index'),
        loki_variable('instance_role','日志实例角色','{'+LOG_TASK+'}','instance_role'),
        loki_variable('replica','日志副本','{'+LOG_TASK+',instance_role=~"$instance_role"}','replica'),
        loki_variable('source_file','日志文件','{'+LOG_TASK+',instance_role=~"$instance_role",replica=~"$replica"}','source_file'),
        {'name':'log_search','label':'日志包含','type':'textbox','query':'','current':{'text':'','value':''}},
    ]
    return {'id':None,'uid':'inference-task-overview','title':'推理任务总览 · '+('NVIDIA' if vendor=='nvidia' else '海光'),
            'description':'按任务查看 SGLang/vLLM；GPU 类型由启动器选择，按节点/型号/卡序号联动；日志按任务/角色/副本筛选。未暴露的指标显示无数据，不伪造为 0。'+(' 当前为模拟数据。' if cfg.get('simulation') else ''),
            'tags':['inference','task',vendor],'schemaVersion':39,'version':1,'editable':False,'timezone':'browser',
            'refresh':'5s','time':{'from':'now-30m','to':'now'},'panels':panels,'templating':{'list':variables},'annotations':{'list':[]},'links':[]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for vendor in ('nvidia','hygon'):
        (args.output_dir/('task-overview-'+vendor+'.json')).write_text(json.dumps(build_dashboard(vendor),ensure_ascii=False,indent=2)+'\n')


if __name__ == '__main__':
    main()
