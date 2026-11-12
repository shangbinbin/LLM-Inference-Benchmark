# llm-bench 命令行手册

`llm-bench up --config site.json --cases cases.json` 使用一份环境配置和一份有序用例清单完成压测。CLI 先检查配置和镜像，按依赖启动设备及主机采集、日志、监控组件，等采集目标和被测服务就绪后逐例启动 AISBench。生产环境的被测推理服务需要先部署好。

## 快速上手

**准备命令。** 在仓库或解压后的完整交付目录中执行以下命令。主机需要 Python 3.9+ 和可用的容器运行时，CLI 本身只使用 Python 标准库。

```bash
export PATH="$PWD/bin:$PATH"
llm-bench --help
llm-bench up --help
llm-bench bench suite --help
```

也可以直接使用 `./bin/llm-bench`。要长期使用，可将仓库的 bin 绝对路径加入 shell 配置的 PATH。交付时保留 bin、scripts、configs、containers 的目录关系。

**在 Apple Silicon Mac 运行第一套用例。** 启动 Docker Desktop，在仓库根目录执行：

```bash
llm-bench plan --profile mac-simulation --cases configs/suites/mac-smoke.json
llm-bench up --profile mac-simulation --cases configs/suites/mac-smoke.json
```

若使用旧入口启动的 mac-simulation 栈已在运行，直接执行 `llm-bench bench suite --config configs/stacks/mac-simulation.json --cases configs/suites/mac-smoke.json` 即可。首次在线启动可能构建项目镜像或拉取公共镜像。终端依次输出组件的 `READY`、每例的 `START/PASS/FAIL` 和汇总文件路径。这份清单依次执行短文本、随机 token ID 和持续压力用例。Grafana 默认入口为 http://localhost:33000。无公网演练可改用 `--profile mac-offline`，需先备齐本地 arm64 镜像。

**复用这套观测栈。** 在同一工作目录执行：

```bash
llm-bench status
llm-bench bench run --concurrency 2 --requests 8
llm-bench bench suite --cases configs/suites/mac-smoke.json
llm-bench down
```

`bench run` 创建一条名为 single 的独立用例。每次运行都产生新目录。down 清理当前栈拥有的容器和网络，保留指标、日志、配置快照和压测结果。

## GPU41 离线现场示例与运行时选择

从仓库根目录执行 `python3 scripts/create_site_config.py --runtime docker`，或 `python3 scripts/create_site_config.py --runtime nerdctl --namespace benchmark`，生成 `generated/site/site.json`（拒绝覆盖已有文件）。核对现场地址、日志和 tokenizer 后，用该文件 plan、images check、up 和 bench run。GPU41 示例固定为随机 1k/1k、1000 次请求、并发 16，完整流程见 [GPU41 指南](gpu41-qwen3-random-1k.md)。下节 linux-single 是另一份通用模板，不会继承 GPU41 的接口、日志或 workload。

## Linux x86 单机

在运行推理服务的 Linux 主机上准备实际 tokenizer、服务日志和驱动。以下示例中的地址、模型名和目录须换成现场值。默认使用本机的设备工具识别 NVIDIA 或海光，也可显式加 `--gpu-vendor nvidia` 或 `--gpu-vendor hygon`。

```bash
llm-bench up --profile linux-single \
  --endpoint http://10.0.0.21:30000 --host-address 10.0.0.21 \
  --model Qwen3-32B --tokenizer /srv/models/Qwen3-32B/tokenizer \
  --log-dir /srv/inference-logs --data-dir /srv/llm-benchmark \
  --gpus 8 --input-len 1024 --output-len 128 \
  --requests 200 --concurrency 16 --offline
```

该命令按单档参数运行一次 AISBench。若加上 `--cases ./cases.json`，将只运行清单中的用例。Linux 主机使用 linux/amd64，Mac profile 使用 linux/arm64。多机、多 P 多 D、独立 metrics 或日志端点使用完整 site.json 表达拓扑。字段说明见 [Docker 配置指南](docker-stack.md)。

## 定义顺序用例

以下内容可保存为 cases.json。用例按数组顺序调度，concurrency 控制单个用例内的请求并发。

```json
{
  "cooldown_seconds": 10,
  "stop_on_failure": true,
  "cases": [
    {"name": "baseline", "benchmark": {"input_length": 1024, "output_length": 128, "concurrency": 1, "request_count": 32}},
    {"name": "concurrency-16", "benchmark": {"input_length": 1024, "output_length": 128, "concurrency": 16, "request_count": 320}},
    {"name": "long-input", "benchmark": {"input_length": 4096, "output_length": 128, "concurrency": 16, "request_count": 320}}
  ]
}
```

```bash
llm-bench up --config site.json --cases cases.json
```

