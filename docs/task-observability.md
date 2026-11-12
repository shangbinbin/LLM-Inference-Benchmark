# 按推理任务查看指标与日志

`llm-bench up --config site.json` 会自动预置一个“推理任务总览 · NVIDIA”或“推理任务总览 · 海光”Dashboard，包含 vLLM、SGLang 核心性能、当前厂商 GPU 指标、Logs Volume 和日志列表。无需在 Grafana 中手工添加面板或安装插件。

浏览器访问 `http://运行观测栈的节点IP:Grafana端口/d/inference-task-overview`，默认端口为 33000；冒烟配置默认 33003，以现场 `ports.grafana` 为准。也可从 Dashboards → Inference Benchmark 打开。

## 标签与筛选关系

| 数据 | 主筛选链 | 配置来源 |
| --- | --- | --- |
| 推理服务性能 | `task` | 顶层 `task`，省略时使用 `project` |
| GPU 监控 | `task` → `gpu_node` → `gpu_model` → `gpu_index` | 同一任务、`gpu.nodes[].name`、型号、Exporter 原生卡序号 |
| Loki 日志 / Logs Volume | `task` → `instance_role` → `replica` → `source_file` | 同一任务、`log_sources[].role` / `.replica` / `.path` |

`project` 继续管理容器名称、网络及 ownership，不再承担唯一的业务分组职责。例如 `project=llm-bench-qwen-real`、`task=qwen3-0.6b`。同一业务任务的指标、GPU、日志会自动得到相同的 `task`；无需逐条重复填写。

旧标签 `deployment=project`、`environment=project` 保留。推理指标和日志保留 `role` 别名，GPU 保留 `node` 别名及原生 `gpu`、`modelName`、`minor_number` 等字段。应用抓取 job 统一为 `inference`；旧的 `job="sglang"` 自定义查询应相应修改。`inference_engine` 根据 `vllm:` / `sglang:` 指标名称识别，其他指标使用 `inference.engine` 配置值。

推理性能图只按任务聚合，改变 GPU 或日志筛选不会改变推理性能图。GPU 节点、型号、卡序号和日志角色、副本、文件都支持多选 / All；下级选项依赖上级选项。配置中的标签值为最多 128 字符的非空字符串，不含控制字符、双引号、反斜杠或反引号。不要用请求 ID、时间戳等不断变化的内容作为这些标签。

## 配置模板

单机以 [GPU41 现场配置](../configs/sites/gpu41-qwen3-0.6b-random-1k.json) 为完整例子，先按 [GPU41 指南](gpu41-qwen3-random-1k.md)生成 `generated/site/site.json`，支持 Docker/nerdctl 选择；通用场景也可复制 [nvidia-single.json](../configs/stacks/nvidia-single.json)。多副本参考 [完整模板](../configs/sites/task-multi-replica.example.json)，替换全部 `REPLACE_`，并保留实际使用的实例。以下是合并到完整 site.json 的片段：

```json
{
  "project": "llm-bench-qwen-real",
  "task": "qwen3-0.6b",
  "inference": {
    "base_url": "http://10.142.118.41:38000/publishaddress/inference/qwen306b/",
    "model": "Qwen3-0.6B",
    "api": "chat",
    "engine": "vllm",
    "api_key_env": "INFERENCE_API_KEY"
  },
  "gpu": {
    "vendor": "nvidia",
    "deployment": "local",
    "nodes": [{"name": "gpu41", "gpus": 1}]
  },
  "storage": {
    "root": "generated/qwen-real",
    "logs": "/nfs-data/b80b3433c7374662bd67be120e30f566/a78f816093a2b0414d6ea1530dfae279",
    "tokenizer": "/nfs-data/b80b3433c7374662bd67be120e30f566/a78f816093a2b0414d6ea1530dfae279"
  },
  "log_sources": [
    {"path": "server_log.log", "role": "master", "replica": "replica-0", "node": "gpu41"}
  ]
}
```

