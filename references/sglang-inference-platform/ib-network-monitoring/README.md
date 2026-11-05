# 两节点分布式推理的 IB 网卡监控配置

> 本文及配套文件来自 `sglang-inference-platform`，作为压测链路搭建参考。
> 来源版本、适用范围和配置适配说明见[参考配置索引](../README.md)。

本目录说明如何复用集群中现有的 `prometheus-node-exporter`，监控两节点
TP=2 分布式推理的 InfiniBand 流量和基础链路事件。现场 exporter 无法升级，
实际返回的版本为 `node_exporter 0.17.0`，因此当前 Dashboard 和告警只使用
该端点真实暴露的指标，不再查询新版 exporter 才有的端口状态和速率指标。

| 文件 | 作用 |
|---|---|
| [`../prometheus/prometheus-distributed-example.yml`](../prometheus/prometheus-distributed-example.yml) | 通过无头 Service 发现 Endpoint，并只请求 `infiniband` collector。 |
| [`../prometheus/rules/ib-network-alerts.yml`](../prometheus/rules/ib-network-alerts.yml) | 检查 target、collector、可见设备数、link-down 和 link-error-recovery。 |
| [`../grafana/dashboards/ib-network-dashboard.json`](../grafana/dashboards/ib-network-dashboard.json) | 展示实际可用的收发吞吐、单播/组播包率、链路事件及 exporter 版本。 |

现有 Service 与 Endpoint 为：

```text
namespace: ampha-business
service:   prometheus-node-exporter (headless, ClusterIP=None)
port:      9100
endpoints: 10.0.3.1:9100, 10.0.3.2:9100, 10.0.3.3:9100
```

Prometheus 会采集 3 个 Endpoint。正式压测时，在 Dashboard 的 `node exporter
instance` 变量中只选择实际承载 rank 0 和 rank 1 的两个节点。

## 1. 实际指标集与能力边界

上传的 IB-only 响应已确认下列指标存在：

| 指标 | 当前用途 |
|---|---|
| `node_exporter_build_info` | 确认 exporter 版本；实测为 `0.17.0`。 |
| `node_scrape_collector_success{collector="infiniband"}` | 确认 IB collector 本轮是否成功。 |
| `node_scrape_collector_duration_seconds{collector="infiniband"}` | 观察 collector 本身耗时。 |
| `node_infiniband_port_data_received_bytes_total` | 端口累计接收字节；`rate() * 8` 转为 bit/s。 |
| `node_infiniband_port_data_transmitted_bytes_total` | 端口累计发送字节；`rate() * 8` 转为 bit/s。 |
| `node_infiniband_unicast_packets_{received,transmitted}_total` | 单播收发包 counter，Dashboard 以 packets/s 展示。 |
| `node_infiniband_multicast_packets_{received,transmitted}_total` | 组播收发包 counter，Dashboard 以 packets/s 展示。 |
| `node_infiniband_link_downed_total` | 链路进入 down 的累计次数。 |
| `node_infiniband_link_error_recovery_total` | 链路错误恢复的累计次数。 |

实际响应中没有下列新版指标：

```text
node_infiniband_info
node_infiniband_state_id
node_infiniband_physical_state_id
node_infiniband_rate_bytes_per_second
node_infiniband_port_transmit_wait_total
node_infiniband_port_*errors*_total / *discards*_total
```

因此，当前不能通过 Prometheus 判定端口是否 `ACTIVE`，不能计算相对额定速率的
利用率，也不能展示 transmit-wait 或完整的 errors/discards。这些面板之前显示
`No data` 不是 Grafana 渲染错误，而是 PromQL 查询了端点根本没有的 time series。
配置中没有用其他 counter 伪造这些语义。

不要用 `node_network_receive_bytes_total`/`node_network_transmit_bytes_total` 代替 IB
counter。NCCL 通过 verbs/RDMA 直接使用 HCA 时，Linux netdev 字节数可能不反映真实
RDMA 数据量。同时，IB counter 是宿主机级指标，不是 SGLang Pod 专属指标；
需结合 NCCL 日志和压测时间窗口归因。

## 2. 确认两个推理节点

先记录两个 Rank 落点：

```shell
kubectl get pod -n default \
  -l app=sglang-ib-2node \
  -o custom-columns='POD:.metadata.name,NODE:.spec.nodeName,POD_IP:.status.podIP' \
  --sort-by=.metadata.name
```

再查看 node-exporter Endpoint IP 与节点名的对应关系：

