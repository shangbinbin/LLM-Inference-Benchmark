<!--
 * @Author: shangbinbin shangbinbin1993@outlook.com
 * @Date: 2026-07-27 15:21:52
 * @LastEditors: shangbinbin shangbinbin1993@outlook.com
 * @LastEditTime: 2026-08-01 16:43:19
 * @FilePath: /LLM-Inference-Benchmark/references/nvidia-h200-3node-8gpu/prometheus/README.md
 * @Description: prometheus配置说明
 * 
 * Copyright (c) 2026 by shangbinbin shangbinbin1993@outlook.com, All Rights Reserved. 
-->

# prometheus配置说明

> 本文及配套文件来自 `sglang-inference-platform`，作为压测链路搭建参考。
> 来源版本、适用范围和配置适配说明见[参考配置索引](../README.md)。

Prometheus 组件使用的当前配置文件参见[此处](./prometheus.yml)。该配置已切换到 GLM5.2
三节点、每节点 8 GPU 场景，只抓取 `glm52-sglang-3node` 普通 Service 暴露的 rank 0
`/metrics`，并继续抓取三个节点的 DCGM Exporter。使用的镜像由
[此处](./prometheus-dockerfile/Dockerfile)的 Dockerfile 构建，启动命令如下：

```shell
/bin/prometheus --config.file=/private/benchmark-data/prometheus/config/prometheus.yml --storage.tsdb.path=/private/benchmark-data/prometheus/data --web.console.libraries=/usr/share/prometheus/console_libraries --web.console.templates=/usr/share/prometheus/consoles --web.enable-lifecycle
```

如需在相同三节点场景中同时抓取 node-exporter IB 指标并加载 IB 告警规则，改用
[`prometheus-distributed-example.yml`](./prometheus-distributed-example.yml)：

```shell
/bin/prometheus --config.file=/private/benchmark-data/prometheus/config/prometheus-distributed-example.yml --storage.tsdb.path=/private/benchmark-data/prometheus/data --web.console.libraries=/usr/share/prometheus/console_libraries --web.console.templates=/usr/share/prometheus/consoles --web.enable-lifecycle
```

其中的`prometheus`文件夹层级结构如下所示：

```console
[root /private/benchmark-data]$tree ./prometheus/ -L 2
./prometheus/
├── config
│   ├── prometheus.yml
│   ├── prometheus-distributed-example.yml
│   └── rules
│       └── ib-network-alerts.yml
├── data
│   ├── 01KY7A3R6J85YYT2YDCSGZB077
│   ├── 01KY97XBCZPE27AM12MX5039GV
│   ├── 01KYAYV55V5GNHRNXXFE5BY18T
│   ├── 01KYD3GDX9673TV9R7KBQ7Y6M6
│   ├── 01KYF19ZG1RJ6S97G46V2DE7XT
│   ├── 01KYGR7RQPGJCN56CZVJSQ1M6E
│   ├── 01KYGZ3FZF7VPJQT7BCJAMSTQ9
│   ├── 01KYGZ3GK7HWHY938MS0JHMBT8
│   ├── 01KYH5Z77G4B6H9YGMJQK5AWF9
│   ├── chunks_head
│   ├── lock
│   ├── queries.active
│   └── wal
└── prometheus-dockerfile
    └── Dockerfile

15 directories, 6 files
```

其中的`Dockerfile`对应的就是前面构建使用的prometheus组件镜像。

使用当前镜像和前述的配置文件，在k8s环境中实际启动的prometheus pod对应的yaml文件配置参见[此处](./prometheus-pod-example.yml)。

`static_configs.labels` 是采集阶段附加到每条 series 的 target labels。它们必须与 Grafana
Dashboard 中使用的 `deployment`、`inference_group` 等过滤名称一致，但不会修改指标名或
SGLang 原生 labels。例如，SGLang 暴露
`sglang:num_running_reqs{model_name="glm-5.2"}` 后，当前配置会额外附加
`deployment="glm52-sglang-3node"`、`model="glm-5.2"` 等标签；最终 series 会同时拥有
`model_name` 和 `model`。Dashboard 应查询带冒号的 `sglang:*` 指标，并按实际语义选择
`model_name` 或 `model`，不能用 Prometheus 静态标签替代 SGLang 原生指标名称。

三节点 TP=8/PP=3 分布式推理场景下，`prometheus.yml` 和完整分布式示例都只采集
rank 0/master Service 指标。rank 1/2 的 SGLang 应用指标不应在未确认指标语义前重复采集。

该分布式配置同时复用集群中已经存在的
`ampha-business/prometheus-node-exporter` 无头 Service，通过 DNS service discovery 为每个
Endpoint 建立独立 target，并加载 [IB 网卡告警规则](./rules/ib-network-alerts.yml)。完整的
三节点的 IB HCA 接入、现场验证、PromQL 和故障排查步骤参见
[IB 网卡监控配置说明](../ib-network-monitoring/README.md)。

