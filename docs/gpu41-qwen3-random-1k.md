# GPU41：离线单实例 Qwen3-0.6B 压测

以 [gpu41-qwen3-0.6b-random-1k.json](../configs/sites/gpu41-qwen3-0.6b-random-1k.json) 为完整现场示例。它对接已经部署的推理服务，不会启动模型服务或修改 Kubernetes Pod。所有命令在解压后的仓库根目录执行。

## 1. 生成现场配置并选择 Docker 或 nerdctl

**`generated/site/site.json` 需要先生成。它不会随仓库下载出现，`up` 也不会自动创建这个输入文件。** 以下两条生成命令选择一条执行：

```bash
# Docker
python3 scripts/create_site_config.py --runtime docker
```

```bash
# nerdctl / containerd，镜像必须导入同一 namespace
python3 scripts/create_site_config.py --runtime nerdctl --namespace benchmark
```

默认源文件是上述 GPU41 示例，默认输出为当前目录下的 `generated/site/site.json`。省略 `--runtime` 时沿用源文件设置（GPU41 示例为 Docker）。可用 `--source 完整配置.json --output 另一现场配置.json` 指定其他文件。**输出已存在时拒绝覆盖**；已有现场配置应直接编辑或另选输出文件。生成时校验配置结构，tokenizer 的实际文件在后续 plan/up 时检查。

选择运行时只修改 `runtime`，保持服务、鉴权变量名、日志、端口与 workload。等价 JSON 为：

| 引擎 | 顶层配置 |
| --- | --- |
| Docker | `"runtime": ["docker"]` |
| nerdctl | `"runtime": ["nerdctl", "--namespace", "benchmark"]` |

也可在 `llm-bench plan`、`images check/build`、`up` 上使用 `--runtime docker` 或 `--runtime nerdctl --namespace benchmark` 覆盖本次生效配置，源 JSON 不会被改写。单独使用 `--namespace NAME` 表示选择 nerdctl。nerdctl 未指定 namespace 时沿用源配置中的 namespace；从 Docker 切换时使用 benchmark。CLI 选择会替换 runtime 命令数组；如需 sudo 包装、socket 或 snapshotter 等自定义参数，请在 JSON 中完整配置，不传运行时覆盖参数。

## 2. 核对现场字段

逐字段说明、占位符定位、NVIDIA/海光和 Docker/nerdctl 对照，以及修改后完整启动顺序见 [site.json 配置与启动压测手册](site-config-and-benchmark.md)。

编辑 `generated/site/site.json`。示例的 GPU41 地址与 NFS 目录不会自动发现或转换，换节点、模型时必须同步修改。

| 字段 | GPU41 示例与修改要求 |
| --- | --- |
| `project` / `task` | `llm-bench-qwen-real` / `qwen3-0.6b`；project 是资源名称前缀，task 是指标和日志的任务筛选标签 |
| `storage.root` | `generated/qwen-real`，保存状态、配置快照、监控数据与结果；相对工程根目录解析 |
| `storage.logs` | `/nfs-data/b80b3433c7374662bd67be120e30f566/a78f816093a2b0414d6ea1530dfae279`，真实服务日志所在目录 |
| `storage.tokenizer` | 示例同上；也可改为交付的 `assets/tokenizer`，必须匹配被测模型且包含真实 tokenizer 文件 |
| `log_sources` | `server_log.log`，角色 `master`、副本 `replica-0`、节点 `gpu41`；文件路径相对 storage.logs |
| `inference.base_url` | `http://10.142.118.41:38000/publishaddress/inference/qwen306b/`，保留网关前缀，不包含具体 API 后缀 |
| `inference.model/api/engine` | `Qwen3-0.6B` / `chat` / `vllm`，model 必须是服务接受的 ID |
| `inference.api_key_env` | `INFERENCE_API_KEY`，只保存变量名；压测和应用 metrics 共用 Token |
| `gpu` | NVIDIA、本机 Exporter、节点 gpu41、1 张卡、basic collectors；按实际硬件调整 |
| `host_metrics` | 示例为 container，只采容器视角主机指标；需要真实宿主机指标时改为 host，并填写容器可达的 node_exporter_target |
| `ports` / `port_bind_address` | 默认高位端口、绑定 0.0.0.0；Grafana 33000、Prometheus 39090、Loki 33100，其余见 JSON；占用时修改 |
| `offline` / `legacy_seccomp_unconfined` | 均为 true，禁止拉取并为所有项目新容器关闭 seccomp 系统调用过滤；影响与关闭方法见 [兼容说明](legacy-docker-threads.md) |

API 从 base_url 派生为 `v1/chat/completions`、`v1/models` 与 `metrics`。GPU41 的 metrics 为 `/publishaddress/inference/qwen306b/metrics`，示例没有独立指标覆盖值；更换 base_url 后三者一起改变。如复制旧配置，检查是否遗留 `topology.metrics_endpoints/metrics_path/metrics_scheme/metrics_api_key_env` 覆盖值，详见 [鉴权与 URL](inference-auth.md)。

