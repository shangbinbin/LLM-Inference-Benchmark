# 本项目执行配置

本目录用于存放已经按本项目环境适配的压测、部署和观测配置。目前没有执行配置或统一配置加载器；现有参考资料请从 [references](../references/README.md) 查阅。

## 后续配置的存放位置

以下是新增文件时的约定，相关子目录在有实际文件时创建：

| 路径约定 | 存放内容 | 示例用途 |
| --- | --- | --- |
| `configs/workloads/<场景>/` | 压测客户端参数、输入输出长度分布、请求速率、并发及 SLO 配置 | chat、rag、reasoning、agent |
| `configs/deployments/<引擎>/` | 推理服务启动、部署与模型并行配置 | vllm、sglang |
| `configs/observability/<组件>/` | 本项目使用的指标采集、日志采集、告警和 Dashboard 配置 | prometheus、grafana、alloy、loki、logrotate |

这些名称只约定文件归属，不代表项目已经支持相应场景或实现了配置解析。

## 从参考配置到执行配置

1. 从 [平台配置参考](../references/sglang-inference-platform/README.md) 选择所需文件，复制到上述对应目录。
2. 按实际环境调整地址、端口、Service、命名空间、PVC/挂载路径、模型、并行规模和指标标签；账号、令牌等使用环境变量或 Secret 注入。
3. 在配置旁写明适用引擎和版本、环境前提、运行命令，以及完成过的验证。未完成验证时明确标注。
4. 记录对应的测试场景、数据集版本、SLO 口径和结果位置，使后续运行能够复现。

原始参考材料保留在 `references/`，测试方案正文放在 `docs/`，数据集实体与运行输出不混入配置目录。新增文件的完整约定见[仓库结构说明](../docs/repository-layout.md)。
