# 仓库结构与迁移

| 目录 | 职责 |
| --- | --- |
| `configs/common/` | 与 GPU 无关的组件配置、日志模板和 SGLang 应用面板 |
| `configs/gpu/<厂商>/<型号或适配器>/` | 型号选择、厂商采集接口、指标口径和 GPU 面板 |
| `configs/network/` | 独立于 GPU 的可选网络采集、告警和面板 |
| `configs/environments/` | 实际节点、模型、拓扑、地址及日志路径 |
| `scripts/` | 配置组合工具，不包含压测执行器 |
| `tests/` | 组合完整性、GPU 隔离和未完成模板拦截测试 |
| `docs/` | 操作指南、方案与验证记录 |
| `references/` | 固定来源档案与其他项目测试场景 |
| `generated/` | 生成的完整组件配置，Git 忽略 |

## 2026-11-06 迁移

旧目录 `references/sglang-inference-platform/` 重命名为 `references/nvidia-h200-3node-8gpu/`，标明实际来源硬件与规模。其内部原始配置及运行快照保持不变，原始文件名保留用于溯源；新配置使用以下命名，不依赖档案参与生成。

| 来源文件（相对原平台目录） | 新的可组合配置 |
| --- | --- |
| `prometheus/prometheus*.yml` 的采集配置 | `common/prometheus/base.json` + GPU/network adapter + environment，由脚本组合 |
| `grafana/grafana.ini`、`dashboards/sglang.yml` | `common/grafana/grafana.ini`、`dashboard-provider.json` |
| `grafana/dashboards/sglang-dashboard.json` | `common/grafana/dashboards/sglang.json`，去掉模型、节点规模与默认选中值 |
| `grafana/dashboards/nvidia-dcgm-gpu-dashboard.json` | `gpu/nvidia/dcgm/grafana/gpu.json` |
| `grafana/dashboards/ib-network-dashboard.json` | `network/infiniband/grafana/network.json` |
| `prometheus/rules/ib-network-alerts.yml` | `network/infiniband/prometheus/alerts.yml`，去掉现场固定设备数告警 |
| `alloy/config*.alloy` | `common/alloy/config.alloy.tmpl` + environment 日志列表 |
| `logrotate/sglang*.conf` | `common/logrotate/inference.conf.tmpl` + 同一日志列表 |
| `loki/loki.yaml` | `common/loki/loki.yaml`，持久化路径改为 `/var/lib/loki` |
| 三节点地址、模型和 TP/PP | `environments/nvidia-h200-3node-8gpu/environment.json` |
| 平台 Pod 快照、镜像构建文件与现场说明 | 仅留来源档案，未作为可移植部署文件复用 |

上表新配置路径均相对于 `configs/`。更早的 2026-11-05 整理把 `configs/<组件>/` 移到旧平台参考目录；现在请从[配置入口](../configs/README.md)使用四层组合结构。

新增海光型号请按[海光说明](../configs/gpu/hygon/README.md)扩展，避免按型号复制整套日志与监控链路。数据集实体、测试输出和密码不放配置目录；运行输出放 `results/<run-id>/`，需版本化的精选报告放 `docs/reports/`。
