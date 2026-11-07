# LLM 推理服务压测

面向不同 GPU/DCU 上的推理服务，管理压测观测链路、环境配置与场景参考。以 AISBench 发起压测，按依赖顺序启动 Prometheus、Grafana、Loki、Alloy、日志轮转和设备/主机采集容器。被测推理服务由外部部署；支持 NVIDIA / 海光指标与面板切换，并预留多机及多 P 多 D 采集接口。

## 快速开始

Docker Desktop 已启动时，在 Mac 上一键运行无 GPU 模拟链路：

```bash
python3 scripts/benchmark_stack.py up --config configs/stacks/mac-simulation.json
```

真实运行 AISBench 与观测组件，以测试端点模拟推理响应和 GPU 指标。结果保存在 `generated/mac-smoke/data/results/`；Grafana 打开 `http://localhost:33000`。生产 NVIDIA 单机从 `configs/stacks/nvidia-single.json` 复制并填写地址、模型及存储路径。

```bash
python3 scripts/benchmark_stack.py down --config configs/stacks/mac-simulation.json
python3 -m unittest discover -s tests -v
```

| 入口 | 内容 |
| --- | --- |
| [Docker 一键部署](docs/docker-stack.md) | 启动顺序、配置项、存储、分布式及 containerd 接口 |
| [离线环境部署](docs/offline-deployment.md) | offline 模式、缺镜像清单、amd64 交付包、组件外联设置与 Mac 验收 |
| [AISBench 使用](docs/aisbench.md) | 合成数据、输入输出长度、压测模式与结果解释 |
| [批量顺序执行](docs/aisbench-sequential.md) | 原生并发档扫描、任意用例清单、冷却和失败处理 |
| [SLO 容量搜索](docs/aisbench-slo-capacity.md) | 业务阈值、候选负载扫描、自动筛选、边界细化与复测 |
| [SLO 自适应搜索](docs/aisbench-auto-slo.md) | 根据每轮结果自动调整并发、重复验证、范围与停止条件 |
| [Mac 验证记录](docs/reports/mac-container-smoke-2026-11-07.md) | 实际容器、请求、指标、日志及重启验证 |
| [配置分层](configs/README.md) | 通用组件、GPU 适配、网络适配、环境实例 |
| [官方监控资料与下载](docs/hygon-official-monitoring.md) | 最新官方文档、配置原件、镜像入口和版本差异 |
| [海光接入](configs/gpu/hygon/README.md) | 官方 HCU v3 参考适配器、下载入口及待填现场信息 |
| [使用指南](docs/observability.md) | 生成、挂载、标签口径与现场验证 |
| [目录及迁移说明](docs/repository-layout.md) | 文件职责、新旧路径映射 |
| [来源资料](references/README.md) | H200 原始链路与压测场景资料 |

海光提供基于官方文档的 HCU v3 参考适配器，暂不限定型号；环境模板需补齐现场信息，尚未通过硬件部署验证。官方旧 DCU 面板另存来源档案，不会混入新版配置。
