# NVIDIA DCGM GPU 监控配置说明

> 本文及配套文件来自 `sglang-inference-platform`，作为压测链路搭建参考。
> 来源版本、适用范围和配置适配说明见[参考配置索引](../README.md)。

本文说明如何把三个 GPU 节点上已有的 `nvidia-dcgm-exporter` 接入当前
Prometheus/Grafana 链路。配置基于现场返回的三个端点：

| 节点 | DCGM Exporter 地址 | 现场 GPU |
|---|---|---|
| `jbsw-gpu01` | `http://10.0.3.1:9400/metrics` | 8 × NVIDIA H200 |
| `jbsw-gpu02` | `http://10.0.3.2:9400/metrics` | 8 × NVIDIA H200 |
| `jbsw-gpu03` | `http://10.0.3.3:9400/metrics` | 8 × NVIDIA H200 |

现场响应中的原生标签包含 `Hostname`、`gpu`、`UUID`、`device`、`modelName`、
`DCGM_FI_DRIVER_VERSION`、`namespace`、`pod` 和 `container`。Prometheus 配置另外为每个
target 附加小写 `node` 标签，使 exporter 不可达时生成的 `up` 指标仍能映射到稳定节点名。
`gpu` 只在单个节点内唯一，跨节点定位一张卡时应至少使用 `node + gpu`，需要全局唯一标识时
使用 `UUID`。

## 1. 数据链路

```text
nvidia-dcgm-exporter
  ├─ 10.0.3.1:9400/metrics (jbsw-gpu01)
  ├─ 10.0.3.2:9400/metrics (jbsw-gpu02)
  └─ 10.0.3.3:9400/metrics (jbsw-gpu03)
                 │ Prometheus 每 5 秒 pull
                 ▼
       job="nvidia-dcgm-exporter"
                 │ PromQL
                 ▼
       NVIDIA DCGM GPU Monitoring
```

相关仓库文件：

- 单机 Prometheus 配置：[prometheus.yml](../prometheus/prometheus.yml)
- 分布式 Prometheus 配置：
  [prometheus-distributed-example.yml](../prometheus/prometheus-distributed-example.yml)
- Grafana Dashboard：
  [nvidia-dcgm-gpu-dashboard.json](../grafana/dashboards/nvidia-dcgm-gpu-dashboard.json)
- Dashboard provider：[sglang.yml](../grafana/dashboards/sglang.yml)

两份 Prometheus 配置都使用相同的 `job_name: nvidia-dcgm-exporter`、`5s`
`scrape_interval` 和 `3s` `scrape_timeout`。三个 target 分开声明，以便分别附加
`node=jbsw-gpu01`、`node=jbsw-gpu02` 和 `node=jbsw-gpu03`。这些地址是静态节点 IP；
如果节点地址发生变化，必须同步修改配置并 reload Prometheus。

## 2. Dashboard 监测项

Dashboard UID 为 `nvidia-dcgm-gpu-monitoring`，默认展示最近 30 分钟并每 5 秒刷新。顶部
变量支持选择 Prometheus 数据源、GPU 节点、GPU 型号和 GPU index，均支持多选和 `All`。

