# LLM 推理服务压测

面向不同 GPU/DCU 上的推理服务，管理压测观测链路、环境配置与场景参考。以 AISBench 发起压测，按依赖顺序启动 Prometheus、Grafana、Loki、Alloy、日志轮转和设备/主机采集容器。被测推理服务由外部部署；支持 NVIDIA / 海光指标与面板切换，并预留多机及多 P 多 D 采集接口。

**文档入口：** [文档总览与阅读指南](docs/文档总览与阅读指南.md) 按任务给出阅读顺序，并逐份说明操作指南、配置 README、验证记录和来源资料的用途。

宿主机支持 Python 3.9+；一键交付包默认适配离线宿主机 Python 3.9，可用 `OFFLINE_PYTHON` 指定其他版本。AISBench/toolbox 容器内部使用 Python 3.11，与宿主机版本独立。

## 快速开始

源码入口先安装 YAML 依赖：`python3 -m pip install -r requirements.txt`。

单机共节点的完整流程见 [在线一键打包 → 离线一键部署 → 分级压测](docs/单机共节点离线部署与分级压测.md)，含 [带注释的 YAML 示例](configs/sites/single-node.example.yaml)、`scripts/build_offline_bundle.sh` 和 `scripts/deploy_offline.sh`。在线打包默认下载并携带 Qwen3-0.6B tokenizer，tokenid 无 GPU 全链路测试通过后才导出镜像和压缩包；离线可用 `deploy_offline.sh --simulation` 仅导入，再用 `llm-bench up/verify` 进行全链路模拟。

在仓库根目录将 CLI 加入当前 shell 的 PATH。Apple Silicon Mac 上启动 Docker Desktop 后，以下命令启动观测栈，再依次执行仓库自带的三个 AISBench 用例。

```bash
export PATH="$PWD/bin:$PATH"
llm-bench up --profile mac-simulation --cases configs/suites/mac-smoke.yaml
llm-bench status
llm-bench bench run --concurrency 2 --requests 8
llm-bench down
```

结果位于 `generated/mac-smoke/data/results/suites/`，Grafana 入口为 `http://localhost:33000`。已有环境可直接执行 `llm-bench up --config site.yaml --cases cases.yaml`。Linux 单机支持直接指定服务地址、模型、tokenizer 和存储路径；生产镜像为 linux/amd64。

**离线对接已有服务：** 先阅读 [修改 site.yaml 后启动压测](docs/现场配置与压测指南.md)，核对 NVIDIA/海光与 Docker/nerdctl 的选择和各字段；完整实例见 [GPU41 示例](docs/节点41随机负载压测示例.md)。在仓库根目录执行 `python3 scripts/create_site_config.py --runtime docker`，或选择 `--runtime nerdctl --namespace benchmark`，生成 `generated/site/site.yaml` 后修改现场地址、路径及端口。已有文件不会被覆盖；该配置不属于模拟冒烟测试。

| 常用入口 | 用途 |
| --- | --- |
| [文档总览与阅读指南](docs/文档总览与阅读指南.md) | 不确定读哪份文档时，从这里按任务选择；完整清单也集中在此。 |
| [单机共节点离线部署与分级压测](docs/单机共节点离线部署与分级压测.md) | 在线一键打包、离线部署、冒烟与分级压测。 |
| [现场配置与压测指南](docs/现场配置与压测指南.md) | 修改 site.yaml，核对现场地址、路径、设备和运行参数。 |
| [多测试例顺序压测指南](docs/多测试例顺序压测指南.md) | 编写 cases.yaml，依次执行不同负载与数据集。 |
| [推理性能诊断与瓶颈排查](docs/推理性能诊断与瓶颈排查.md) | 理解服务指标并排查队列、缓存、执行阶段和实例差异。 |

海光正式链路默认使用光合社区 dcu-exporter:v2.4.1，按 dcu_* 指标、name/minor_number 标签生成检测和面板；升级已有现场配置见 [DCU v2.4.1 迁移说明](docs/海光采集器版本2.4.1适配指南.md)。HCU v3 配置保留为独立参考，不再是 llm-bench 默认。真实硬件及镜像拉取仍需现场验证。
