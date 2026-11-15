# 单机共节点：在线打包、离线部署与分级压测

适用于 Linux x86_64，推理服务与压测/观测链路在同一台 GPU/DCU 节点。推理服务、模型权重、设备驱动和 GPU 容器运行时须提前部署；本工具部署 Prometheus、Grafana、Loki、Alloy、Exporter、日志轮转及 AISBench 客户端。在线机无需 GPU，其模拟冒烟验证软件链路；离线机再验证真实服务。

## 1. 在线机一键构建交付包

准备 Git、可访问的 Docker daemon、Python 3.10+（含 venv/pip）、GNU tar/coreutils，以及足够的构建/镜像导出磁盘空间（建议至少 30 GB）。网络须可访问 GitHub、镜像源、PyPI、AISBench 构建所用的 Debian/PyTorch 等依赖源；私有镜像先完成 `docker login`。

在已有仓库根目录运行（脚本会另建目录重新拉取远端，**只打包远端已提交的版本**）：

```bash
REF=main OFFLINE_PYTHON=3.11 \
  bash scripts/build_offline_bundle.sh
```

默认拉取当前项目远端 `https://github.com/shangbinbin/LLM-Inference-Benchmark.git`。自建镜像仓库或不同交付选择可先修改一份 YAML，然后指定绝对路径：

```bash
REPO_URL=https://github.com/shangbinbin/LLM-Inference-Benchmark.git \
REF=main OFFLINE_PYTHON=3.11 \
SITE_CONFIG=/opt/build/site.yaml \
DELIVERY_CONFIG=/opt/build/offline-images.yaml \
TOKENIZER_DIR=/opt/models/Qwen3-0.6B \
OUTPUT_DIR=/opt/delivery \
  bash scripts/build_offline_bundle.sh
```

后三个配置变量均可省略；默认现场模板为 [`single-node.example.yaml`](../configs/sites/single-node.example.yaml)，默认交付 NVIDIA/DCU 两种 Exporter。`TOKENIZER_DIR` 仅复制 tokenizer 相关文件，不包含权重；省略时在离线机准备实际 tokenizer。`OFFLINE_PYTHON` 必须与离线机的 CPython 主次版本一致；包内带相应 Linux x86_64 wheel，无需离线下载依赖。默认 Python 3.11，支持设置为其他有 PyYAML wheel 的版本。海光私有镜像不可访问时，可在 `delivery.yaml` 的 `selected_gpu_exporters` 中仅保留实际需要的厂商；离线 `gpu.vendor` 应与交付选择一致。

自动执行：远端拉取并记录提交 → 准备 Python 依赖和离线 wheel → 单元测试 → 构建 toolbox/AISBench → 拉取并校验镜像架构、导出镜像 → 用这些镜像进行 CPU 模拟冒烟 → 验证请求结果、Prometheus、Grafana 和 Loki → 关闭模拟栈 → 打包源码、镜像、wheel、配置及验证证据。冒烟默认使用 43000 起的七个端口，冲突时设置 `SMOKE_PORT_BASE`。任一步失败返回非零，保留 `.build-*/build.log` 和诊断数据，不产出成功压缩包。

输出为 `generated/delivery/llm-bench-offline-<提交>-<UTC时间>.tar.gz` 及 `.sha256`。包内有 `repo/`、`images/`、`wheelhouse/`、`metadata/`、带注释的 `site.yaml`、`delivery.yaml`、`SHA256SUMS` 和本文。模拟冒烟证据在 `metadata/online-smoke.yaml`，源提交在 `metadata/revision.txt`。不要从构建中的 `.build-*` 目录提前拷包。

## 2. 离线机导入并一键部署（不发起压测）

提前安装 Docker 或 nerdctl/containerd、相应 GPU/DCU 驱动和容器运行时，以及与 `OFFLINE_PYTHON` 相同版本的 CPython（含 venv/pip）。这些主机软件与模型权重不在包内。当前用户须有容器运行权限及配置中目录的读写权限。启动推理服务，确认 `/v1/models`、`/metrics` 和真实日志可访问。

将压缩包和校验文件复制到离线机同一目录：

```bash
sha256sum -c llm-bench-offline-*.tar.gz.sha256
tar -xzf llm-bench-offline-<提交>-<时间>.tar.gz
cd llm-bench-offline-<提交>-<时间>
vi site.yaml
```

按 `site.yaml` 中的 **【必改】** 注释核对：此机真实 IP/推理端口、模型 ID、tokenizer/日志/结果目录、日志文件、GPU 厂商/数量/节点名、TP/PP 和正式 workload。推理服务须监听容器可访问的本机 IP 或 `0.0.0.0`；桥接容器中的 `127.0.0.1` 指向容器自身。若使用包内 tokenizer，可将 `storage.tokenizer` 改为解压后 `tokenizer/` 的**绝对路径**。`storage.logs` 下配置的日志文件必须真实存在，勿创建空文件替代真实服务日志。

