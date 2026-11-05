# 仓库结构与文件存放约定

## 当前划分

本项目目前处于参考配置与测试方案整理阶段。目录按用途划分，并区分已经收集的参考材料与后续适配的执行配置。

| 目录或文件 | 职责 | 当前状态 |
| --- | --- | --- |
| [README.md](../README.md) | 项目入口与导航 | 已提供 |
| `docs/` | 本项目方案、结构、指标口径和使用说明 | 当前包含本结构说明 |
| [configs/](../configs/README.md) | 本项目实际使用的工作负载、部署和观测配置 | 当前仅定义存放约定 |
| [references/sglang-inference-platform/](../references/sglang-inference-platform/README.md) | 来自已有平台的监控与日志配置参考 | 保留 7 个组件目录及其配套文件 |
| [references/benchmark-scenarios/](../references/benchmark-scenarios/README.md) | 其他项目的压测场景与测试项参考 | 已收录一份核对修订版清单 |

配置参考按来源集中管理，组件内部仍保留 README、配置、Pod 示例及 Dockerfile，便于对照来源版本。GPU/IB 说明与 Prometheus、Grafana 的兄弟目录关系保持不变。

## 2026-11-05 路径迁移

| 原路径 | 新路径 |
| --- | --- |
| `readme.md` | [README.md](../README.md) |
| `configs/alloy/` | [references/sglang-inference-platform/alloy/](../references/sglang-inference-platform/alloy/README.md) |
| `configs/gpu-monitoring/` | [references/sglang-inference-platform/gpu-monitoring/](../references/sglang-inference-platform/gpu-monitoring/README.md) |
| `configs/grafana/` | [references/sglang-inference-platform/grafana/](../references/sglang-inference-platform/grafana/README.md) |
| `configs/ib-network-monitoring/` | [references/sglang-inference-platform/ib-network-monitoring/](../references/sglang-inference-platform/ib-network-monitoring/README.md) |
| `configs/logrotate/` | [references/sglang-inference-platform/logrotate/](../references/sglang-inference-platform/logrotate/README.md) |
| `configs/loki/` | [references/sglang-inference-platform/loki/](../references/sglang-inference-platform/loki/README.md) |
| `configs/prometheus/` | [references/sglang-inference-platform/prometheus/](../references/sglang-inference-platform/prometheus/README.md) |
| `configs/examples/推理测试场景与测试项清单.md` | [references/benchmark-scenarios/推理测试场景与测试项清单.md](../references/benchmark-scenarios/推理测试场景与测试项清单.md) |

本次只整理仓库路径、导航与说明。YAML、JSON、Alloy、INI、conf、Dockerfile 和场景清单的内容保持不变，文档中的配置参数也未调整。logrotate 原来指向未复制文件的两处相对链接已改为上游固定版本链接；三节点 profile 的引用也指向上游对应文件。

原环境的容器路径和存储挂载路径保持原样。外部脚本如果曾从本仓库的旧 `configs/<组件>/` 路径复制文件，应按上表更新其仓库源路径。

## 后续新增文件放在哪里

只在有实际内容时创建子目录，以下尚未落地的路径是规划约定：

| 新增内容 | 建议位置 |
| --- | --- |
| 测试方案、SLO 定义、指标解释、操作指南 | `docs/` 下按主题命名的 Markdown 文件 |
| chat、rag、reasoning、agent 等场景的客户端配置 | `configs/workloads/<场景>/` |
| vLLM、SGLang 的部署配置 | `configs/deployments/<引擎>/` |
| 本项目使用的采集、日志、告警和面板配置 | `configs/observability/<组件>/` |
| 准备数据、发压、采集、汇总等执行脚本 | `scripts/`，在实现时按职责细分 |
| 数据集说明、版本、获取方式与小规模样例 | `datasets/`；大型数据文件另行存储 |
| 运行原始输出、日志、指标快照和临时报告 | `results/<run-id>/`；创建运行流程时加入忽略规则，默认不纳入 Git |
| 需要版本化保存的精选报告 | `docs/reports/` |
| 其他已有平台的配置参考 | `references/<来源项目>/`，同时记录来源版本和适用范围 |
| 其他项目的压测场景与测试要求 | `references/benchmark-scenarios/`，材料增多后按项目拆分子目录 |

文件名优先采用含义明确的英文短横线命名；现有中文场景清单保留原名。配置与运行输出分开保存，敏感参数通过环境变量或 Secret 注入。
