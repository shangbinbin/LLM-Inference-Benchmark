# NVIDIA H200 三节点八卡来源档案

本目录保留来源环境的历史快照（含单机、两节点辅助示例），仅供追溯，不作为新环境的配置入口。可组合配置和使用命令见 [configs](../../configs/README.md)。旧路径 `references/sglang-inference-platform/` 已迁至此处。下文为来源记录，文中的“本次整理”指 2026-11-05 的快照整理。

# 平台监控与日志配置参考

本目录收集从 [sglang-inference-platform](https://github.com/shangbinbin/sglang-inference-platform) 复制的配置及说明，用于后续搭建本项目的压测观测链路。

## 来源记录

| 项目 | 记录 |
| --- | --- |
| 上游目录 | [`sglang-inference-platform/config/`](https://github.com/shangbinbin/sglang-inference-platform/tree/46609ee6b3095e0851e612a5b5c587e7a78f280e/config) 中的 7 个组件目录 |
| 对照版本 | [`46609ee6b3095e0851e612a5b5c587e7a78f280e`](https://github.com/shangbinbin/sglang-inference-platform/commit/46609ee6b3095e0851e612a5b5c587e7a78f280e) |
| 整理日期 | 2026-11-05 |
| 本次整理范围 | 迁移原 `configs/` 下的 30 个文件；配置、Dashboard、Pod YAML 和 Dockerfile 内容保持不变；README 增加来源说明并修正引用 |

整理前的 30 个文件均与上述版本对应文件的 Git blob SHA 一致。此处是该批文件的参考副本，不会自动跟随上游更新，也没有复制上游的推理部署代码、控制器或完整压测工具。

## 按用途查找

| 链路环节 | 目录 | 内容 |
| --- | --- | --- |
| 指标采集与告警 | [prometheus/](prometheus/README.md) | SGLang、GPU、IB 指标采集配置，IB 告警规则，Pod 示例与 Dockerfile |
| GPU 监控接入 | [gpu-monitoring/](gpu-monitoring/README.md) | DCGM Exporter 接入、指标口径和验证说明；配置与面板分别在 Prometheus、Grafana 目录 |
| IB 网络监控接入 | [ib-network-monitoring/](ib-network-monitoring/README.md) | node_exporter InfiniBand 采集、告警、面板和现场验证说明 |
| 指标与日志展示 | [grafana/](grafana/README.md) | SGLang、GPU、IB Dashboard，provider、Grafana 配置、Pod 示例与 Dockerfile |
| 日志采集 | [alloy/](alloy/README.md) | 单机、两节点、三节点日志采集配置及 Pod 示例 |
| 日志存储与查询 | [loki/](loki/README.md) | Loki 配置、Pod 示例与 Dockerfile |
| 日志轮转 | [logrotate/](logrotate/README.md) | 单机、两节点、三节点日志轮转配置及 Pod 示例 |

`gpu-monitoring/` 与 `ib-network-monitoring/` 当前只包含接入说明，不包含独立 Exporter 部署清单。三份 Dashboard 集中在 `grafana/dashboards/`，告警规则集中在 `prometheus/rules/`。

## 使用前的适配说明

- 各组件 README 中的“当前环境”“现场”“三节点”等描述均指来源项目的部署背景；单机、两节点、GLM5.2 三节点示例的适用范围不同。
- `/private/benchmark-data/...` 等绝对路径是容器或共享存储中的运行路径，与本仓库目录不同。迁移仓库文件不会部署组件或移动运行数据。
- 节点 IP、Service、命名空间、镜像地址、账号示例、PVC、指标标签及日志前缀需要按实际环境确认，并保持采集、告警和面板之间一致。
- 将选用配置复制并适配到本项目的 [configs/](../../configs/README.md)，再按组件要求检查和部署；这里的副本不表示已经在本项目环境验证。

## 未复制的上游依赖

| 参考内容 | 来源位置 |
| --- | --- |
| logrotate 镜像构建与入口脚本 | [Dockerfile](https://github.com/shangbinbin/sglang-inference-platform/blob/46609ee6b3095e0851e612a5b5c587e7a78f280e/logrotate/logrotate-dockerfile/Dockerfile)、[docker-entrypoint.sh](https://github.com/shangbinbin/sglang-inference-platform/blob/46609ee6b3095e0851e612a5b5c587e7a78f280e/logrotate/logrotate-dockerfile/docker-entrypoint.sh) |
| 三节点部署 profile 与日志前缀 | [glm52-3node-8gpu.json](https://github.com/shangbinbin/sglang-inference-platform/blob/46609ee6b3095e0851e612a5b5c587e7a78f280e/sglang-deployment/profiles/glm52-3node-8gpu.json) |
| SGLang 部署、日志生成和脚本 | [sglang-deployment/](https://github.com/shangbinbin/sglang-inference-platform/tree/46609ee6b3095e0851e612a5b5c587e7a78f280e/sglang-deployment) |

上述链接固定到对照版本，便于理解原配置的上下文。返回[参考资料索引](../README.md)或查看[目录迁移表](../../docs/repository-layout.md)。