| Dashboard 监测项 | Prometheus 指标/计算 | 口径 |
|---|---|---|
| DCGM Exporter target health | `min(up{job="nvidia-dcgm-exporter"})` | 所选 target 全部为 `1` 才显示 UP。 |
| Visible GPUs | `count(DCGM_FI_DEV_GPU_UTIL)` | 当前有利用率样本的 GPU 数；三个节点全选时现场预期为 24。 |
| GPU utilization | `DCGM_FI_DEV_GPU_UTIL` | GPU 核心利用率，单位 `%`。 |
| GPU memory-copy utilization | `DCGM_FI_DEV_MEM_COPY_UTIL` | 显存复制引擎利用率，单位 `%`。 |
| Framebuffer utilization | `100 * FB_USED / (FB_USED + FB_FREE)` | 由 MiB gauge 计算每卡和总体显存使用率。 |
| GPU and memory temperature | `DCGM_FI_DEV_GPU_TEMP`、`DCGM_FI_DEV_MEMORY_TEMP` | 核心与显存温度，单位 `°C`。 |
| GPU power usage | `DCGM_FI_DEV_POWER_USAGE` | 每卡当前功耗，单位 `W`。 |
| GPU clocks | `DCGM_FI_DEV_SM_CLOCK`、`DCGM_FI_DEV_MEM_CLOCK` | SM 与显存时钟，单位 `MHz`。 |
| GPU engine activity | `DCGM_FI_PROF_GR_ENGINE_ACTIVE`、`PIPE_TENSOR_ACTIVE`、`DRAM_ACTIVE` | DCGM profiling 原值为 `0–1` 的比例，Dashboard 乘以 100 后展示为 `%`。 |
| PCIe throughput | `DCGM_FI_PROF_PCIE_RX_BYTES`、`TX_BYTES` | 指标本身已经是 bytes/s gauge，直接展示，不能再套 `rate()`。 |
| Last XID error | `max(DCGM_FI_DEV_XID_ERRORS)` | 最近一次 XID 错误码；现场正常值为 `0`。 |
| Row-remap status | `max(DCGM_FI_DEV_ROW_REMAP_FAILURE)` | `0` 为正常，`1` 表示 remap failure。 |
| Uncorrectable remapped rows | `sum(DCGM_FI_DEV_UNCORRECTABLE_REMAPPED_ROWS)` | 所选 GPU 的累计不可纠正 remapped rows。 |
| PCIe replay increase | `sum(increase(DCGM_FI_DEV_PCIE_REPLAY_COUNTER[5m]))` | 最近 5 分钟 PCIe retry 增量。 |

