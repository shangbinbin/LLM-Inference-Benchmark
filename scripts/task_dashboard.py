"""Build an offline Grafana task dashboard with exactly one hardware vendor section."""
import argparse
from pathlib import Path
from config_io import write_yaml
from gpu_metrics import DEVICE_METRICS, GPU_JOBS

PROM = {'type': 'prometheus', 'uid': '$datasource'}
LOKI = {'type': 'loki', 'uid': '$loki_datasource'}
TASK = 'task=~`${task:regex}`'
INFERENCE = TASK + ',job="inference",instance=~`${inference_instance:regex}`'
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
    def metric(name, filters=''):
        return name + '{' + INFERENCE + filters + '}'
    def aggregate(expression, by=()):
        return ('sum by (' + ','.join(by) + ')' if by else 'sum') + '(' + expression + ')'
    def rate(name, by=(), filters=''):
        return aggregate('rate(' + metric(name, filters) + '[$__rate_interval])', by)
    def quantiles(name, by=(), mean=True):
        # Pool bucket counts, never average replica quantiles. Hide idle/absent distributions.
        buckets = rate(name + '_bucket', (*by, 'le'))
        count = rate(name + '_count', by)
        prefix = ''.join('{{' + label + '}} / ' for label in by)
        queries = [(prefix + f'P{int(q*100)}', 'histogram_quantile(' + str(q) + ', ' + buckets + ') and (' + count + ' > 0)')
                   for q in (.5, .95, .99)]
        if mean:
            queries.append((prefix + '平均', rate(name + '_sum', by) + ' / (' + count + ' > 0)'))
        return queries
    instance_legend = '{{instance}}'
    engine_legend = '{{instance}} / {{model_name}} / 引擎{{engine}}'

    row('推理任务采集状态')
    chart('推理实例健康', [('{{replica}} / {{instance_role}}', metric('up'))], description='所选实例的指标抓取状态：1 正常、0 失败；不代表生成请求一定成功。', kind='stat')
    chart('采集正常的推理实例数', [('UP', 'sum('+metric('up')+')')], kind='stat', description='所选推理指标端点中抓取正常的数量，不等于进程或 GPU 数。')
    chart('GPU Exporter 健康', [('{{gpu_node}}', 'up{' + TASK + ',gpu_vendor="' + vendor + '",job="' + GPU_JOBS[vendor] + '"}')], kind='stat', description='GPU/DCU 指标抓取状态：1 正常、0 失败。')

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
                            (host_legend + ' / 可用', memory_available), (host_legend + ' / 总量', memory_total)], 'bytes', '主机总内存、可用内存及两者之差；已用量排除可回收缓存。')
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
                           (disk_legend + ' / 写', host_rate('node_disk_writes_completed_total', DISK))], 'iops', '各块设备每秒完成的读写操作数；与吞吐、忙碌率一起判断 I/O 压力。')
    chart('主机磁盘忙碌率', [(disk_legend, '100 * ' + host_rate('node_disk_io_time_seconds_total', DISK))], 'percent',
          '设备有 I/O 在执行的时间比例；不代表 NVMe/RAID 的吞吐能力已达上限。')
    net_legend = host_legend + ' / {{device}}'
    chart('主机网络收发吞吐', [(net_legend + ' / 收', host_rate('node_network_receive_bytes_total', INTERFACE)),
                              (net_legend + ' / 发', host_rate('node_network_transmit_bytes_total', INTERFACE))], 'Bps',
          '按“主机网卡”筛选，保留 lo/网桥/veth 以观察同机容器流量；bond 与成员接口、虚拟与物理接口不求和。' + scope)
    chart('主机网络错误速率', [(net_legend + ' / 收', host_rate('node_network_receive_errs_total', INTERFACE)),
                              (net_legend + ' / 发', host_rate('node_network_transmit_errs_total', INTERFACE))], 'ops', '各网卡每秒接收/发送错误数；升高时核查网卡、驱动和链路。')
    chart('主机网络丢包速率', [(net_legend + ' / 收', host_rate('node_network_receive_drop_total', INTERFACE)),
                              (net_legend + ' / 发', host_rate('node_network_transmit_drop_total', INTERFACE))], 'pps', '各网卡每秒接收/发送丢包数；结合错误、吞吐及队列排查网络压力。')

    row('vLLM 核心性能 · 任务 / 推理实例')
    chart('vLLM 请求结束速率', [('{{finished_reason}}', rate('vllm:request_success_total', ('finished_reason',)))], 'reqps',
          '每秒结束的请求数，按 stop/length/abort 等实际原因分组；不是发送 QPS 或 HTTP 成功率。')
    chart('vLLM Token 吞吐', [('输入 Token/s', rate('vllm:prompt_tokens_total')), ('输出 Token/s', rate('vllm:generation_tokens_total'))],
          'suffix:Token/s', '每秒处理的输入和生成 Token 数；观察吞吐是否随并发增长或进入平台期。')
    chart('vLLM 运行/排队请求', [('运行', 'sum('+metric('vllm:num_requests_running')+')'), ('排队', 'sum('+metric('vllm:num_requests_waiting')+')')],
          description='正在执行和等待调度的请求总数；持续排队说明到达负载超过当前处理能力。')
    chart('vLLM TTFT', quantiles('vllm:time_to_first_token_seconds'), 's', '服务端首 Token 延迟，含排队和 Prefill；展示 P50/P95/P99 及样本加权平均。')
    chart('vLLM 端到端延迟', quantiles('vllm:e2e_request_latency_seconds'), 's', '服务端从接收到完成请求的耗时；不包含完整客户端网络路径。')
    chart('vLLM KV Cache 使用率', [(engine_legend, metric('vllm:kv_cache_usage_perc')+' or '+metric('vllm:gpu_cache_usage_perc'))],
          'percentunit', '各实例/引擎的 KV 缓存块占用比例，1=100%；接近满载并伴随抢占时关注缓存容量。')
    chart('vLLM 输出间隔 ITL', quantiles('vllm:inter_token_latency_seconds'), 's', '相邻服务端输出事件的间隔；一次输出可能包含多个 Token，不等于请求级 TPOT。')
    chart('vLLM 排队时间', quantiles('vllm:request_queue_time_seconds'), 's', '请求处于等待调度阶段的累计时间；用于区分排队与执行耗时。')
    chart('vLLM 抢占速率', [(instance_legend, rate('vllm:num_preemptions_total', ('instance',)))], 'ops', '各实例每秒抢占次数；升高时结合 KV 占用和长请求排查重计算开销。')

    row('vLLM 执行与负载诊断 · 任务 / 推理实例')
    chart('vLLM 请求级 TPOT', quantiles('vllm:request_time_per_output_token_seconds'), 's', '每请求首 Token 后的平均输出 Token 耗时；与 ITL 分开统计，短输出口径随版本变化。')
    chart('vLLM Prefill 耗时', quantiles('vllm:request_prefill_time_seconds'), 's', '每请求 Prefill 阶段墙钟耗时；长输入或缓存未命中时通常升高，不是纯 GPU 内核时间。')
    chart('vLLM Decode 耗时', quantiles('vllm:request_decode_time_seconds'), 's', '每请求 Decode 阶段总耗时；受输出长度和调度影响，需与 TPOT 一起看。')
    chart('vLLM 输入 Token 分布', quantiles('vllm:request_prompt_tokens'), 'suffix:Token', '完成请求的实际输入长度分布；用于核对 workload 和长输入尾部。')
    chart('vLLM 输出 Token 分布', quantiles('vllm:request_generation_tokens'), 'suffix:Token', '完成请求的实际生成长度分布；核对是否因 EOS 或长度限制提前结束。')
    chart('vLLM 前缀缓存命中率', [('Token 命中率', rate('vllm:prefix_cache_hits_total')+' / ('+rate('vllm:prefix_cache_queries_total')+' > 0)')],
          'percentunit', '窗口内命中 Token / 查询 Token，加权合并实例；不是请求命中率，无查询时无数据。')
    chart('vLLM 实例结束速率', [(instance_legend, rate('vllm:request_success_total', ('instance',)))], 'reqps', '分别比较各实例每秒结束请求数，定位负载不均；包含该计数器记录的结束原因。')
    chart('vLLM 实例输出吞吐', [(instance_legend, rate('vllm:generation_tokens_total', ('instance',)))], 'suffix:Token/s', '分别比较各实例输出 Token/s，避免总吞吐掩盖慢副本。')
    chart('vLLM 实例运行/排队', [(instance_legend+' / 运行', 'sum by (instance)('+metric('vllm:num_requests_running')+')'),
                                    (instance_legend+' / 排队', 'sum by (instance)('+metric('vllm:num_requests_waiting')+')')],
          description='各实例当前运行和等待请求数；识别单个副本排队或路由倾斜。')
    chart('vLLM 实例 TTFT P99', [quantiles('vllm:time_to_first_token_seconds', ('instance',))[2]], 's', '各实例首 Token 延迟 P99；不对实例分位数求平均。')
    chart('vLLM 请求执行时间', quantiles('vllm:request_inference_time_seconds'), 's', '请求处于执行阶段的墙钟时间，含 Prefill/Decode；不是 CPU/GPU 纯计算时间。')
    chart('vLLM 每轮调度 Token 分布', quantiles('vllm:iteration_tokens_total'), 'suffix:Token', '每次引擎迭代处理的 Token 数，混合输入和输出；不是请求 batch 大小。')

    row('SGLang 核心性能 · 任务 / 推理实例')
    chart('SGLang 运行/排队请求', [('运行', 'sum('+metric('sglang:num_running_reqs')+')'), ('排队', 'sum('+metric('sglang:num_queue_reqs')+')')],
          description='当前运行和等待调度的请求总数；持续排队时结合缓存和延迟定位容量限制。')
    chart('SGLang 输出 Token 吞吐', [('Token/s', 'sum('+metric('sglang:gen_throughput')+')')], 'suffix:Token/s', '调度器上报的输出 Token/s；不同于按完成请求计数器计算的吞吐窗口。')
    chart('SGLang 缓存命中率（实例）', [(instance_legend, metric('sglang:cache_hit_rate'))], 'percentunit', '各调度器上报的前缀缓存命中比例，1=100%；不跨实例简单平均。')
    chart('SGLang TTFT', quantiles('sglang:time_to_first_token_seconds'), 's', '服务端首 Token 延迟，含排队和 Prefill；流式/非流式终点定义依服务版本。')
    chart('SGLang 端到端延迟', quantiles('sglang:e2e_request_latency_seconds'), 's', '服务端从接收到完成请求的耗时；用于结合排队时间判断执行开销。')
    chart('SGLang 每 Token 延迟', quantiles('sglang:inter_token_latency_seconds'), 's', '服务端输出间隔按新增 Token 数折算的延迟分布；多 Token 输出时不同于原始事件间隔。')
    chart('SGLang 请求完成速率', [('请求/s', rate('sglang:num_requests_total'))], 'reqps', '每秒正常走完完成统计的请求数；中止另计，不代表入口请求总量。')
    chart('SGLang 请求中止速率', [('中止/s', rate('sglang:num_aborted_requests_total'))], 'reqps', '每秒中止请求数，可能含客户端取消或服务异常；需结合错误日志判断原因。')
    chart('SGLang 完成请求 Token 吞吐', [('输入 Token/s', rate('sglang:prompt_tokens_total')), ('输出 Token/s', rate('sglang:generation_tokens_total'))],
          'suffix:Token/s', '完成请求累计 Token 的每秒速率；长请求完成时可能突增，不等于逐 Decode 步瞬时吞吐。')

    row('SGLang 缓存、调度与负载诊断 · 任务 / 推理实例')
    chart('SGLang KV Token 容量使用率', [(instance_legend, metric('sglang:token_usage'))], 'percentunit', '各调度器 KV Token 池使用比例，1=100%；不是整卡显存使用率。')
    chart('SGLang 已用 KV Token 数', [(instance_legend, metric('sglang:num_used_tokens'))], 'suffix:Token', '当前 KV Token 池已用 Token 数；与容量使用率一起观察长上下文压力。')
    chart('SGLang 排队时间', quantiles('sglang:queue_time_seconds'), 's', '调度器记录的等待队列耗时；PD 的其他队列需结合分阶段指标查看。')
    chart('SGLang 分阶段耗时 P95', [quantiles('sglang:per_stage_req_latency_seconds', ('instance', 'stage'))[1]], 's', '按实例和原始 stage 分别展示请求阶段耗时 P95；不混合不同阶段，也不等同纯 GPU 耗时。')
    chart('SGLang 周期重调度请求', [(instance_legend, metric('sglang:num_retracted_reqs'))], description='最近调度统计周期的回退/重调度请求数；是采样 Gauge，非累计计数或当前队列长度，不能求 rate。')
    chart('SGLang 每请求重调度次数', quantiles('sglang:num_retractions'), 'short', '已完成请求的重调度次数分布；用于观察缓存压力带来的重复执行。')
    chart('SGLang 输入 Token 分布', quantiles('sglang:prompt_tokens_histogram'), 'suffix:Token', '完成请求的实际输入长度分布；需在服务端启用 --collect-tokens-histogram。')
    chart('SGLang 输出 Token 分布', quantiles('sglang:generation_tokens_histogram'), 'suffix:Token', '完成请求的实际生成长度分布；需在服务端启用 --collect-tokens-histogram。')
    chart('SGLang 完成请求缓存命中率', [('Token 命中率', rate('sglang:cached_tokens_total')+' / ('+rate('sglang:prompt_tokens_total')+' > 0)')],
          'percentunit', '完成请求缓存 Token / 输入 Token，按 Token 加权；尚未创建命中序列时无数据，可对照调度器命中率。')
    chart('SGLang 实例输出吞吐', [(instance_legend, rate('sglang:generation_tokens_total', ('instance',)))], 'suffix:Token/s', '分别比较各实例完成请求的输出 Token/s；长请求完成时计数可能成批更新。')
    chart('SGLang 实例运行/排队', [(instance_legend+' / 运行', 'sum by (instance)('+metric('sglang:num_running_reqs')+')'),
                                      (instance_legend+' / 排队', 'sum by (instance)('+metric('sglang:num_queue_reqs')+')')],
          description='各实例当前运行和排队请求数；用于发现路由倾斜或慢副本。')
    chart('SGLang 实例 TTFT P99', [quantiles('sglang:time_to_first_token_seconds', ('instance',))[2]], 's', '各实例首 Token 延迟 P99；避免总体分位数掩盖单实例瓶颈。')
    if cfg.get('topology', {}).get('kind') == 'pd':
        row('SGLang PD 传输诊断（需服务暴露相应指标）')
        chart('SGLang PD 队列', [(instance_legend+' / '+label, metric('sglang:'+name)) for label, name in (
            ('Prefill 预分配', 'num_prefill_prealloc_queue_reqs'), ('Prefill 传输中', 'num_prefill_inflight_queue_reqs'),
            ('Decode 预分配', 'num_decode_prealloc_queue_reqs'), ('Decode 传输', 'num_decode_transfer_queue_reqs'))],
              description='PD 各阶段当前队列请求数；定位 KV 预分配或传输阶段积压。')
        chart('SGLang PD KV 传输耗时', [(instance_legend+' / '+label, metric('sglang:'+name)) for label, name in (
            ('传输', 'kv_transfer_latency_ms'), ('握手', 'kv_transfer_bootstrap_ms'), ('分配等待', 'kv_transfer_alloc_ms'))], 'ms',
              '实例上报的 KV 传输、握手和分配等待毫秒数；是 Gauge，不是耗时分位数。')
        chart('SGLang PD KV 传输速率', [(instance_legend, metric('sglang:kv_transfer_speed_gb_s'))], 'suffix:GB/s', '实例上报的 KV 传输 GB/s；仅 PD 模式有意义，不代表整机网卡吞吐。')
        chart('SGLang PD 失败速率', [(instance_legend+' / '+label, rate('sglang:'+name, ('instance',))) for label, name in (
            ('握手', 'num_bootstrap_failed_reqs_total'), ('传输', 'num_transfer_failed_reqs_total'))], 'reqps',
              '每秒 PD 握手或传输失败数；结合队列、网卡和服务日志定位链路问题。')

    row('HTTP 生成接口（需暴露 http_requests_total）')
    http_filter = ',method="POST",handler=~".*/v1/(chat/completions|completions)"'
    http_total = rate('http_requests_total', filters=http_filter)
    http_errors = rate('http_requests_total', filters=http_filter+',status=~"4..|5.."')
    chart('HTTP 生成接口响应速率', [('{{status}}', rate('http_requests_total', ('status',), http_filter))], 'reqps',
          '生成接口完成响应的每秒速率，按 HTTP 状态分组；流式 2xx 不保证后续生成成功。')
    chart('HTTP 4xx/5xx 速率', [('{{status}}', rate('http_requests_total', ('status',), http_filter+',status=~"4..|5.."'))], 'reqps',
          '每秒客户端/服务端错误响应数；不包含已返回 2xx 后发生的流式错误。')
    chart('HTTP 4xx/5xx 比例', [('错误响应比例', '('+http_errors+' or (0 * '+http_total+')) / ('+http_total+' > 0)')], 'percentunit',
          '窗口内 4xx/5xx 占生成接口响应的比例；只在观测到响应时补零，无接口指标或无流量时无数据。')

    row(('NVIDIA GPU' if vendor == 'nvidia' else '海光 DCU v2.4.1') + ' · 任务 → 节点 → 型号 → 卡序号')
    legend = '{{gpu_node}} / {{gpu_model}} / 卡{{gpu_index}}'
    def gpu(name):
        return name + '{' + GPU + '}'
    if vendor == 'nvidia':
        chart('GPU 利用率', [(legend, gpu('DCGM_FI_DEV_GPU_UTIL'))], 'percent', '整卡 GPU 活动时间比例；高值不等于 Tensor 算力或显存带宽已饱和。')
        chart('GPU 显存占用', [(legend, '100 * '+gpu('DCGM_FI_DEV_FB_USED')+' / ('+gpu('DCGM_FI_DEV_FB_USED')+' + '+gpu('DCGM_FI_DEV_FB_FREE')+')')], 'percent', '整卡已用显存比例，含权重、KV 和运行开销；不等同 KV Cache 使用率。')
        chart('GPU 已用显存', [(legend, gpu('DCGM_FI_DEV_FB_USED')+' * 1048576')], 'bytes', '各卡已用显存字节数，包含同卡其他进程。')
        chart('GPU 功率', [(legend, gpu('DCGM_FI_DEV_POWER_USAGE'))], 'watt', '各卡当前功耗；结合温度、时钟和利用率观察功耗限制。')
        chart('GPU 温度', [(legend, gpu('DCGM_FI_DEV_GPU_TEMP'))], 'celsius', '各卡 GPU 温度；高温伴随时钟下降时核查散热和降频。')
        chart('GPU 时钟', [('SM '+legend, gpu('DCGM_FI_DEV_SM_CLOCK')), ('显存 '+legend, gpu('DCGM_FI_DEV_MEM_CLOCK'))], 'suffix:MHz', '各卡 SM 与显存频率；与功耗、温度一起观察降频。')
        chart('最近 XID 错误代码', [(legend, gpu('DCGM_FI_DEV_XID_ERRORS'))], description='这是错误代码，不是错误计数；0 表示当前没有上报 XID。')
        if cfg.get('gpu', {}).get('nvidia_metrics_profile', 'basic') == 'full':
            chart('GPU profiling 活跃率（需支持）', [('SM '+legend, gpu('DCGM_FI_PROF_GR_ENGINE_ACTIVE')), ('Tensor '+legend, gpu('DCGM_FI_PROF_PIPE_TENSOR_ACTIVE'))], 'percentunit', '图形/计算引擎与 Tensor 管线活动比例；需硬件和 DCGM profiling 支持，不等于达到峰值算力。')
            chart('GPU profiling DRAM 活跃率（需支持）', [(legend, gpu('DCGM_FI_PROF_DRAM_ACTIVE'))], 'percentunit', '显存接口活动比例；作为带宽压力线索，需 DCGM profiling 支持。')
        else:
            chart('GPU 空闲显存', [(legend, gpu('DCGM_FI_DEV_FB_FREE')+' * 1048576')], 'bytes', '各卡当前空闲显存；关注是否有扩展 KV 或并发的空间。')
            chart('可见 GPU 卡数', [('卡数', 'count('+gpu('DCGM_FI_DEV_GPU_UTIL')+')')], kind='stat', description='当前筛选下采集到利用率指标的 GPU 序列数；不是配置文件声明的卡数。')
    else:
        # Physical-card charts collapse copies carrying Kubernetes/pod metadata.
        # Never sum duplicate copies of the same card, and never mix vdcu_* into dcu_*.
        def card(name):
            return 'max by (task,gpu_node,gpu_model,gpu_index) (' + gpu(name) + ')'
        chart('海光 DCU 利用率', [(legend, card('dcu_utilizationrate'))], 'percent', '各物理卡的设备活动比例；去除同卡元数据副本，不表示任务独占算力。')
        chart('海光已用显存', [(legend, card('dcu_usedmemory_bytes'))], 'bytes', '各物理卡已用显存字节数，含权重、KV 和其他进程开销。')
        chart('海光显存容量', [(legend, card('dcu_memorycap_bytes'))], 'bytes', 'Exporter 上报的各物理卡显存总容量。')
        chart('海光显存使用率', [(legend, '100 * '+card('dcu_usedmemory_bytes')+' / ('+card('dcu_memorycap_bytes')+' > 0)')], 'percent', '已用显存 / 总容量；与服务的 KV Token 容量比例区分。')
        chart('海光 DCU 温度', [(legend, card('dcu_temp'))], 'celsius', '各物理卡温度；结合功耗和时钟排查热约束。')
        chart('海光 DCU 功率', [(legend, card('dcu_power_usage'))], 'watt', '各物理卡当前功耗；结合利用率、温度和时钟判断设备状态。')
        chart('海光 PCIe 带宽', [(legend, card('dcu_pciebw_mb'))], 'MiBs', '官方 DCU 指标上报的 PCIe 传输带宽；不是整机网卡或 KV 传输速度。')
        chart('海光核心时钟', [(legend, card('dcu_sclk'))], 'suffix:MHz', '各物理卡当前核心频率；结合温度和功耗观察降频。')
        chart('海光错误计数（原值）', [('CE '+legend, card('dcu_ce_count')), ('UE '+legend, card('dcu_ue_count'))], description='按官方 DCU 面板显示原始计数，不把未知计数类型当作速率。')
        chart('可见 DCU 卡数', [('卡数', 'count('+card('dcu_memorycap_bytes')+')')], kind='stat', description='当前筛选下采集到显存容量的物理卡数，按节点/型号/卡号去重。')

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
        prom_variable('inference_instance','推理实例', 'up{'+TASK+',job="inference"}', 'instance'),
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
            'description':'按任务/推理实例查看 SGLang/vLLM 的吞吐、延迟、缓存和调度；主机/GPU/日志独立筛选。版本或功能未暴露的指标显示无数据；HTTP 2xx 不代表流式生成成功。'+(' 当前为模拟数据。' if cfg.get('simulation') else ''),
            'tags':['inference','task',vendor],'schemaVersion':39,'version':3,'editable':False,'timezone':'browser',
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
