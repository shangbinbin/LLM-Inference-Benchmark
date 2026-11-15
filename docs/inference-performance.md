# 压测时用 Grafana 定位推理瓶颈

原有吞吐、延迟和 GPU 利用率能观察性能变化；定位原因还需结合调度、执行阶段、缓存压力、请求长度和实例差异。任务总览现在为 vLLM、SGLang 各提供 21 个性能/诊断面板，并与主机、GPU/DCU 和日志联动观察。所有指标面板都附有简短释义。

选择“推理任务”后选择“推理实例”，支持单选、多选和 All。推理实例是被抓取的 `host:port` 指标端点，不等于 GPU 卡或 OS 进程。All 只汇总所选端点：如果 router、Prefill、Decode 或多个 TP rank 重复上报同一请求，须选择统计边界不重复的端点，不能将重复计数当作整体吞吐。主机、GPU 和日志下拉独立于推理实例。

## 指标对应关系

| 诊断方向 | vLLM | SGLang |
| --- | --- | --- |
| 请求流量 | `request_success_total` 按 finished_reason 展示结束速率 | `num_requests_total` 完成速率、`num_aborted_requests_total` 中止速率 |
| Token 吞吐 | `prompt_tokens_total`、`generation_tokens_total` 的速率 | `gen_throughput` 调度器值，以及 prompt/generation 计数器的完成请求速率 |
| 运行与排队 | `num_requests_running`、`num_requests_waiting` | `num_running_reqs`、`num_queue_reqs` |
| 首 Token / 全请求延迟 | `time_to_first_token_seconds`、`e2e_request_latency_seconds` | 同名时间直方图 |
| 每 Token 延迟 | `inter_token_latency_seconds` 输出间隔；`request_time_per_output_token_seconds` 请求级 TPOT | `inter_token_latency_seconds`，按新增 Token 数折算的间隔 |
| 执行阶段 | `request_queue_time_seconds`、`request_prefill_time_seconds`、`request_decode_time_seconds`、`request_inference_time_seconds` | `queue_time_seconds`、`per_stage_req_latency_seconds`（保留 instance/stage） |
| 缓存容量 | `kv_cache_usage_perc`，旧名 `gpu_cache_usage_perc` 作同序列回退 | `token_usage`、`num_used_tokens` |
| 缓存效果 | `prefix_cache_hits_total / prefix_cache_queries_total` 的窗口速率比 | 调度器 `cache_hit_rate`，以及完成请求 `cached_tokens_total / prompt_tokens_total` 的窗口速率比 |
| 调度压力 | `num_preemptions_total` 每秒抢占数 | `num_retracted_reqs` 最近调度周期上报值；`num_retractions` 每完成请求的次数分布 |
| 实际负载长度 | `request_prompt_tokens`、`request_generation_tokens` 直方图 | `prompt_tokens_histogram`、`generation_tokens_histogram` |
| 调度批量 | `iteration_tokens_total` 直方图，表示每次引擎迭代处理的输入+输出 Token 数 | 当前不臆造等价的 batch 指标 |
| 副本差异 | 实例结束速率、输出吞吐、运行/排队、TTFT P99 | 实例输出吞吐、运行/排队、TTFT P99 |

表中省略 `vllm:` / `sglang:` 前缀。`*_total` 计数器使用 rate，Gauge 使用当前值；Histogram 使用 `_bucket`、`_sum` 和 `_count`。

时间和长度分布展示 P50/P95/P99 与按样本数加权的平均值。分位数先汇总桶再计算，不平均实例分位数；不同版本若桶边界不同，应分别选实例查看。无样本时不显示分位数或平均值。分阶段图按 stage 和实例独立计算 P95，不把不同阶段混成一个延迟。

## 需要区分的口径

