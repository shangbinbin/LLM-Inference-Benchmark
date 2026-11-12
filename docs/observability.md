# 观测链路使用指南

本页说明独立配置渲染器及历史环境格式。新的容器启动/AISBench 入口请使用 [Docker 一键部署](docker-stack.md)和 `configs/stacks/`；它支持独立的多角色采集入口，不受本页旧环境格式的单 master 约定限制。

正式 `llm-bench` 链路的标签已统一为 task、instance_role/replica 和 gpu_node/gpu_model/gpu_index，Dashboard 合并为带 Logs Volume 的任务总览；详见 [任务观测配置](task-observability.md)。本页下面的 deployment/rank 标签和独立渲染命令仅描述历史参考格式。

## 生成配置

从仓库根目录执行：

```bash
python3 scripts/render_observability.py \
  --environment configs/environments/nvidia-h200-3node-8gpu/environment.json \
  --output generated/nvidia-h200-3node-8gpu
```

输出目录必须不存在，防止切换 GPU 后混入旧面板。修改配置后生成到新目录，审阅后再替换运行挂载。`generated/` 和 `results/` 不纳入 Git。生成过程不启动容器、不部署 Exporter、不修改推理服务。

H200 文件保留来源环境地址。新环境应复制环境目录并调整清单，禁止直接把来源地址当作实际部署地址。海光优先参照[官方监控接入指南](hygon-official-monitoring.md)使用 HCU v3 参考适配器；未补齐现场地址的环境仍只允许显式预览。

## 部署时的路径约定

| 生成目录/文件 | 容器内配置位置 | 数据与运行要求 |
| --- | --- | --- |
| `prometheus/` | `/etc/prometheus/` | `--config.file=/etc/prometheus/prometheus.yml`；TSDB 另挂持久卷 |
| `grafana/grafana.ini` | `/etc/grafana/grafana.ini` | 以此配置启动；管理员密码用 Secret/环境变量注入 |
| `grafana/provisioning/` | `/etc/grafana/provisioning/` | 自动创建 Prometheus/Loki 数据源和 Dashboard provider |
| `grafana/dashboards/` | `/etc/grafana/dashboards/` | 只加载所选 GPU 与可选网络面板 |
| `loki/loki.yaml` | `/etc/loki/loki.yaml` | `-config.file=/etc/loki/loki.yaml`；持久化 `/var/lib/loki` |
| `alloy/config.alloy` | `/etc/alloy/config.alloy` | `alloy run --storage.path=/var/lib/alloy /etc/alloy/config.alloy`；持久化 storage path |
| `logrotate/inference.conf` | `/etc/logrotate.d/inference` | 用定时任务调度，持久化 state，同一日志只由一个实例轮转 |

Grafana 还需持久化 `/var/lib/grafana` 并保证写权限。Alloy 必须能读取清单中所有 rank 的日志路径；当前组合按共享日志卷设计。若采用节点本地日志，应按节点部署采集器并裁剪 targets，避免多个采集器重复采集同一文件。logrotate 需要对日志有写权限。

来源中的 Pod YAML 是平台导出的运行快照，包含调度平台注解、UID、ownerReferences 和 status，仅留在档案中；不要直接用于部署新环境。独立渲染器不执行部署。容器启动器会按 stacks 配置选择镜像、挂载与 Exporter 参数，无需 Docker Compose；未来 Kubernetes 仍需按目标集群生成部署清单。

Loki 为来源的单实例本地存储模式，`auth_enabled: false`；仅适用于受控网络，未配置高可用和自动日志保留策略。logrotate 的 `maxage 7` 管理原始文件历史，不等于 Loki 保留周期。`copytruncate` 沿用来源策略，复制与截断期间存在少量日志丢失窗口。

## 指标与日志关联

- SGLang 请求指标仅抓 master 的单一入口，避免对同一分布式推理组重复聚合；该入口必须只选 rank 0。
- 指标和日志共享 `deployment`、`inference_group`、`model`、`topology`、`environment`、`gpu_vendor`、`gpu_model`。原生 SGLang `model_name` 不被改名；应用面板仍按其原生标签查询。
- GPU 抓取额外附加稳定 `node` 标签，保留厂商原生设备标识；NVIDIA 查询只存在于 NVIDIA 适配器中。
- 所有 rank 日志由环境清单生成，采集与轮转使用同一组具体文件路径。当前 pod 标签假定 StatefulSet 的 `<deployment>-<rank>` 命名，其他命名方式需要扩展清单/生成器。
- 当前生成器用于每环境独立的 Prometheus/Grafana 栈；Dashboard UID 固定，不应将多个环境的生成目录一起导入同一个 Grafana。共享栈需另行加入环境变量筛选和 UID 隔离。

## 验证

仓库内验证生成逻辑：

```bash
python3 -m unittest discover -s tests -v
```

在部署目标版本的组件镜像中，使用上述配置路径和挂载进一步检查：

```bash
promtool check config /etc/prometheus/prometheus.yml
alloy validate /etc/alloy/config.alloy
logrotate --debug /etc/logrotate.d/inference
```

随后确认 Prometheus Targets 全部 UP、SGLang 指标只统计一次、GPU 数与实际一致；GPU 温度/功耗/显存单位应与设备工具读数对照。确认 Grafana 自动加载正确厂商面板，网络关闭时没有 IB 面板或告警。为每个 rank 写入唯一测试日志，验证 Loki 可查到且标签正确；重启 Alloy 后检查 positions 续读，再检查轮转后的连续性。

历史渲染器经过生成和结构测试；新的容器链路另有 [Mac 模拟实测记录](reports/mac-container-smoke-2026-11-07.md)。尚未在 H200 或海光服务器部署验证，未来上传的 Exporter 仍需核对指标契约。