Docker 默认使用 `runtime: [docker]`。nerdctl 改为 `runtime: [nerdctl, --namespace, benchmark]`，导入及启动自动复用该 namespace。保持 `offline: true`、`simulation: false`。有鉴权时取消 `api_key_env` 注释，在本机 shell 设置对应环境变量；若指标不鉴权，可设置 `topology.metrics_api_key_env: null`。

```bash
# 有鉴权时设置（只输入 Token 本身，不带 Bearer 前缀）
# read -rsp 'Inference token: ' INFERENCE_API_KEY; export INFERENCE_API_KEY; echo
PYTHON=python3.11 bash repo/scripts/deploy_offline.sh
source ./env.sh
SITE="$PWD/site.yaml"
llm-bench status --config "$SITE"
```

脚本校验包内不可变文件，使用 `pip --no-index` 安装包内 wheel，导入镜像，检查镜像并执行 `llm-bench up --no-benchmark`。部署会检查服务/指标就绪，**不会发送 AISBench 冒烟或压测 workload**。推理服务由原有部署管理，不由本脚本启动。`site.yaml` 可现场编辑，不在包内逐文件校验清单中；源码、镜像、wheel 和验证元数据均在清单中。

## 3. 用 llm-bench 逐级测试

从上述 bundle 根目录执行；新 shell 先重新 `source ./env.sh` 并设置 `SITE` 和所需鉴权环境变量。每步成功后再继续下一步：

```bash
# 冒烟：1 并发、4 请求，先确认真实模型、鉴权、tokenizer 和流式接口可用
llm-bench bench run --config "$SITE" \
  --input-len 32 --output-len 16 --concurrency 1 --requests 4

# 小规模：2 并发、20 请求；检查响应成功、延迟、GPU/CPU/日志
llm-bench bench run --config "$SITE" \
  --input-len 128 --output-len 64 --concurrency 2 --requests 20

# 正式压测：使用已审核的 site.yaml 中 benchmark 参数（示例 1k/1k、16 并发、1000 请求）
llm-bench bench run --config "$SITE"

# 查看日志采集状态；测试结束后关闭观测栈，保留数据
llm-bench logs check --config "$SITE"
llm-bench down --config "$SITE"
```

`bench run` 只对当前运行栈的 workload 做参数覆盖。若修改 `site.yaml` 的模型、路径、GPU、端口或正式参数，先 `down` 再重新部署；运行中的栈使用启动时保存的配置。测试按顺序执行，勿同时运行不同 workload。默认结果在 `storage.root/data/results/suites/<时间>/`，`suite-results.json` 中每例应为 `passed: true`、`status: complete`，AISBench 汇总的 Failed Requests 应为 0。Grafana 默认 `http://<此机IP>:33000`，初始账号按镜像默认 `admin/admin`，首次登录后修改密码，密码变更保存在持久数据目录。

同机压测会与推理服务共享 CPU、内存和网络资源，正式并发应根据小规模测试逐步提高，同时观察客户端是否成为瓶颈。Grafana 任务总览的 node-exporter 主机资源区域可查看 CPU/I/O 等待、负载、内存、文件系统、磁盘和网络；通过“主机采集目标”筛选，单机模板保持 `host_metrics: host` 才能完整采集宿主机。详细口径见[任务观测说明](task-observability.md#主机资源的筛选与采集口径)。失败时停止后续档位，用 `llm-bench logs collect --config "$SITE"` 收集诊断包；部署失败也保留栈和日志，先定位再用 `down` 清理后重试。不要直接反复执行部署脚本覆盖活跃栈。

## YAML 迁移说明

`configs/` 中部署、交付、suite、search、环境、GPU/网络适配和 Dashboard 源配置均改为 `.yaml`；源码运行前先 `python3 -m pip install -r requirements.txt`。旧的外部 JSON 配置仍可读取，推荐改用 YAML；`create_site_config.py` 默认输出 `generated/site/site.yaml`。模板中的字符串若类似布尔值（如 `"off"`）或数字，请加引号；重复键会报错。

tokenizer 的 `tokenizer*.json`、ShareGPT/JSONL 数据集、运行状态/测试报告和 `references/` 中的上游原始资料保留原格式。Grafana 运行时仍需 JSON Dashboard，由 YAML 源自动渲染；Prometheus/Grafana provisioning 现在生成常规 YAML。无需手工修改 `storage.root/config/<哈希>/` 中的渲染文件。
