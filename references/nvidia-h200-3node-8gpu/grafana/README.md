<!--
 * @Author: shangbinbin shangbinbin1993@outlook.com
 * @Date: 2026-07-27 13:47:44
 * @LastEditors: shangbinbin shangbinbin1993@outlook.com
 * @LastEditTime: 2026-08-01 16:43:19
 * @FilePath: /LLM-Inference-Benchmark/references/nvidia-h200-3node-8gpu/grafana/README.md
 * @Description: grafana配置说明
 * 
 * Copyright (c) 2026 by shangbinbin shangbinbin1993@outlook.com, All Rights Reserved. 
-->

# grafana配置说明

> 本文及配套文件来自 `sglang-inference-platform`，作为压测链路搭建参考。
> 来源版本、适用范围和配置适配说明见[参考配置索引](../README.md)。

grafana组件使用的配置文件参见[此处](./grafana.ini)，使用的镜像为`grafana:13.0.4-ubuntu-root`，该镜像通过[此处](./grafana-dockerfile/Dockerfile)的Dockerfile构建。grafana配置的启动命令如下所示：

```shell
GF_PATHS_CONFIG=/private/benchmark-data/grafana/grafana.ini GF_PATHS_DATA=/private/benchmark-data/grafana/data GF_PATHS_LOGS=/private/benchmark-data/grafana/logs GF_PATHS_PLUGINS=/private/benchmark-data/grafana/plugins GF_PATHS_PROVISIONING=/private/benchmark-data/grafana/provisioning GF_DATABASE_TYPE=sqlite3 GF_DATABASE_PATH=/private/benchmark-data/grafana/grafana.db GF_SERVER_HTTP_ADDR=0.0.0.0 GF_SERVER_HTTP_PORT=3000 GF_SECURITY_ADMIN_USER=admin GF_SECURITY_ADMIN_PASSWORD=123456 GF_USERS_ALLOW_SIGN_UP=false exec /run.sh
```

其中 `grafana` 文件夹内部层级结构如下所示：

```console
[root /private/benchmark-data]$tree ./grafana/ -L 2
./grafana/
├── dashboards
│   ├── ib-network-dashboard.json
│   ├── nvidia-dcgm-gpu-dashboard.json
│   ├── sglang-dashboard.json
│   └── sglang.yml
├── data
│   ├── csv
│   ├── grafana.db
│   ├── pdf
│   ├── png
│   └── unified-search
├── grafana.db
├── grafana-dockerfile
│   └── Dockerfile
├── grafana.ini
├── logs
├── plugins
│   ├── grafana-exploretraces-app
│   ├── grafana-lokiexplore-app
│   ├── grafana-metricsdrilldown-app
│   └── grafana-pyroscope-app
└── provisioning
    └── datasources

16 directories, 7 files
```

其中的`sglang-dashboard.json`文件参见[此处](./dashboards/sglang-dashboard.json)，`sglang.yml`文件参见[此处](./dashboards/sglang.yml)。同一个 provider 会扫描目录下全部 JSON，因此 [IB 网卡 Dashboard](./dashboards/ib-network-dashboard.json) 和 [NVIDIA DCGM GPU Dashboard](./dashboards/nvidia-dcgm-gpu-dashboard.json) 都不需要第二份 provisioning provider。

使用当前镜像和前述的配置文件，在k8s环境中实际启动的grafana pod对应的yaml文件配置参见[此处](./grafana-pod-example.yml)

## Dashboard 数据源与指标名称

当前 `sglang-dashboard.json` 不再绑定某个环境专属的 Prometheus UID，而是增加
`datasource` 类型变量。直接导入 JSON 后，在 Dashboard 顶部的
`Prometheus data source` 下拉框选择 Grafana 中已经能够查看指标的 Prometheus 数据源；
所有 panel target 和其余 query variable 都通过 `$datasource` 使用同一个数据源。

SGLang 原生指标名带冒号，例如：