当前现场 IB-only 响应已确认为 node_exporter 0.17.0，实际提供 RX/TX 字节、
单播/组播包数、link-down 和 link-error-recovery counter，但不提供 `info`、
`state_id`、额定速率、transmit-wait 和新版完整 errors/discards 指标。告警规则
和 Dashboard 已按这一实际指标集改写，不再用空 time series 判断端口状态。
上传样本每节点实际可见 10 个 device，规则将 10 作为 exporter 宿主机基线；
本次业务计划使用的 8 个 HCA 需另外通过宿主机与 NCCL 日志建立映射。

IB 专用 job 已通过 `params.collect[]` 要求 node-exporter 只执行 `infiniband`
collector，与现场验证使用的 `metrics?collect[]=infiniband` 请求保持一致。该响应仍会
提供 exporter build info 和 collector health。现场实测完整 `/metrics` 首字节需
2.4～5.5 秒，会超过
3 秒 `scrape_timeout`，而 IB-only 响应约 0.2 秒。仅使用
`metric_relabel_configs` 无法解决这个问题，因为它在 Prometheus 收完响应之后才执行。
现场出现 `context deadline exceeded` 和 exporter `broken pipe` 时，按说明文档
第 4.3 节对比完整与 IB-only 端点，并检查 collector duration。
规则文件中的 `NodeExporterIBScrapeSlow` 会在成功抓取持续超过 2 秒时预警，
在达到 3 秒硬超时之前暴露性能回归。

部署分布式配置时，必须把配置和规则保持以下相对层级同步到 Prometheus 挂载目录：

```text
/private/benchmark-data/prometheus/config/prometheus-distributed-example.yml
/private/benchmark-data/prometheus/config/rules/ib-network-alerts.yml
```

先执行 `promtool check config` 和 `promtool check rules`，全部通过后才能 reload。当前仓库
没有配置 Alertmanager；规则可在 Prometheus `/alerts` 中查看，但不会自行发送通知。

## 分布式多 Pod 扩展规则

- 一个 TP/PP 推理组默认只抓取该组 frontend/master；不要把会解析到所有 Rank 的
  headless Service 作为 target。
- 多个独立 DP 副本或推理组各自接收请求时，每个组的 frontend 都要成为独立 target；
  不要只抓取可能随机落到不同后端的负载均衡 Service。
- 每个 target 必须设置 `deployment`、`inference_group`、`topology`、`role`、
  `node_rank`、`replica_id` 和 `pod`。这些低基数标签与 Alloy 日志标签保持一致。
- worker 只有在 SGLang 明确暴露 rank 局部指标且已经确认聚合语义后才加入 target。
  master 已聚合的请求数、吞吐和延迟不能与 worker 再次求和。

当前三节点样例只有一个推理组 `glm52-sglang-3node-group-0`，因此两份配置都只包含
`glm52-sglang-3node-0` 对应的 master Service `glm52-sglang-3node`。
扩展到第二个独立推理组时，复制该 static target 并修改 Service、`inference_group`、
`replica_id` 和 `pod`；目标数量较多时应改用 Kubernetes service discovery 与受控 relabel，
同时确保只保留 frontend Pod。

## NVIDIA DCGM Exporter GPU 采集

`prometheus.yml` 和 `prometheus-distributed-example.yml` 均增加了
`job_name: nvidia-dcgm-exporter`，每 5 秒采集以下三个现场端点：

| target | Prometheus 附加标签 | Exporter 原生主机标签 |
|---|---|---|
| `10.0.3.1:9400` | `node="jbsw-gpu01"` | `Hostname="jbsw-gpu01"` |
| `10.0.3.2:9400` | `node="jbsw-gpu02"` | `Hostname="jbsw-gpu02"` |
| `10.0.3.3:9400` | `node="jbsw-gpu03"` | `Hostname="jbsw-gpu03"` |

每个 target 独立设置 `node`，是为了让 `up` 指标在 exporter DOWN、没有任何 DCGM series
时仍然可以定位到节点。Exporter 原生的 `gpu`、`UUID`、`device`、`modelName`、
`DCGM_FI_DRIVER_VERSION`、`pod`、`namespace` 和 `container` 不做重命名或过滤。

配置 reload 后先检查：

```promql
up{job="nvidia-dcgm-exporter"}
```

再检查每个节点的 GPU 数量：

```promql
count by (node) (
  DCGM_FI_DEV_GPU_UTIL{job="nvidia-dcgm-exporter"}
)
```

现场三个节点各暴露 8 张 NVIDIA H200，预期结果分别为 8、合计 24 张。Grafana 使用
[NVIDIA DCGM GPU Dashboard](../grafana/dashboards/nvidia-dcgm-gpu-dashboard.json)
展示利用率、显存、温度、功耗、时钟、profiling、PCIe 和错误指标。完整部署、指标口径及
排障步骤参见 [GPU 监控配置说明](../gpu-monitoring/README.md)。