```shell
kubectl get endpoints -n ampha-business prometheus-node-exporter \
  -o jsonpath='{range .subsets[*].addresses[*]}{.ip}{"\t"}{.nodeName}{"\t"}{.targetRef.name}{"\n"}{end}'
```

按 `NODE` 对齐后记下两个推理节点对应的 `IP:9100`。不要仅按
`10.0.3.1/2/3` 的数字顺序猜节点身份。

## 3. 验证 node-exporter 实际输出

从与 Prometheus 相同的网络路径请求每个 Endpoint：

```shell
curl --silent --show-error --fail \
  'http://10.0.3.1:9100/metrics?collect[]=infiniband' \
  | grep -E '^node_exporter_build_info|^node_scrape_collector_(success|duration_seconds).*infiniband|^node_infiniband_(port_data_(received|transmitted)_bytes_total|link_(downed|error_recovery)_total)'
```

将 IP 依次替换为 `10.0.3.2` 和 `10.0.3.3`。当前上传样本的预期结果为：

```text
node_exporter_build_info{...,version="0.17.0"} 1
node_scrape_collector_success{collector="infiniband"} 1
node_scrape_collector_duration_seconds{collector="infiniband"} 0.015852107
每个可见 device/port 都有 RX/TX 字节 counter
```

保存响应后核对设备和端口：

```shell
grep '^node_infiniband_port_data_received_bytes_total{' node-exporter.metrics \
  | sed -n 's/.*device="\([^"]*\)".*/\1/p' \
  | sort -u

grep '^node_infiniband_port_data_received_bytes_total{' node-exporter.metrics \
  | sed -n 's/.*device="\([^"]*\)".*port="\([^"]*\)".*/\1 port \2/p' \
  | sort -u
```

上传样本实际返回 `mlx5_0` 至 `mlx5_9`，共 **10 个** device，每个都是 port 1。
这与“推理计划使用 8 张 IB 网卡”不矛盾：exporter 统计宿主机 sysfs 可见的所有
IB device，其中可能包括未用于本次 NCCL 数据面的管理、存储或其他 HCA。
不能仅根据 exporter 的 device 数量猜测哪 8 个被 SGLang 使用。

需在每个推理节点上建立真实映射：

```shell
ibdev2netdev
for dev in /sys/class/infiniband/*; do
  printf '%s: ' "$(basename "$dev")"
  cat "$dev/node_guid"
done
ibstat
```

再将结果与 SGLang/NCCL 日志中的 HCA 选择、`NCCL_IB_HCA`、RDMA 设备分配和
GID 对照。建立映射后，在 Grafana `IB device` 变量中只选择那 8 个设备。

### 3.1 collector 成功但没有 IB counter

检查 DaemonSet 参数和宿主机 sysfs 挂载，不要再启动一套重复 exporter：

```shell
kubectl get daemonset -n ampha-business -o name | grep node-exporter
kubectl get daemonset -n ampha-business '<daemonset-name>' -o yaml
kubectl logs -n ampha-business '<node-exporter-pod>' | grep -i infiniband
```

确认没有通过 `--no-collector.infiniband` 或 `--collector.disable-defaults` 关闭 collector，并确认
exporter 能读取宿主机 `/sys/class/infiniband`。如果宿主机根目录挂载为
`/host/root`，`--path.rootfs` 需与该布局一致。

## 4. Prometheus 部署与验收

将下列文件保持相对层级同步到 Prometheus 挂载目录：

```text
/private/benchmark-data/prometheus/config/prometheus-distributed-example.yml
/private/benchmark-data/prometheus/config/rules/ib-network-alerts.yml
```

专用 job 的关键配置是：

- `dns_sd_configs` 把无头 Service 的每个 A 记录建成独立 target。
- `scrape_interval: 5s` 和 `scrape_timeout: 3s`。
- `params.collect[]` 只运行 `infiniband`，与已验证的 IB-only 请求保持一致并避免完整 `/metrics` 超时。
- `metric_relabel_configs` 只将 IB、collector health/duration 和 build info 入库。

先检查再重载：

```shell
promtool check config /private/benchmark-data/prometheus/config/prometheus-distributed-example.yml
promtool check rules /private/benchmark-data/prometheus/config/rules/ib-network-alerts.yml
curl --fail --show-error -X POST http://127.0.0.1:9090/-/reload
```

在 `/targets` 确认 3 个 target 持续 `UP`，然后执行：

```promql
node_exporter_build_info{job="prometheus-node-exporter-ib"}
```

```promql
node_scrape_collector_success{job="prometheus-node-exporter-ib",collector="infiniband"}
```