`inference.engine` 支持 `auto`（默认）、`vllm`、`sglang`，作为服务描述标签；不会改变推理进程，也不改变 `inference.api`。两种引擎的常用图表都已预置，未暴露相应指标的图表显示 No data。接口及 metrics 鉴权仍按 [统一基础 URL](inference-auth.md#统一基础-url自动拼接推理与指标接口)配置，不把 Token 写入 JSON。

### 多副本：角色与日志路径一一对应

```json
{
  "task": "qwen-service",
  "topology": {
    "kind": "pd", "tp": 8, "pp": 1,
    "metrics_endpoints": [
      {"target": "prefill-host:8000", "role": "prefill", "replica": "prefill-0"},
      {"target": "decode-host:8000", "role": "decode", "replica": "decode-0"}
    ]
  },
  "log_sources": [
    {"path": "prefill-0/server.log", "role": "prefill", "replica": "prefill-0", "node": "gpu-a"},
    {"path": "decode-0/server.log", "role": "decode", "replica": "decode-0", "node": "gpu-b"}
  ]
}
```

每个 `path` 都相对于 `storage.logs`，上述文件分别为 `storage.logs/prefill-0/server.log` 和 `storage.logs/decode-0/server.log`。角色可以是 `master`、`worker`、`router`、`prefill`、`decode` 等稳定名称；`replica` 用于区分同角色的多个实例。一个副本的多个日志文件使用相同角色和副本标签、不同路径即可。

**设置 `log_sources` 后，它优先于旧 `log_files`，采集和 logrotate 都读取这一份清单。** 路径必须指向具体活跃文件，不能使用通配符、绝对路径或 `..`。省略 `log_sources` 时仍兼容 `log_files`：按对应序号的 metrics endpoint 补角色/副本；没有对应 endpoint 时，首文件为 master，其余为 worker，副本默认 replica-N。正式多副本配置应显式填写，避免顺序变化导致误标。

这只是采集标签映射，不会自动创建 Pod 或迁移日志。当前 Alloy 读取一个共享/本机日志根目录；跨节点的独立本地目录需另行部署采集器，写入同一 Loki 时使用一致标签，且同一文件只采集一次、只由一个轮转器管理。

`metrics_endpoints` 只填写实际存在、统计边界不重复的端点。Dashboard 按任务汇总计数器和直方图，不会自动去除 router/prefill/decode 重复统计的同一个请求。只在 rank 0 暴露请求统计的服务，不能把同一聚合入口为每个 rank 重复添加。

### GPU 节点、型号、卡序号

本机自动识别可设置 `gpu.vendor="auto"`：启动器检查本机 `nvidia-smi` / `hy-smi`，识别后生成对应 Exporter 抓取和 Dashboard GPU 区域。只加载当前厂商 GPU 查询，不显示另一厂商的空图。**这是启动/生成配置时的自动选择；每个观测栈当前只支持一个厂商，不是同一 Dashboard 内混合两厂商的动态切换。** 远程 Exporter 或两套工具同时可用时，显式填写 `nvidia` / `hygon`。

多个 GPU 节点使用 external 模式，提前在各节点部署 Exporter：

```json
{
  "gpu": {
    "vendor": "hygon",
    "deployment": "external",
    "nodes": [
      {"name": "dcu-node-a", "gpus": 8, "target": "10.20.0.11:16080", "model": "填写实际海光型号"},
      {"name": "dcu-node-b", "gpus": 8, "target": "10.20.0.12:16080", "model": "填写实际海光型号"}
    ]
  }
}
```

型号与卡序号映射规则：

| 字段 | NVIDIA | 海光 HCU |
| --- | --- | --- |
| `gpu_node` | `gpu.nodes[].name` | `gpu.nodes[].name` |
| `gpu_model` | 优先 `nodes[].model`，否则读取原生 `modelName` | 优先 `nodes[].model`；默认不猜测原生型号字段 |
| `gpu_index` | 原生 `gpu` | 原生 `minor_number` |

Exporter 有其他字段名时，可在 `gpu` 中配置 `"model_label": "实际型号标签名"`、`"index_label": "实际卡序号标签名"`。它们填写指标标签名，不能填写卡序号值。型号仍无法确定时标为 `unspecified`；不把卡序号或设备 ID 当型号。海光建议明确填写每节点实际 `model`；一个节点有多种型号时应依赖 Exporter 每卡的型号标签，不填写统一 `nodes[].model`。缺少原生卡序号标签的指标不会出现在按卡筛选的图表中，应先核对 Exporter 契约。

任务标签表示本栈将该节点纳入任务监控，不表示独占该节点。共享 GPU 节点采集的是设备整体指标，不能将其直接当成该任务的独占利用率。`gpus` 是配置数量，Dashboard 的实际卡选项来自已采集指标。

## 预置图表与实时日志

- vLLM：请求完成速率、输入/输出 Token 吞吐、运行/排队请求、TTFT、端到端延迟、KV Cache、ITL/TPOT、排队时间、抢占速率。
- SGLang：运行/排队请求、输出 Token 吞吐、缓存命中率、TTFT、端到端延迟和版本支持的 Token 间延迟。
- NVIDIA：利用率、已用/空闲显存、显存比例、功率、温度、时钟、XID 及可见卡数。默认 basic 配置避免 profiling 不兼容；显式 full 时增加 profiling 面板，需现场确认支持。
- 海光 HCU：计算利用率、显存已用/容量/比例、显存温度及功耗。指标遵循官方 HCU v3 名称；利用率和功耗的单位未确认时保持原始值，不假设缩放或功率单位。
- 日志：Logs Volume 按级别堆叠显示时间窗内条数，未识别级别归 unknown；日志列表和 Volume 共享任务、角色、副本、文件及“日志包含”筛选。

Dashboard 默认最近 30 分钟、每 5 秒自动刷新。日志行可展开详情查看标签。需要真正的持续 Live 跟踪时，打开 Explore，选择 Benchmark Loki、切换 Code，输入：

```logql
{task="qwen-service", instance_role="decode", replica="decode-0"}
```

点击 Live。Dashboard 的 5 秒轮询与 Explore 的流式 Live 不同；无需进入 Data sources 页面查看日志。推理服务版本未暴露某个指标时会显示 No data，应核对 `/metrics` 指标名称，不把空图理解为数值为零。

## 生成文件与离线更新

主配置仍是自己传给 `--config` 的 site.json。`plan` / `up` 自动生成以下文件，勿在哈希目录里手工修改后期待下次生效：

```text
storage.root/config/<hash>/
├── prometheus/prometheus.yml
├── alloy/config.alloy
├── logrotate/inference.conf
└── grafana/dashboards/task-overview.json
```

通用生成逻辑位于 `scripts/observability_labels.py`、`scripts/task_dashboard.py` 和 `scripts/benchmark_stack.py`。预生成 JSON 在 `configs/common/grafana/dashboards/task-overview-{nvidia,hygon}.json`，便于审阅/导入已接入相同标签的数据源；自动启动只挂载按现场配置生成的一个 task-overview.json。可选 IB 面板仍独立保留。仅更改预生成快照不会改变启动器生成逻辑，修改生成器后用下列命令同步快照：

```bash
python3 scripts/task_dashboard.py --output-dir configs/common/grafana/dashboards
```

**本次标签和 Dashboard 更新只需同步当前源码与配置，不需要重新构建镜像。** 不要只复制 benchmark_stack.py，需同时包含上述新增 Python 模块及 configs/。已有栈先用原配置 down，再修改现场 JSON、重新导出鉴权环境变量并 up：

```bash
# 在离线源码根目录执行；将路径换成当前现场配置。
./bin/llm-bench down --config generated/site/site.json
# 此时更新源码/配置并编辑 site.json；不要覆盖 storage.root/data 或现场 tokenizer。
./bin/llm-bench plan --config generated/site/site.json
./bin/llm-bench up --config generated/site/site.json --no-benchmark
# 需要发压时，再复用观测栈。
./bin/llm-bench bench run --config generated/site/site.json
```

新标签仅作用于重新采集的数据，不回填历史 TSDB/Loki。保留的旧数据仍可用 deployment 查询；选择过渡后的时间范围再使用新任务 Dashboard。旧的生成式 SGLang/GPU 面板由统一任务总览替代，Grafana 中自行保存的自定义面板不会自动重写。

当前仍采用每项目独立 Prometheus/Grafana/Loki 的部署方式；任务下拉只列出该数据源采集到的任务，不自动汇总其他栈。Dashboard UID 固定，不要把两份不同厂商的预生成 JSON 同时导入同一个 Grafana。集成测试覆盖真实 Prometheus、Loki、Grafana 和两种厂商模拟链路；真实 NVIDIA/海光卡及推理服务不同版本的指标覆盖需在现场验证。详见 [本次验证记录](reports/task-observability-2026-11-12.md)。