```promql
sglang:num_running_reqs
sglang:gen_throughput
sglang:e2e_request_latency_seconds_bucket
sglang:time_to_first_token_seconds_bucket
```

Prometheus `static_configs.labels` 只会给这些 series 附加 `deployment`、
`inference_group`、`model` 等 target labels，不会把 `sglang:*` 改成 `sglang_*`，也不会
把 SGLang 原生的 `model_name` 改成 `model`。当前 Dashboard 的关联关系如下：

| Dashboard 变量 | 来源 | 是否必须在 `prometheus.yml` 中显式配置 |
|---|---|---|
| `datasource` | Grafana 中已创建的 Prometheus 数据源 | 否 |
| `deployment` | Prometheus target label | 是；当前配置已经提供 |
| `inference_group` | Prometheus target label | 是；当前配置已经提供 |
| `instance` | Prometheus 根据实际 scrape target 自动生成 | 否 |
| `model_name` | SGLang `sglang:*` 指标原生标签 | 否；值来自 SGLang served model name |

因此，Dashboard 中用于过滤的 target label 名必须与 Prometheus 最终存储的 label 名一致，
但不是所有 Dashboard label 都来自 `prometheus.yml`。当前三节点配置的静态
`model="glm-5.2"` 表示部署侧模型标识；原生 `model_name="glm-5.2"` 是 API served model name，
二者用途不同，不能仅因为 `prometheus.yml` 中存在 `model` 就删除 `model_name`。

## 直接导入后的无数据排查

1. 在 `Prometheus data source` 变量中选择 Metrics 页面实际使用的数据源。
2. 保持 `deployment`、`inference_group`、`instance`、`model_name` 为 `All`，并确认时间
   范围覆盖 SGLang 运行和压测时间。
3. 在 Grafana Explore 或 Prometheus 中先执行 `sglang:num_running_reqs`；若有结果，再执行
   `sglang:num_running_reqs{deployment=~".*",inference_group=~".*",instance=~".*"}`。
4. 若第一条有数据而变量无候选值，检查 Prometheus 实际加载的是单机还是分布式配置，
   并通过 series labels 确认 target labels。修改磁盘文件后还需要 reload/restart Prometheus。
5. 如果导入过旧版同 UID Dashboard，应选择覆盖已有 Dashboard，或先删除旧 Dashboard
   再导入；仅替换本地 JSON 不会更新已经手工导入到 Grafana 数据库中的副本。

## 分布式多 Pod Dashboard 规则

当前 `sglang-dashboard.json` 已增加 `deployment`、`inference_group` 和 `instance` 变量，
并把三者写入所有 SGLang PromQL。变量支持 All 和多选；多组并行压测时应先选择目标
deployment/group，再选择该组 frontend target，避免把无关组的 gauge 或延迟直方图混合。

Dashboard v14 默认选择三节点 GLM5.2 的
`deployment=glm52-sglang-3node`、`inference_group=glm52-sglang-3node-group-0`、
`instance=glm52-sglang-3node.default.svc.cluster.local:30000` 和 `model_name=glm-5.2`。
这些默认值与 [`prometheus.yml`](../prometheus/prometheus.yml) 一致；Prometheus 只抓取 rank 0/master，
因此应用请求 gauge 和直方图不会把 rank 1/2 重复求和。若切回其他部署，可在变量中选择
`All` 或对应标签值，无需改 panel PromQL。

分布式日志仍在 Explore 中通过 Loki 数据源查询，建议至少使用：

```logql
{job="sglang", deployment="glm52-sglang-3node", inference_group="glm52-sglang-3node-group-0"}
```

再按 `role`、`node_rank` 或 `pod` 缩小范围。Grafana 数据源地址不因 SGLang Pod 数量
改变；通过 `datasource` 变量选择实际 Prometheus 数据源，不再要求部署环境使用固定 UID。

## IB 网卡 Dashboard