```promql
count by (instance) (
  count by (instance, device) (
    node_infiniband_port_data_received_bytes_total{job="prometheus-node-exporter-ib"}
  )
)
```

```promql
count by (instance) (
  node_infiniband_port_data_received_bytes_total{job="prometheus-node-exporter-ib"}
)
```

```promql
rate(node_infiniband_port_data_received_bytes_total{job="prometheus-node-exporter-ib"}[1m]) * 8
```

前两个计数查询分别表示“有字节 counter 的唯一 device 数”和“device/port
counter 序列数”，**都不表示端口 ACTIVE**。根据上传样本，单节点基线为 10。

### 4.1 为什么必须保留 `collect[]`

现场完整 `/metrics` 首字节需 2.411～5.515 秒，总耗时 2.419～5.720 秒，
响应约 365 KB；仅请求 `infiniband` 时总耗时约 0.203 秒，响应约 14.8 KB。
完整端点会超过 3 秒 `scrape_timeout`，形成：

```text
Prometheus context deadline exceeded
  -> Prometheus 关闭连接
  -> node-exporter 继续写入时报 broken pipe
  -> up=0，Grafana 时序出现缺口或 No data
```

`metric_relabel_configs` 在 Prometheus 收完整个响应后才过滤，不能替代请求级
`collect[]`。如果 IB-only 端点仍超时，再检查 exporter CPU limit、sysfs、驱动和
Prometheus 到 Endpoint 的网络路径。

## 5. Grafana 使用说明

将 `ib-network-dashboard.json` 放入：

```text
/private/benchmark-data/grafana/dashboards/ib-network-dashboard.json
```

provider 最多 30 秒后会加载
`Distributed Inference IB Network`。当前为 Dashboard v5，
保持 UID `distributed-inference-ib-network`。如果是手工
导入的旧副本，需选择覆盖相同 UID；仅替换本地 JSON 不会修改 Grafana 数据库中
手工导入的 Dashboard。

使用步骤：

1. 选择实际 Prometheus 数据源。
2. `node exporter instance` 只选择两个推理节点。
3. 首先确认 target health、collector health 和 collector scrape duration 面板正常。
4. 根据第 3 节的宿主机/NCCL 映射，在 `IB device` 中选择本次数据面的 8 个
   device；未建立映射前保持 `All`，但不得将所有 10 个都归因为 SGLang。
5. 压测期间观察 RX/TX bit/s、单播/组播 packets/s 和 link 事件。

v5 面板说明：

| Dashboard 面板 | 语义 |
|---|---|
| `Visible IB devices per target (expected: 10)` | 根据 RX counter 的 `device` label 去重计数；不受 device 变量影响，始终检查整机基线。 |
| `Exported IB ports per target` | RX counter 的 device/port series 数，不是 ACTIVE 端口数；不受 device/port 变量影响。 |
| `IB receive/transmit throughput` | 字节 counter 的变化率，单位 bit/s。 |
| `IB unicast packet rate` | 每个 device/port 的单播收发包率。 |
| `IB link events per minute` | 每个 device/port 每分钟新增的 link-down 和 link-error-recovery 事件数。 |
| `InfiniBand collector scrape duration` | 每个 target 的 IB collector 单次采集耗时。 |
| `IB multicast packet rate` | 每个 device/port 的组播收发包率。 |

### 5.1 Tooltip 有值但曲线不可见

Dashboard 仍保留之前的稀疏数据渲染修复：`Show points: Auto`、30 秒短缺口连线、
2 px 线宽、4 px 点和 `Min step: 5s`。长于 30 秒的缺口不会强制连线，以免
伪造连续流量。

如果仍然只看到孤立点，在 Prometheus 中查看最近 1 分钟原始样本数：

```promql
count_over_time(node_infiniband_port_data_received_bytes_total{job="prometheus-node-exporter-ib",instance="10.0.3.1:9100"}[1m])
```

稳定的 5 秒采集通常接近 12 个样本。若只有 0～1 个，需查看 `/targets`、DNS、
scrape duration 和 exporter 日志，不能通过无限连接 null 掩盖。

## 6. 告警规则

