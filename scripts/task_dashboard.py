"""Build an offline Grafana task dashboard with exactly one hardware vendor section."""
import argparse
from pathlib import Path
from config_io import write_yaml
from gpu_metrics import DEVICE_METRICS, GPU_JOBS

PROM = {'type': 'prometheus', 'uid': '$datasource'}
LOKI = {'type': 'loki', 'uid': '$loki_datasource'}
TASK = 'task=~`${task:regex}`'
GPU = TASK + ',gpu_node=~`${gpu_node:regex}`,gpu_model=~`${gpu_model:regex}`,gpu_index=~`${gpu_index:regex}`'
HOST = TASK + ',job="node-exporter",instance=~`${host:regex}`'
# Retain separate mount/device series: summing stacked devices or interfaces double counts I/O.
FILESYSTEM = ',fstype!~"tmpfs|devtmpfs|overlay|squashfs|nsfs",mountpoint!~"/(dev|proc|sys|run)($|/.*)"'
DISK = ',device!~"loop[0-9]+|ram[0-9]+|fd[0-9]+"'
INTERFACE = ',device=~`${net_device:regex}`'
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
    chart('GPU Exporter 健康', [('{{gpu_node}}', 'up{' + TASK + ',gpu_vendor="' + vendor + '",job="' + GPU_JOBS[vendor] + '"}')], kind='stat')

    row('主机资源 · node-exporter（按任务 / 主机采集目标筛选）')
    def host(name, filters=''):
        return name + '{' + HOST + filters + '}'
    def host_rate(name, filters=''):
        return 'rate(' + host(name, filters) + '[$__rate_interval])'
    host_legend = '{{instance}}'
    scope = ('当前挂载宿主机 /proc、/sys 和根目录，展示节点整体资源。' if cfg.get('host_metrics') == 'host'
             else '当前为容器采集视角；真实 Linux 节点应配置 host_metrics: host 和 node_exporter_target，才能完整观察宿主机文件系统及网络。')
    chart('主机 Exporter 健康', [(host_legend, host('up'))], kind='stat',
          description='1 为采集成功，0 为失败；主机下拉保留采集失败的目标。' + scope + '这些指标不代表任务独占资源。')
    chart('主机 CPU 使用率', [(host_legend, '100 * (1 - avg by (instance) (' + host_rate('node_cpu_seconds_total', ',mode="idle"') + '))')],
          'percent', '各逻辑 CPU 平均非 idle 时间，包含 iowait；不是推理进程或客户端独占使用率。')
    chart('主机 CPU I/O 等待', [(host_legend, '100 * avg by (instance) (' + host_rate('node_cpu_seconds_total', ',mode="iowait"') + ')')],
          'percent', 'CPU 等待 I/O 的时间比例，结合磁盘忙碌率及设备吞吐定位存储瓶颈。')
    chart('主机系统负载', [(host_legend + ' / ' + window, host('node_load' + window)) for window in ('1', '5', '15')],
          description='1/5/15 分钟原始 load average，含可运行及不可中断等待任务；不是 CPU 百分比，需结合节点 CPU 核数判断。')
    memory_total, memory_available = host('node_memory_MemTotal_bytes'), host('node_memory_MemAvailable_bytes')
    chart('主机内存使用率', [(host_legend, '100 * (1 - ' + memory_available + ' / (' + memory_total + ' > 0))')],
          'percent', '使用 MemAvailable 而非 MemFree，避免把可回收缓存全部视作已用内存。')
    chart('主机内存容量', [(host_legend + ' / 已用', memory_total + ' - ' + memory_available),
                            (host_legend + ' / 可用', memory_available), (host_legend + ' / 总量', memory_total)], 'bytes')
    chart('主机 Swap 容量', [(host_legend + ' / 已用', host('node_memory_SwapTotal_bytes') + ' - ' + host('node_memory_SwapFree_bytes')),
                            (host_legend + ' / 总量', host('node_memory_SwapTotal_bytes'))], 'bytes',
          '未配置 Swap 时为 0；不计算除以零的使用率。')
    fs_available = host('node_filesystem_avail_bytes', FILESYSTEM)
    fs_size = host('node_filesystem_size_bytes', FILESYSTEM)
    fs_legend = host_legend + ' / {{mountpoint}} / {{device}}'
    chart('主机文件系统使用率', [(fs_legend, '100 * (1 - ' + fs_available + ' / (' + fs_size + ' > 0))')],
          'percent', '按挂载点展示，使用普通用户可用空间计算；排除常见虚拟文件系统，0 容量不参与计算。' + scope)
    chart('主机文件系统可用空间', [(fs_legend, fs_available)], 'bytes',
          '按挂载点保留独立序列，不将同一设备的多个挂载点相加。')
    disk_legend = host_legend + ' / {{device}}'
    chart('主机磁盘读写吞吐', [(disk_legend + ' / 读', host_rate('node_disk_read_bytes_total', DISK)),
                              (disk_legend + ' / 写', host_rate('node_disk_written_bytes_total', DISK))], 'Bps',
          '按块设备展示字节速率，排除 loop/ram/fd；dm/md 与底层盘可能重复，不跨层求和。')
    chart('主机磁盘 IOPS', [(disk_legend + ' / 读', host_rate('node_disk_reads_completed_total', DISK)),
                           (disk_legend + ' / 写', host_rate('node_disk_writes_completed_total', DISK))], 'iops')
    chart('主机磁盘忙碌率', [(disk_legend, '100 * ' + host_rate('node_disk_io_time_seconds_total', DISK))], 'percent',
          '设备有 I/O 在执行的时间比例；不代表 NVMe/RAID 的吞吐能力已达上限。')
    net_legend = host_legend + ' / {{device}}'
    chart('主机网络收发吞吐', [(net_legend + ' / 收', host_rate('node_network_receive_bytes_total', INTERFACE)),
                              (net_legend + ' / 发', host_rate('node_network_transmit_bytes_total', INTERFACE))], 'Bps',
          '按“主机网卡”筛选，保留 lo/网桥/veth 以观察同机容器流量；bond 与成员接口、虚拟与物理接口不求和。' + scope)
    chart('主机网络错误速率', [(net_legend + ' / 收', host_rate('node_network_receive_errs_total', INTERFACE)),
                              (net_legend + ' / 发', host_rate('node_network_transmit_errs_total', INTERFACE))], 'ops')
    chart('主机网络丢包速率', [(net_legend + ' / 收', host_rate('node_network_receive_drop_total', INTERFACE)),
                              (net_legend + ' / 发', host_rate('node_network_transmit_drop_total', INTERFACE))], 'pps')

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

    row(('NVIDIA GPU' if vendor == 'nvidia' else '海光 DCU v2.4.1') + ' · 任务 → 节点 → 型号 → 卡序号')
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
        # Physical-card charts collapse copies carrying Kubernetes/pod metadata.
        # Never sum duplicate copies of the same card, and never mix vdcu_* into dcu_*.
        def card(name):
            return 'max by (task,gpu_node,gpu_model,gpu_index) (' + gpu(name) + ')'
        chart('海光 DCU 利用率', [(legend, card('dcu_utilizationrate'))], 'percent')
        chart('海光已用显存', [(legend, card('dcu_usedmemory_bytes'))], 'bytes')
        chart('海光显存容量', [(legend, card('dcu_memorycap_bytes'))], 'bytes')
        chart('海光显存使用率', [(legend, '100 * '+card('dcu_usedmemory_bytes')+' / ('+card('dcu_memorycap_bytes')+' > 0)')], 'percent')
        chart('海光 DCU 温度', [(legend, card('dcu_temp'))], 'celsius')
        chart('海光 DCU 功率', [(legend, card('dcu_power_usage'))], 'watt')
        chart('海光 PCIe 带宽', [(legend, card('dcu_pciebw_mb'))], 'MiBs')
        chart('海光核心时钟', [(legend, card('dcu_sclk'))], 'suffix:MHz')
        chart('海光错误计数（原值）', [('CE '+legend, card('dcu_ce_count')), ('UE '+legend, card('dcu_ue_count'))], description='按官方 DCU 面板显示原始计数，不把未知计数类型当作速率。')
        chart('可见 DCU 卡数', [('卡数', 'count('+card('dcu_memorycap_bytes')+')')], kind='stat')

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
    device_metric = DEVICE_METRICS[vendor]
    variables += [
        prom_variable('host','主机采集目标', 'up{'+TASK+',job="node-exporter"}', 'instance'),
        prom_variable('net_device','主机网卡', 'node_network_receive_bytes_total{'+HOST+'}', 'device'),
        prom_variable('gpu_node','GPU 节点', device_metric+'{'+TASK+'}', 'gpu_node'),
        prom_variable('gpu_model','GPU 型号', device_metric+'{'+TASK+',gpu_node=~`${gpu_node:regex}`}', 'gpu_model'),
        prom_variable('gpu_index','GPU 卡序号', device_metric+'{'+TASK+',gpu_node=~`${gpu_node:regex}`,gpu_model=~`${gpu_model:regex}`}', 'gpu_index'),
        loki_variable('instance_role','日志实例角色','{'+LOG_TASK+'}','instance_role'),
        loki_variable('replica','日志副本','{'+LOG_TASK+',instance_role=~"$instance_role"}','replica'),
        loki_variable('source_file','日志文件','{'+LOG_TASK+',instance_role=~"$instance_role",replica=~"$replica"}','source_file'),
        {'name':'log_search','label':'日志包含','type':'textbox','query':'','current':{'text':'','value':''}},
    ]
    return {'id':None,'uid':'inference-task-overview','title':'推理任务总览 · '+('NVIDIA' if vendor=='nvidia' else '海光'),
            'description':'按任务查看 SGLang/vLLM；node-exporter 按任务/主机采集目标展示整体资源；GPU 类型由启动器选择，按节点/型号/卡序号联动；日志按任务/角色/副本筛选。未暴露的指标显示无数据，不伪造为 0。'+(' 当前为模拟数据。' if cfg.get('simulation') else ''),
            'tags':['inference','task',vendor],'schemaVersion':39,'version':2,'editable':False,'timezone':'browser',
            'refresh':'5s','time':{'from':'now-30m','to':'now'},'panels':panels,'templating':{'list':variables},'annotations':{'list':[]},'links':[]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for vendor in ('nvidia','hygon'):
        write_yaml(args.output_dir/('task-overview-'+vendor+'.yaml'), build_dashboard(vendor))


if __name__ == '__main__':
    main()
