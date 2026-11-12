# 配置分层

配置按“通用组件 + GPU 型号/采集适配 + 可选网络 + 环境实例”组合。每次生成一个独立观测栈的配置；`stacks/` 是容器启动与 AISBench 压测入口；`environments/` 保留独立观测配置渲染入口，两者都不会部署真实推理服务。

```text
configs/
├── stacks/                        # Mac 模拟 / NVIDIA 单机完整运行配置
├── sites/                         # GPU41 等现场完整示例，复制后按实际环境修改
├── suites/                        # 按顺序执行的 AISBench 测试例清单
├── search/                        # 自适应 SLO 搜索范围、阈值及预算
├── fixtures/datasets/              # 混合 suite 使用的业务 JSONL 与 ShareGPT 示例
├── fixtures/mock-tokenizer/        # 仅模拟使用的小型 tokenizer
├── common/
│   ├── prometheus/base.json         # 全局采集周期
│   ├── grafana/                     # 服务、provider、统一任务/GPU/日志面板
│   ├── alloy/config.alloy.tmpl      # 日志采集机制
│   ├── loki/loki.yaml               # 单实例日志存储
│   └── logrotate/inference.conf.tmpl
├── gpu/
│   ├── nvidia/
│   │   ├── dcgm/                   # Exporter job 与 GPU Dashboard
│   │   ├── model-agnostic/         # 不限定 NVIDIA 卡型号
│   │   └── h200/profile.json       # 型号到适配器的选择
│   └── hygon/
│       ├── hcu-exporter-v3/        # 基于最新官方文档的参考适配器
│       ├── model-agnostic-hcu-v3/  # 暂不限定型号
│       ├── dcu-exporter-template/  # 待确认的采集与指标契约、接入状态面板
│       └── model-template/         # 复制为实际型号
├── network/infiniband/             # 可选 IB job、规则、Dashboard
└── environments/
    ├── nvidia-h200-3node-8gpu/      # 来源环境，3 × 8 H200，TP=8/PP=3
    ├── hygon-hcu-v3-template/       # 官方 HCU v3，待填现场信息
    └── hygon-template/             # 海光待适配环境
```

## 生成现场 site.json

单实例真实服务以 [gpu41-qwen3-0.6b-random-1k.json](sites/gpu41-qwen3-0.6b-random-1k.json) 为完整示例，步骤见 [GPU41 操作指南](../docs/gpu41-qwen3-random-1k.md)。在工程根目录执行以下命令，按运行时选择一条：

```bash
python3 scripts/create_site_config.py --runtime docker
# 或：python3 scripts/create_site_config.py --runtime nerdctl --namespace benchmark
```

输出 `generated/site/site.json`，已存在时拒绝覆盖；之后编辑该文件，不直接修改 `storage.root/config/<哈希>/` 中的渲染快照。`sites/` 保存具体现场例子，`stacks/` 保留通用模板和模拟模板，`generated/` 为本地文件，不随仓库预置。只切换引擎不会改变示例的接口、路径及 workload；已有栈应先 down 再 up。

## 各层修改边界

| 变化 | 修改位置 |
| --- | --- |
| 容器镜像、存储、客户端 workload、多角色采集入口 | `sites/` 完整示例或 `stacks/` 通用模板，生成到本地 site.json 后修改 |
| 采集周期、日志重试、保留策略、Grafana/Loki 通用设置 | `common/` |
| GPU Exporter 的 job、接口、指标与专属面板 | `gpu/<厂商>/<适配器>/` |
| 型号对应的适配器、验证状态 | `gpu/<厂商>/<型号>/profile.json` |
| IB 等网络指标、规则与面板 | `network/<网络类型>/` |
| 节点数量、卡数、地址、模型、TP/PP、日志路径、服务 URL | llm-bench 修改本地 site.json；旧独立渲染入口修改 `environments/<环境>/environment.json` |

型号可以共享已经确认兼容的适配器；不同型号、驱动或 Exporter 指标不兼容时建立独立适配器目录。不要整套复制 `common/`。网络类型单独选择，不由 GPU 厂商推断；`network: null` 不生成网络 job、规则或面板。

## 环境清单

- `gpu_profile`：相对于 `configs/` 的型号目录；profile 中 `adapter` 同样相对于 `configs/`。
- `nodes`：按 node rank 排序；每项含稳定节点名、独立 GPU Exporter `host:port` 和卡数。模板中的 `gpus: 1` 仅为示意。
- `inference`：SGLang 主节点指标入口、部署名、推理组、模型、TP/PP，以及按 rank 排序的一节点一活跃日志文件。当前仅实现一个推理组、一个副本；其他部署布局需扩展生成器。
- `network`：可为 null；IB 支持 `dns_names` + `port` 或 `targets`，两者都有时使用 DNS。DNS 应指向 headless Service，以逐个发现 Endpoint。
- `services`：Grafana/Alloy 容器实际可达的 Prometheus、Loki URL；示例服务名必须由部署环境提供。
- `status`：`reference` 是来源示例，`template` 是未完成适配。完成现场验证后可记录为 `validated`，并在旁边补充版本和证据；状态本身不构成验证证明。

生成工具仅依赖 Python 3.9+ 标准库。JSON 配置片段由工具组合为完整 Prometheus/Grafana YAML（使用合法的 JSON 表示形式），不是让 Prometheus 自动 include job 片段。运行命令、输出目录挂载与限制见[使用指南](../docs/observability.md)。

AISBench 参数目前放 `stacks/*.json` 的 `benchmark` 字段，详见 [AISBench 指南](../docs/aisbench.md)。新增容器部署请从 [Docker 指南](../docs/docker-stack.md)开始；上文旧环境清单的单组限制不适用于新入口的多角色 targets。

正式 llm-bench 链路的 task / instance_role / replica / gpu_node / gpu_model / gpu_index 标签与统一 Dashboard 见 [任务观测说明](../docs/task-observability.md)。多副本完整模板为 [task-multi-replica.example.json](sites/task-multi-replica.example.json)。`common/grafana/dashboards/task-overview-{nvidia,hygon}.json` 为预生成快照，启动器按当前厂商只生成并挂载一份任务总览；旧 sglang.json、GPU 适配器 gpu.json 继续供历史独立渲染器参考。

nerdctl 模板：`stacks/nerdctl-nvidia-single.json` 用于 Linux 真实单机服务，`stacks/nerdctl-offline-smoke.json` 用于无 GPU 的 amd64 离线链路验证。均显式指定 containerd namespace，见 [nerdctl 指南](../docs/nerdctl.md)。