默认 benchmark 是 **随机 tokenid 输入 1024、输出请求上限 1024、1000 次请求、并发 16**；AISBench 本地生成，无需先准备数据集。warmups=0、retry=1、request_rate=0，generation_kwargs 为 temperature=0、ignore_eos=true。后端须支持流式 chat 与 ignore_eos；1024 是请求上限，实际完成长度以报告为准。

若复用到海光节点，修改 gpu.vendor、节点和 Exporter 配置；已有 Exporter 可用 deployment=external、每个节点填写真实 target。旧 dcu-exporter 2.4.1 不应直接假定符合 HCU v3 的指标契约，须先核对其 `/metrics`，见 [海光指标适配](task-observability.md)。

## 3. 导入镜像、鉴权与预检查

先按 [离线部署](offline-deployment.md) 准备完整源码、amd64 镜像和真实 tokenizer。根据所选运行时执行对应导入命令，归档路径替换为实际位置：

```bash
# Docker 配置使用这一条
docker image load -i /home/llm-bench-delivery/offline-bundle/images/images-linux-amd64.tar
```

```bash
# nerdctl 配置使用这一条，namespace 与 site.json 一致
nerdctl --namespace benchmark load -i /home/llm-bench-delivery/offline-bundle/images/images-linux-amd64.tar
```

Docker 与 containerd 的镜像存储独立，namespace 也必须一致。仅切换配置/运行时不需要重建现有镜像或重启 Docker/containerd；nerdctl 的 CNI、设备注入及 1.7.7 注意事项见 [nerdctl 指南](nerdctl.md)。

需要鉴权时，在运行压测的同一 Bash 终端输入实际 Token：

```bash
read -r -s -p 'Inference Token: ' INFERENCE_API_KEY
export INFERENCE_API_KEY
./bin/llm-bench plan --config generated/site/site.json
./bin/llm-bench images check --config generated/site/site.json
```

plan 不读取 Token、不访问服务，也不验证镜像存在；它会检查 tokenizer 文件并生成配置快照。images check 使用所选运行时检查本地镜像。不要将 plan 当成服务连通性验收。

## 4. 启动、小规模验证和正式压测

```bash
./bin/llm-bench up --config generated/site/site.json --no-benchmark
./bin/llm-bench status --config generated/site/site.json
# 先以相同 1k/1k 长度做 8 次请求、并发 2 验证
./bin/llm-bench bench run --config generated/site/site.json --requests 8 --concurrency 2
# 验证通过后使用配置中的 1000 次请求、并发 16
./bin/llm-bench bench run --config generated/site/site.json
```

只需直接启动并执行一次正式压测时，使用不带 `--no-benchmark` 的 up。**已有栈再次发压使用 bench run，不要重复 up。** bench run 的进度输出写入用例目录的 client.log，可在另一终端对本次文件执行 `tail -f`。

Grafana：`http://运行观测栈的节点IP:33000`（部署在 GPU41 时为 `http://10.142.118.41:33000`）。打开任务总览，任务选择 `qwen3-0.6b`，GPU 选择节点、型号与卡序号，日志选择 instance_role/replica。Loki 日志可查询 `{task="qwen3-0.6b", instance_role="master", replica="replica-0"}`，总览同时包含 Logs Volume。真实服务须持续向指定文件输出日志，详见 [任务观测](task-observability.md)。

## 5. 数据、停止与切换引擎

| 内容 | GPU41 示例中的位置 |
| --- | --- |
| 默认 up 压测结果 | `generated/qwen-real/data/results/<AISBench时间目录>/performances/` |
| bench run 日志和结果 | `generated/qwen-real/data/results/suites/<运行目录>/single/client.log` 与同目录的 `results/` |
| 监控持久数据 | `generated/qwen-real/data/` 下的 prometheus、loki、grafana、alloy、logrotate |
| 运行配置快照 / 状态 | `generated/qwen-real/config/<哈希>/manifest.json` / `generated/qwen-real/state.json` |
| 原始推理日志 | `storage.logs/server_log.log`，仍在真实服务的节点目录 |

```bash
./bin/llm-bench logs collect --config generated/site/site.json
./bin/llm-bench down --config generated/site/site.json
```

down 只删除本项目登记且标签匹配的容器和网络，保留持久数据与原始日志。status/down/bench 使用启动时保存的 runtime/namespace；它们不接受运行时覆盖参数。

**Docker ↔ nerdctl 切换流程：先 down 原栈 → 将镜像导入新引擎/namespace → 修改现场 JSON 的 runtime（或在 up 时显式覆盖）→ images check → up。** 同一持久目录可保留，但须确认新容器有读写权限。修改 JSON 后直接 bench run 不会改变已运行栈的引擎或基础配置。并行运行两套时必须分别配置 project、storage.root 与端口；nerdctl 1.7.7 的 project 名称还须在节点上唯一。