DCGM profiling 字段的比例语义和 PCIe bytes/s 口径参见
[NVIDIA DCGM profiling 文档](https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html)
及 [DCGM Exporter 指标说明](https://docs.nvidia.com/datacenter/dcgm/latest/reference/dcgm-exporter-metrics.html)。

温度面板中的 `75°C`/`85°C` 和显存面板中的 `80%`/`95%` 只是 Dashboard 颜色提示，不是
Prometheus 告警，也不能替代型号对应的 NVIDIA 降频/关断阈值。正式运行前应按 H200、
机房散热和业务余量要求校准；本次改动没有新增 Alertmanager 或 GPU 告警规则。

附件中还有总能耗、编码/解码器、NVLink、vGPU license 和 correctable remapped rows 等
指标，均会被 Prometheus 原样保存。它们没有全部放入主 Dashboard，仍可在 Grafana Explore
中按需查询，避免主页面被推理压测不常用的面板淹没。

## 3. 部署与 reload

### 3.1 同步 Prometheus 配置

根据实际启动方式选择一份配置，不要同时把两份当作主配置：

```text
/private/benchmark-data/prometheus/config/prometheus.yml
# 或
/private/benchmark-data/prometheus/config/prometheus-distributed-example.yml
```

先在 Prometheus 容器中检查语法，再 reload：

```shell
/bin/promtool check config /private/benchmark-data/prometheus/config/prometheus.yml
curl -fsS -X POST http://10.0.3.253:33334/-/reload
```

若实际使用分布式配置，把 `promtool` 参数改为
`prometheus-distributed-example.yml`。当前启动命令已启用 `--web.enable-lifecycle`，因此
可以调用 `/-/reload`；若 lifecycle 端点受网络策略限制，则重启 Prometheus Pod。

### 3.2 同步 Grafana Dashboard

把以下两个文件同步到 Grafana 挂载目录：

```text
/private/benchmark-data/grafana/dashboards/nvidia-dcgm-gpu-dashboard.json
/private/benchmark-data/grafana/dashboards/sglang.yml
```

`sglang.yml` 的 provider 会扫描 `.../grafana/dashboards` 下的全部 JSON，每 30 秒检查一次，
因此无需为 GPU Dashboard 再创建 provider。Grafana 中的 Prometheus 数据源 URL 仍为：

```text
http://10.0.3.253:33334
```

仓库没有提交环境专属的数据源 provisioning。首次打开 Dashboard 时，在顶部
`Prometheus data source` 中选择已经可用的数据源；如果同 UID Dashboard 已经手工导入过
旧副本，应覆盖旧副本或等待 provisioning 更新。

## 4. 验证

### 4.1 Exporter 端点

从 Prometheus 所在网络验证三个端点，而不是只从 GPU 节点本机验证：

```shell
curl -fsS http://10.0.3.1:9400/metrics | grep -m1 DCGM_FI_DEV_GPU_UTIL
curl -fsS http://10.0.3.2:9400/metrics | grep -m1 DCGM_FI_DEV_GPU_UTIL
curl -fsS http://10.0.3.3:9400/metrics | grep -m1 DCGM_FI_DEV_GPU_UTIL
```

### 4.2 Prometheus

依次执行以下 PromQL：

```promql
up{job="nvidia-dcgm-exporter"}
```

预期三个 target 都为 `1`。

```promql
count by (node) (
  DCGM_FI_DEV_GPU_UTIL{job="nvidia-dcgm-exporter"}
)
```

按附件现场数据，预期 `jbsw-gpu01`、`jbsw-gpu02` 和 `jbsw-gpu03` 各为 `8`。

```promql
count by (node, Hostname, modelName, DCGM_FI_DRIVER_VERSION) (
  DCGM_FI_DEV_GPU_UTIL{job="nvidia-dcgm-exporter"}
)
```

该查询同时验证 Prometheus 附加的 `node` 与 Exporter 原生的主机、型号、驱动标签没有丢失。

### 4.3 Grafana

1. 打开 `SGLang/NVIDIA DCGM GPU Monitoring`。
2. 选择实际 Prometheus 数据源，并先保持节点、型号、GPU index 为 `All`。
3. 确认 target health 为 UP、Visible GPUs 为 24。
4. 运行一段推理压测，确认 GPU utilization、power、temperature、engine activity 和 PCIe
   throughput 随负载变化。
5. 分别选择 `jbsw-gpu01`、`jbsw-gpu02` 和 `jbsw-gpu03`，确认 GPU 0–7 的图例不会跨节点混淆。

## 5. 常见问题

- **三个 target 都 DOWN**：从 Prometheus Pod 内检查到 `10.0.3.1/2/3:9400` 的路由、防火墙
  和 NetworkPolicy；不要因为 GPU 节点本机 `curl` 成功就认定 Prometheus 一定可达。
- **Dashboard 变量为空**：先在 Prometheus 查询
  `DCGM_FI_DEV_GPU_UTIL{job="nvidia-dcgm-exporter"}`。若有数据，再确认 Dashboard 顶部选择
  了正确的数据源，且 Prometheus 实际加载的是已经修改的配置文件。
- **target UP 但某个 GPU 无曲线**：查询该卡的 `UUID` 和 `gpu` 标签，检查 Exporter/DCGM
  是否仍能枚举该卡；`up=1` 只表示 HTTP scrape 成功，不保证每个预期字段和 GPU 都存在。
- **profiling 面板无数据**：`DCGM_FI_PROF_*` 取决于 DCGM Exporter 的 metrics CSV、GPU
  支持的 profiling metric group 和 DCGM 采集状态；不要用普通 GPU utilization 伪造替代。
- **Pod/namespace 标签为空**：附件中空值表示 GPU 当前未关联 Kubernetes workload；
  节点级 GPU 指标仍然有效。工作负载归属应以 Exporter 实际注入的 `pod`、`namespace`、
  `container` 标签为准。
- **静态 IP 变化**：修改三个 `targets`，保留对应稳定 `node` 标签，重新执行 `promtool`
  检查和 Prometheus reload。

9400 端口当前是无认证 HTTP 指标端点，应只暴露在 Prometheus 可达的可信网络中，不能直接
发布到不受控网络。