`Distributed Inference IB Network` Dashboard 面向两节点
分布式推理压测，依赖 Prometheus job `prometheus-node-exporter-ib`。现场实际
IB-only 响应显示 node_exporter 版本为 0.17.0，且每个样本节点暴露
`mlx5_0`～`mlx5_9` 共 10 个 device。Dashboard v5 只查询实际存在的指标，提供：

- node-exporter target 与 `infiniband` collector 健康度；
- 根据 RX counter label 计算的可见 device 和 device/port series 数；
- 每个 device/port 的 RX/TX bits/s 和单播、组播 packets/s；
- link-down、link-error-recovery 事件率和 IB collector 采集耗时。

0.17.0 返回中不存在 `node_infiniband_info`、`state_id`、
`physical_state_id`、`rate_bytes_per_second`、`port_transmit_wait_total` 和新版完整
errors/discards 指标。因此 v5 不再展示无法验证的 ACTIVE 状态、利用率、
transmit-wait 和完整错误统计；不能用其他 counter 的值代替这些语义。

Dashboard 使用 `datasource`、`instance`、`device` 和 `port` 变量。Prometheus 会发现当前
无头 Service 的全部 Endpoint；正式压测时，必须在 `instance` 中只选择 SGLang 两个 Rank
实际所在节点对应的 `IP:9100`。完整的节点映射方法、指标含义和验收步骤参见
[IB 网卡监控配置说明](../ib-network-monitoring/README.md)。

Dashboard v2 还修复了 tooltip 有值但曲线不可见的问题：IB 时序面板改为稀疏数据自动
显示点、只连接 30 秒以内的 null、使用 2 px 线宽，并在每条 `rate()` 查询上设置与
Prometheus job 一致的 `Min step: 5s`。Grafana Prometheus 数据源的 `Scrape interval` 也
应设置为 `5s`；两者含义及 Query Inspector 验证步骤见说明文档第 5.1～5.2 节。

Dashboard v3 将 target 健康度从 `sum(up)` 改为 `min(up)`：只有所选 target
全部抓取成功才显示绿色，避免双节点中仅一台正常时仍误报健康。
`InfiniBand collector` 的 No data 表示 exporter 抓取失败、collector 状态未知；
不应把它解释为 collector 已返回失败。

Dashboard v4 为 node_exporter 0.17 兼容版：变量从 RX counter 的 labels 生成，
“可见设备”基线按实测设为 10，并用实际存在的单播/组播包率、链路事件和
exporter 版本取代无数据面板。10 是宿主机 sysfs 可见 device 数，不代表
SGLang/NCCL 一定使用全部设备；计划使用的 8 个 HCA 必须根据 `ibdev2netdev`、
GUID、`ibstat` 和 NCCL 日志建立映射后再筛选。

Dashboard v5 保留上述指标兼容性，但从 Dashboard 标题、标签、面板标题和说明中
移除版本/兼容模式信息；各面板只说明当前监测语义。原 exporter 版本面板改为
`InfiniBand collector scrape duration`，用于监测每个 target 的 IB collector 单次采集耗时。

## NVIDIA DCGM GPU Dashboard

`NVIDIA DCGM GPU Monitoring` Dashboard 使用 Prometheus job
`nvidia-dcgm-exporter`，对应现场的 `10.0.3.1:9400`、`10.0.3.2:9400` 和
`10.0.3.3:9400`。它与其他
Dashboard 一样通过 `datasource` 变量选择实际 Prometheus 数据源，并提供 `node`、`model`
和 `gpu` 多选变量。

Dashboard 展示 target health、可见 GPU 数量、核心与显存利用率、温度、功耗、时钟、
graphics/tensor/DRAM activity、PCIe RX/TX bytes/s，以及 XID、row remap 和 PCIe replay
状态。DCGM profiling 字段按 0–1 ratio 乘以 100 展示；`DCGM_FI_PROF_PCIE_*_BYTES`
本身已经是 bytes/s gauge，不再使用 `rate()`。完整接入、PromQL 验证和常见问题参见
[GPU 监控配置说明](../gpu-monitoring/README.md)。