| 告警 | 触发条件 | 级别 |
|---|---|---|
| `NodeExporterIBTargetDown` | target 连续 1 分钟不可采集。 | critical |
| `NodeExporterIBScrapeSlow` | 成功抓取持续 2 分钟超过 2 秒。 | warning |
| `NodeExporterIBCollectorFailed` | `infiniband` collector 连续 2 分钟失败，或 target 为 UP 但 collector health 序列缺失。 | critical |
| `NodeExporterIBMetricsMissing` | target 为 UP，但连续 2 分钟没有 RX counter。 | critical |
| `NodeExporterIBDeviceCountUnexpected` | 可见唯一 device 数连续 5 分钟不等于实测基线 10。 | warning |
| `NodeExporterIBLinkWentDown` | 5 分钟内 link-down counter 增加。 | critical |
| `NodeExporterIBLinkErrorRecovery` | 5 分钟内 link-error-recovery counter 增加。 | warning |

`DeviceCountUnexpected` 监控的是宿主机 exporter 可见基线，不是“本次 NCCL 必须使用
10 张网卡”。如果三个 Endpoint 硬件规格不同，应将 IB job/Service 限制到同规格
节点，或把规则改为按稳定节点标签设置不同基线。Grafana 变量不会限制 Prometheus 告警。

## 7. 压测验收清单

压测前：

- 两个 SGLang Pod 位于不同节点，已记录 Pod、node、node-exporter instance 映射。
- 两个 target `up=1`，`infiniband` collector success=1，exporter 版本符合预期。
- 两个节点的可见 device 基线已确认；实测为 10，已通过宿主机和 NCCL 日志确认
  其中本次使用的 8 个。
- 由于 exporter 0.17 不暴露 port state，已通过 `ibstat` 或
  `/sys/class/infiniband/*/ports/*/state` 确认在用端口 ACTIVE。
- 已记录 link-down 和 link-error-recovery counter 起始值。

压测中：

- 两节点目标 HCA 的 RX/TX counter 增长，吞吐曲线与压测时间窗口一致。
- 没有新的 link-down/link-error-recovery 事件。
- 如果仅有部分 HCA 有流量，检查 NCCL 日志、`NCCL_IB_HCA`、RDMA 设备分配和路由；
  不能单凭 0 流量认定端口 down。

压测后：

- 保留 Dashboard 时间范围/快照、PromQL 原始结果、两 Rank NCCL 日志和压测 JSON。
- 对比压测前后 counter，而不是只看当前累计绝对值。
- 记录 exporter 版本、节点名、可见 device 与实际数据面 HCA 映射。

## 8. 常见问题

### target 为 UP，为什么部分面板仍然 No data

先查询面板 PromQL 使用的指标名，再与 IB-only `/metrics` 的 `# HELP`/`# TYPE`
列表对比。现场 0.17 端点不存在 `info` / `state_id` / `rate_bytes_per_second` /
`transmit_wait` / 新版 errors/discards，因此旧 Dashboard 的相关查询返回空向量。
加载 v4 JSON 后，所有 `node_*` 面板指标都存在于上传的实际响应中。

### target 反复 DOWN，node-exporter 日志出现 broken pipe

若 Targets 显示 `context deadline exceeded` 且耗时接近 3 秒，`broken pipe` 是
Prometheus 超时关闭连接后的次生错误。确认当前 job 已发送 `collect[]=infiniband`；
完整 `/metrics` 在现场已证实会超时。

### 为什么可见 10 个 device，但业务计划使用 8 张 IB 网卡

10 是 sysfs/exporter 可见 device 数，8 是本次推理数据面计划值，两者口径不同。
使用 `ibdev2netdev`、GUID、`ibstat`、NCCL 日志和 `NCCL_IB_HCA` 建立映射后，
再在 Dashboard 中筛选 8 个实际设备。

### 如何判定端口是否 ACTIVE

当前 exporter 无此指标，使用宿主机 `ibstat` 或直接读取：

```shell
for state in /sys/class/infiniband/*/ports/*/state; do
  printf '%s: ' "$state"
  cat "$state"
done
```

若必须把状态纳入 Prometheus，需要另行评估 textfile collector 或专用 IB exporter，
这属于新的部署和运维边界，当前修改未自动引入。

### 有吞吐，但不能确认 NCCL 使用了 IB

宿主机 counter 可能包含其他工作负载。确认节点独占，并同时检查两个 Rank 日志中
的 `NET/IB`、`GDRDMA` 和实际 HCA 选择证据。

## 9. 参考资料

- [Prometheus node_exporter collectors](https://github.com/prometheus/node_exporter#collectors)
- [node_exporter changelog](https://github.com/prometheus/node_exporter/blob/master/CHANGELOG.md)
- [node_exporter 0.17.0 InfiniBand collector implementation](https://github.com/prometheus/node_exporter/blob/v0.17.0/collector/infiniband_linux.go)
- [Prometheus DNS service discovery configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#dns_sd_config)