- **TPOT 与 ITL：** vLLM 请求级 TPOT 是每个请求首 Token 后的平均输出耗时；ITL 是相邻输出事件的间隔，一次输出可能有多个 Token。旧的 `vllm:time_per_output_token_seconds` 是弃用的间隔指标，不能当作当前请求级 TPOT。SGLang 的输出间隔按新增 Token 数折算，跨引擎比较前须核对版本与采样方式。
- **结束、完成、中止与 HTTP：** vLLM 按实际 finished_reason 分组，包含计数器记录的 abort 等原因；SGLang 完成与中止单独计数。HTTP 4xx/5xx 只描述接口响应，流式请求可能先返回 2xx 再失败。这些都不能直接替代 AISBench 客户端成功率。
- **瞬时与完成时统计：** SGLang 的 prompt/generation/cached Token 计数器在完成请求时更新，长请求完成时会成批增加；`gen_throughput` 是调度器采样值，两者时间窗口不同。缓存命中率按 Token 加权，不平均实例比例。v0.5.5 的缓存命中计数序列在首次命中时才创建，此前完成请求命中率可能无数据，可对照调度器的 `cache_hit_rate`，不把缺失计数器臆测为零。
- **缓存与显存：** 服务 KV 使用率描述缓存块或 Token 池，GPU/DCU 显存包含权重、缓存和运行开销。整卡显存很高而 KV 使用率不高并不矛盾。
- **重调度：** `sglang:num_retracted_reqs` 是最近调度统计周期的采样 Gauge，不是当前队列长度或累计次数，不能求 rate；其周期归零逻辑见[调度统计实现](https://github.com/sgl-project/sglang/blob/v0.5.5/python/sglang/srt/managers/scheduler_metrics_mixin.py)。`sglang:num_retractions` 是完成请求的次数直方图。vLLM 抢占计数使用累计 counter 的速率。
- **HTTP 无错误序列：** 错误比例仅在生成接口已有响应计数时把缺失的错误类别视为零；接口指标缺失或窗口无流量时仍为 No data。接口过滤为 POST `/v1/completions` 或 `/v1/chat/completions`，兼容路径前缀。

## 常见瓶颈线索

| 观察组合 | 下一步核查 |
| --- | --- |
| 排队持续增长、TTFT 升高，Prefill 耗时相对稳定 | 到达负载与服务容量是否匹配；对比实例队列，核查并发和路由 |
| TTFT、Prefill 耗时和输入长度一起升高，前缀命中下降 | 输入分布是否改变、前缀复用是否减少，是否存在长输入阻塞 |
| KV 使用率接近满载，抢占/重调度增加，吞吐下降 | 长上下文/并发造成的 KV 容量压力、重计算和调度开销 |
| TPOT/每 Token 延迟升高，输出长度相近，吞吐进入平台期 | Decode 计算或显存访问压力；结合设备 profiling、时钟和功耗进一步确认 |
| 总体正常，但单个实例排队、TTFT P99 异常 | 网关分流、实例资源差异、慢副本或实例级异常 |
| 实际输出变短、length/abort 或中止速率升高 | 上下文与输出长度限制、客户端取消、OOM 和服务日志 |
| HTTP 4xx/5xx 或中止上升 | 鉴权、参数、限流、内存及后端异常；按请求时间关联日志 |
| 客户端慢、服务端延迟较低，GPU 利用率较低 | 压测客户端 CPU/内存、网卡、网关与网络；不能直接断定 GPU 性能不足 |
| PD 队列积压、KV 传输耗时/失败率升高 | KV 握手/预分配、传输链路、网卡吞吐/丢包和两侧日志 |

这些组合用于缩小排查范围，不能仅凭单个指标确定根因。NVIDIA `full` 模式可补充 Tensor/DRAM 等活动指标，但需硬件及 DCGM profiling 支持；具体内核、通信和进程级瓶颈仍需相应 profiler。AISBench 的发送并发、请求成功率和客户端 SLO 继续以本次结果报告为准，按时间范围与 Grafana 对齐。

## 版本与启用条件

- 已核对 [vLLM v0.11.0 指标实现](https://github.com/vllm-project/vllm/blob/v0.11.0/vllm/v1/metrics/loggers.py)、[SGLang v0.5.5 指标实现](https://github.com/sgl-project/sglang/blob/v0.5.5/python/sglang/srt/metrics/collector.py)，以及 [vLLM 官方指标说明](https://docs.vllm.ai/en/stable/design/metrics/)和 [SGLang 官方指标说明](https://docs.sglang.io/docs/references/production_metrics)。现场版本是否暴露这些指标仍以实际 `/metrics` 为准。
- SGLang 需要 `--enable-metrics`；长度直方图还需 `--collect-tokens-histogram`。工具不自动修改外部推理服务启动参数。
- SGLang PD 四个额外面板仅在现场配置 `topology.kind: pd` 时生成。KV 传输延迟/速率是实例上报的 Gauge，不计算成延迟分位数。
- HTTP 三个面板依赖服务实际暴露 `http_requests_total`，兼容 `status="2xx"` 与 `status="200"` 两种标签值；缺失时保留 No data，不臆造后端错误指标。
- 未启用的功能、未支持的旧版本或缺少的指标均显示 No data；只有已有接口响应计数的 HTTP 错误比例会按上述规则补零。新面板无需重建镜像：更新源码后用原配置 `down`，再 `up --no-benchmark` 即可生成加载。

## 可复现验证

```bash
python3 -m unittest discover -s tests
# 需要已有 Prometheus 镜像；不会拉取镜像或访问服务
python3 tests/verify_inference_promql.py --image prom/prometheus:v3.5.0
# 或使用本机工具：python3 tests/verify_inference_promql.py --promtool /path/to/promtool
```

后者解析全部 Dashboard PromQL，并用多实例、不同样本权重、缺失/空闲指标及 HTTP 状态等固定序列验证数值和筛选语义。
