# LLM 推理服务压测

面向不同 GPU/DCU 上的推理服务，管理压测观测链路、环境配置与场景参考。目前提供 SGLang 的指标、GPU、可选 IB 网络及日志配置组合工具，尚未实现自动发压或推理服务部署。

## 快速开始

```bash
python3 scripts/render_observability.py \
  --environment configs/environments/nvidia-h200-3node-8gpu/environment.json \
  --output generated/nvidia-h200-3node-8gpu
python3 -m unittest discover -s tests -v
```

生成结果沿用三台 8 卡 NVIDIA H200 的来源地址，仅用于配置对照。复制环境清单、调整地址和挂载后，按[观测链路使用指南](docs/observability.md)部署并验证。

| 入口 | 内容 |
| --- | --- |
| [配置分层](configs/README.md) | 通用组件、GPU 适配、网络适配、环境实例 |
| [官方监控资料与下载](docs/hygon-official-monitoring.md) | 最新官方文档、配置原件、镜像入口和版本差异 |
| [海光接入](configs/gpu/hygon/README.md) | 官方 HCU v3 参考适配器、下载入口及待填现场信息 |
| [使用指南](docs/observability.md) | 生成、挂载、标签口径与现场验证 |
| [目录及迁移说明](docs/repository-layout.md) | 文件职责、新旧路径映射 |
| [来源资料](references/README.md) | H200 原始链路与压测场景资料 |

海光提供基于官方文档的 HCU v3 参考适配器，暂不限定型号；环境模板需补齐现场信息，尚未通过硬件部署验证。官方旧 DCU 面板另存来源档案，不会混入新版配置。