所有用例在启动栈前完成配置校验。每例独立继承栈的 benchmark 默认值，再应用本例覆盖值。相邻用例之间默认冷却 5 秒，可设为 0。默认遇到失败用例就停止，设置 `stop_on_failure: false` 后，已完成但未通过报告检查的用例会被记录，调度器继续下一例，整套运行最终仍返回失败。每个 name 须唯一，使用小写字母、数字和短横线，最长 40 字符，以字母或数字开头。

可覆盖 dataset_type、input_length、output_length、request_count、concurrency、request_rate、warmups、mode、generation_kwargs、retry、prefix_length、pressure_seconds。generation_kwargs 整体替换。用例共用站点的推理服务、设备、tokenizer 和离线设置。原生 concurrency_levels 扫描可通过不带 --cases 的 up 执行。两种输入生成方式的计数口径和实际输出长度解读见 [AISBench 使用指南](aisbench.md)。

文件型用例在与 `benchmark` 同级的 `dataset` 中写 `type` 与 `path`。路径相对清单所在目录解析。可直接运行 `llm-bench up --profile mac-offline --cases configs/suites/mixed-datasets.json`，依次发送业务 JSONL、ShareGPT 和合成请求。[三种格式、挂载规则与完整配置](aisbench-sequential.md#在同一份-casesjson-混用三种数据集)

## 命令与选项

| 命令 | 行为 |
| --- | --- |
| `plan` | 校验、渲染并显示生效配置、选中厂商和组件顺序。带 --cases 时，cases 列出各例的数据来源及生效参数，workloads 列出执行顺序。可带 --cases 或 --no-benchmark。 |
| `images check` | 按选中厂商、拓扑和平台检查本地镜像，汇总缺失项及架构错误。--no-benchmark 省略 AISBench。 |
| `images build` | 在线构建 toolbox 和 AISBench。offline 配置会拒绝构建。 |
| `up` | 启动新的观测栈并跑默认压测。--cases 指定清单，--no-benchmark 仅启动观测，两者互斥。 |
| `status` | 输出状态快照及各容器实际状态。 |
| `logs check` | 检查真实文件与 /logs 挂载、Alloy/Loki 及当前任务最近日志；可加 --lookback-minutes。无活动栈时只检查本地文件。 |
| `logs collect` | 导出离线故障包。支持选择运行、日志行数、外部日志及输出路径。[采集与回传](offline-diagnostics.md) |
| `bench run` | 在现有栈执行一条用例，接受 --input-len、--output-len、--requests、--concurrency。 |
| `bench suite --cases FILE` | 在现有栈运行另一份清单。 |
| `bench slo --policy FILE [--cases FILE]` | 按策略实测最大并发。可传入普通 suite 的用例清单，依次为每个数据集搜索并汇总。 |
| `down` | 清理选中栈。正在执行的压测持有锁时会提示稍后重试。 |

`up`、`plan` 和 `images` 的配置来源如下：

| 选项 | 含义 |
| --- | --- |
| `--profile NAME` | linux-single、nerdctl-single、nerdctl-offline-smoke、mac-simulation、mac-offline、mac-slo-search。省略 profile 和 config 时从 linux-single 开始。 |
| `--config FILE` | 单独使用时读取完整栈 JSON。与 profile 同用时，以该 profile 为底稿递归覆盖 JSON 对象，数组整体替换。 |
| `--runtime docker/nerdctl`、`--namespace NAME` | 选择本次引擎/namespace，覆盖 JSON runtime。仅 plan、up、images 支持；省略时保持 JSON。namespace 单独使用表示 nerdctl；nerdctl 未指定 namespace 时沿用源值或默认 benchmark。 |
| `--project NAME` | 独立容器和网络的前缀。并行栈需独立名称、数据目录、日志目录及宿主机端口。 |
| `--endpoint URL`、`--model ID` | 提供服务根 URL 和对外模型名。linux-single 模板的 metrics 占位地址从 URL 的主机和端口补全；已显式配置的 metrics 端点保留。 |
| `--host-address HOST` | node_exporter 的可达主机名或地址；host 模式端口由 ports.node-exporter 决定，默认 39100。 |
| `--gpu-vendor VENDOR`、`--gpus N` | auto、nvidia 或 hygon，以及单节点卡数。模拟环境需显式厂商，内置 Mac profile 已提供。 |
| `--tokenizer DIR`、`--log-dir DIR`、`--data-dir DIR` | 本地 tokenizer、被测服务日志和栈的持久化目录。 |
| `--input-len N`、`--output-len N` | 合成输入长度和请求输出上限。 |
| `--requests N`、`--concurrency N` | 正式请求数和并发。显式给出单档并发会移除继承的原生并发扫描数组。 |
| `--offline` | 开启本地镜像预检查、禁止构建/拉取并关闭已知组件外联功能。配置里已开启时省略此选项也会生效。 |

显式 runtime 选择会替换命令数组；需要自定义 socket/snapshotter/sudo 包装时在 JSON 中配置，省略 CLI runtime 覆盖。已有栈的 status、down、bench 从启动快照读取运行时；切换引擎前先 down 原栈、向目标引擎/namespace 导入镜像，再按新配置 up，不需要重启 Docker/containerd 或重建已有镜像。

优先级从低到高为 profile、配置文件、显式 CLI 参数、单个用例内的 benchmark 字段。文件名和 CLI 目录参数按当前工作目录解析，栈 JSON 内的相对 storage 路径延续旧版语义，按工程根目录解析。

## 定位运行中的栈

up 将最终配置保存到数据目录下的 `config/<哈希>/manifest.json`，运行状态写到 `state.json`。调用目录的 `.llm-bench/` 记录这次使用的数据目录。同一目录只有一套活动栈时，status、down 和 bench 可省略定位参数。

从其他目录执行、管理旧脚本启动的栈，或同一目录同时有多个活动栈时，可以这样定位：

```bash
llm-bench status --data-dir /srv/llm-benchmark
llm-bench bench suite --data-dir /srv/llm-benchmark --cases ./cases.json
llm-bench bench slo --data-dir /srv/llm-benchmark --policy ./slo.json
llm-bench down --data-dir /srv/llm-benchmark
```

也可用 `--config site.json` 通过其中的 project 和 storage.root 定位。后续任务读取启动时保存的配置。若最初通过 CLI 覆盖了 project 或数据目录，使用实际的 --data-dir。要更换服务或基础设施配置，先 down，再按新配置 up。

## SLO 搜索、结果和排障

Mac 自适应搜索示例配有专用栈模板和策略。首次在线运行需有客户端镜像，可先执行 `llm-bench images build --profile mac-slo-search`。

```bash
llm-bench up --profile mac-slo-search --no-benchmark
llm-bench bench slo --policy configs/search/mac-slo.json
llm-bench down
```

多数据集搜索可直接复用顺序压测的清单：

```bash
llm-bench bench slo --config configs/stacks/mac-slo-search.json \
  --policy configs/search/mac-mixed-datasets.json --cases configs/suites/mixed-datasets.json
```

每组正式请求数均须覆盖策略中的 min_requests 和最大并发，ShareGPT 的独立对话数还须覆盖最大并发。压测模式、时延单位、停止条件及结果解释见 [SLO 搜索手册](aisbench-auto-slo.md)。

| 任务 | 在 data-dir 下查看 |
| --- | --- |
| 默认 up | data/results/ 下的 AISBench 时间目录和 performances 报告 |
| up --cases、bench suite、bench run | data/results/suites/ 下每次运行的 suite-results.json。各用例目录中有 stack.json、client.log、results/。 |
| bench slo | data/results/slo-search/ 下的 search-results.json 及逐轮记录；使用 --cases 时查看 suite-results.json 和 cases/ 下的逐例报告 |

命令成功返回 0。自动化程序应检查退出码，并读取相应报告。清单未全部通过、SLO 未得到推荐并发或执行出错时返回非零。用例的 PASS 表示运行与报告检查通过，业务时延是否达标由 SLO 策略判定。

- **缺少镜像**。用相同配置运行 images check，导入匹配平台和标签的镜像后重试。up --cases 在创建网络前也会检查客户端。详见 [离线部署](offline-deployment.md)。
- **已有运行状态**。用 status 检查；继续测试用 bench，重新部署用 down 后再 up。清单失败后观测栈仍可用于排查和重试。
- **多个活动栈或找不到栈**。用 --data-dir 指定实际目录。不要把不同栈的状态目录互相复制。
- **Loki 无标签/日志**。运行 `llm-bench logs check --config site.json`，根据实际 storage.logs + log_sources 修正，down/up 后生效；详见 [现场修复](loki-log-troubleshooting.md)。
- **用例失败**。先找 suite-results.json 中的 case 和退出码，再查该用例的 client.log、正式请求数和失败请求数。若失败发生在观测组件启动阶段，先用 status 定位组件。

原有 benchmark_stack.py、run_aisbench_suite.py、slo_search.py 仍支持已有自动化。新的日常操作可统一使用 llm-bench。

项目 HTTP 服务默认通过节点 IP 和高位端口访问，支持 ports、port_bind_address 和 publish_ports；详见 [端口配置](docker-stack.md#节点-ip-访问与可配置高位端口)。

## nerdctl / containerd

新增 `nerdctl-single`、`nerdctl-offline-smoke` profile；site.json 也可设置 `"runtime":["nerdctl","--namespace","benchmark"]`。显式 namespace 必须与镜像 load 一致，单次、顺序、SLO 搜索、状态、诊断和 down 共用运行时。完整步骤和现场 GPU 要求见 [nerdctl 部署指南](nerdctl.md)。
