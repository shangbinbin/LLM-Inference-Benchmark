# LLM 推理服务压测

面向不同 GPU/DCU 上的推理服务，管理压测观测链路、环境配置与场景参考。以 AISBench 发起压测，按依赖顺序启动 Prometheus、Grafana、Loki、Alloy、日志轮转和设备/主机采集容器。被测推理服务由外部部署；支持 NVIDIA / 海光指标与面板切换，并预留多机及多 P 多 D 采集接口。

## 快速开始

在仓库根目录将 CLI 加入当前 shell 的 PATH。Apple Silicon Mac 上启动 Docker Desktop 后，以下命令启动观测栈，再依次执行仓库自带的三个 AISBench 用例。

```bash
export PATH="$PWD/bin:$PATH"
llm-bench up --profile mac-simulation --cases configs/suites/mac-smoke.json
llm-bench status
llm-bench bench run --concurrency 2 --requests 8
llm-bench down
```

结果位于 `generated/mac-smoke/data/results/suites/`，Grafana 入口为 `http://localhost:33000`。已有环境可直接执行 `llm-bench up --config site.json --cases cases.json`。Linux 单机支持直接指定服务地址、模型、tokenizer 和存储路径；生产镜像为 linux/amd64。

| 入口 | 内容 |
| --- | --- |
| [CLI 使用手册](docs/cli.md) | 快速上手、参数优先级、顺序用例、复用观测栈、结果及排障 |
| [Docker 一键部署](docs/docker-stack.md) | 启动顺序、配置项、存储、分布式及 containerd 接口 |
| [推理服务 Bearer Token](docs/inference-auth.md) | 环境变量鉴权、就绪探测、单次/顺序/SLO 压测与离线升级 |
| [离线环境部署](docs/offline-deployment.md) | offline 模式、缺镜像清单、amd64 交付包、组件外联设置与 Mac 验收 |
| [离线日志与故障回传](docs/offline-diagnostics.md) | 一键打包全链路组件日志、AISBench/SLO 结果和异常信息 |
| [无 GPU 在线构建 → 离线 NVIDIA 压测](docs/online-cpu-to-offline-nvidia.md) | 逐步构建、镜像导出、tokenizer 校验、现场部署、验收与排错 |
| [AISBench 使用](docs/aisbench.md) | 合成数据、输入输出长度、压测模式与结果解释 |
| [批量顺序执行](docs/aisbench-sequential.md) | 混用业务 JSONL、ShareGPT 与合成数据的完整示例、并发档扫描和失败处理 |
| [SLO 容量搜索](docs/aisbench-slo-capacity.md) | 业务阈值、候选负载扫描、自动筛选、边界细化与复测 |
| [SLO 自适应搜索](docs/aisbench-auto-slo.md) | 按测试例搜索 TTFT、TPOT 达标的最大并发，汇总不同数据集的结果 |
| [Mac 验证记录](docs/reports/mac-container-smoke-2026-11-07.md) | 实际容器、请求、指标、日志及重启验证 |
| [配置分层](configs/README.md) | 通用组件、GPU 适配、网络适配、环境实例 |
| [官方监控资料与下载](docs/hygon-official-monitoring.md) | 最新官方文档、配置原件、镜像入口和版本差异 |
| [海光接入](configs/gpu/hygon/README.md) | 官方 HCU v3 参考适配器、下载入口及待填现场信息 |
| [使用指南](docs/observability.md) | 生成、挂载、标签口径与现场验证 |
| [目录及迁移说明](docs/repository-layout.md) | 文件职责、新旧路径映射 |
| [来源资料](references/README.md) | H200 原始链路与压测场景资料 |

海光提供基于官方文档的 HCU v3 参考适配器，暂不限定型号；环境模板需补齐现场信息，尚未通过硬件部署验证。官方旧 DCU 面板另存来源档案，不会混入新版配置。
