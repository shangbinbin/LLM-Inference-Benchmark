# 配置驱动的容器压测链路

推理服务需要 Bearer Token 时，设置 `inference.api_key_env` 并导出对应环境变量；单次、顺序及 SLO 搜索均支持，详见 [推理鉴权说明](inference-auth.md)。离线升级需重新交付 toolbox 镜像和当前源码。

命令入口与安装步骤见 [CLI 快速上手](cli.md#快速上手)。

本入口以 AISBench 为唯一发压客户端，启动已有推理服务之外的观测组件。生产模式不会启动或改动被测推理服务；`simulation: true` 才创建明确标识的测试 fixture，以便 Mac 无 GPU 验证。

无公网环境请先按 [离线部署手册](offline-deployment.md)准备完整 amd64 镜像、tokenizer、运行时/驱动与内网配置，并在栈 JSON 中设置 `"offline": true`。未设置时默认保持在线行为。

任务指标、GPU 与多副本日志的统一标签，以及预置 SGLang/vLLM/GPU/Logs Volume Dashboard，见 [按任务查看指标与日志](task-observability.md)。

## Mac 一键模拟

前提：Docker Desktop 已运行，主机有 Python 3.9+，能够拉取镜像及 AISBench 官方源码/依赖。无需 Compose。首次运行会构建缺失的工具与 AISBench 镜像，耗时取决于网络；后续复用镜像。

```bash
llm-bench up --config configs/stacks/mac-simulation.json
```

其余操作：

```bash
# 只渲染配置和打印容器计划，不启动容器
llm-bench plan --config configs/stacks/mac-simulation.json
# 提前构建客户端/辅助镜像（源码归档有固定提交与 SHA-256）
llm-bench images build --config configs/stacks/mac-simulation.json
# 只启动观测链路
llm-bench up --config configs/stacks/mac-simulation.json --no-benchmark
llm-bench status --config configs/stacks/mac-simulation.json
# 删除本项目创建的容器和网络，保留数据/结果/日志
llm-bench down --config configs/stacks/mac-simulation.json
```

完整启动使用新的项目状态；已有状态时会拒绝重复启动。更改配置后先 `down`，再执行 `up`。失败时保留容器及日志用于诊断，不继续发压。不要在进程运行中修改配置或执行 down。

## 正式离线模式

在 site.json 顶层设置 `"offline": true`，之后所有命令使用同一个配置文件。该字段也随配置传递到顺序用例和 SLO 搜索，无需每次重新加命令行开关。

```bash
# 检查全部选用镜像的本地存在性和架构，不构建/拉取、不创建容器或网络
llm-bench images check --config /path/to/site.json
llm-bench up --config /path/to/site.json
```

`up` 在创建网络/容器前自动执行相同检查，一次列出所有缺失镜像、组件用途和架构错误。相同镜像只检查一次；只检查实际选用厂商的本地 Exporter，simulation/external 不要求本地硬件 Exporter 镜像。`images check --no-benchmark` 与 `up --no-benchmark` 只要求观测链路镜像；以后运行客户端时仍需 AISBench 镜像。

离线时 build 命令直接拒绝，所有 run/create（包括就绪探测、顺序压测和 SLO 搜索）使用 `--pull=never`。镜像在预检查后被删除也会报错，不会自动拉取。`plan` 只渲染计划，不代替 images check。

Grafana 自动合并 [离线覆盖配置](../configs/common/grafana/offline.ini)，关闭统计、更新检查、插件预安装/在线管理、签名公钥下载和新闻源；签名校验仍保留。Alloy 关闭统计上报，Loki 保持统计禁用，AISBench 显式使用本地 Hugging Face 数据/模型并禁用其 telemetry。

离线模式保留到推理服务和 Exporter 的内网通信，不自动把生产 Docker 网络改成 internal，也不是主机防火墙。新增插件、远程数据源或更换组件版本时需重新验证其联网行为。已有栈不能原地切换配置：用旧配置 down 后，再修改 offline 并 up，持久化数据保留。

Mac 示例（需已准备 arm64 镜像）：

```bash
llm-bench images check --config configs/stacks/mac-offline.json
llm-bench up --config configs/stacks/mac-offline.json
llm-bench down --config configs/stacks/mac-offline.json
```

要在独立内部网络中自动验证缺镜像、两种厂商模拟指标、顺序用例及 SLO 搜索，可执行 `python3 docs/examples/verify_offline_mac.py`，见 [正式离线模式验收](reports/mac-offline-mode-2026-11-07.md)。

默认本机入口：Grafana `http://localhost:33000`，Prometheus `http://localhost:39090`，Loki `http://localhost:33100`。端口可在配置中修改，默认绑定所有网卡，可通过节点 IP 访问。Grafana 初始账号遵循镜像默认值 admin/admin，首次登录修改密码；数据目录会持久化此修改。

## 节点 IP 访问与可配置高位端口

默认 `port_bind_address` 为 `0.0.0.0`，所有具备 HTTP 监听接口的项目常驻容器发布到节点网卡，可通过 `http://运行该栈的节点IP:配置端口` 访问。该节点不一定是推理网关所在节点；容器间采集仍使用隔离网络和内部端口。

| 服务 / ports 中的键 | 默认节点端口 | 容器内部端口 | 常用访问路径 |
| --- | --- | --- | --- |
| grafana | 33000 | 3000 | / |
| prometheus | 39090 | 9090 | /targets |
| loki | 33100 | 3100 | /ready（查询使用 Loki API） |
| alloy | 32345 | 12345 | /、/-/ready |
| node-exporter | 39100 | 9100（host 模式直接监听节点配置端口） | /metrics |
| gpu-exporter | 39400 | NVIDIA 9400 / 海光 16080 | /metrics |
| fixture | 38080 | 18000 | /v1/models（仅模拟模式启动） |

AISBench 客户端、logrotate 和临时探测容器没有对外 HTTP 服务，不映射无用途的端口。external 模式的 GPU Exporter 由其他节点部署，不由本栈代理端口。此表是普通模板的默认值；mac-offline 及据其生成的冒烟配置使用 Grafana 33003、Prometheus 39093、Loki 33103、Alloy 32348、node_exporter 39103、GPU Exporter 39403、fixture 38083，减少与生产栈冲突。

可以在 site.json 或 offline-smoke.json 顶层手工配置，省略的 ports 项继承默认值：

```json
"port_bind_address": "0.0.0.0",
"publish_ports": true,
"ports": {
  "grafana": 43000,
  "prometheus": 49090,
  "loki": 43100,
  "alloy": 42345,
  "node-exporter": 49100,
  "gpu-exporter": 49400,
  "fixture": 48080
}
```

port_bind_address 可改为节点的某个 IP（如 10.142.118.41），也支持 IPv6；设为 127.0.0.1 时仅本机访问。端口允许 1..65535，启动器会检查类型及当前实际启动服务之间的配置重复；已经被其他进程占用的端口会在 Docker 创建/启动时报错，应另选端口。

单项设为 0 或 null 可关闭该服务的 Docker 端口映射；`publish_ports=false` 关闭所有 Docker 端口映射，`ports={}` 表示采用默认值，不能用来关闭映射。端口关闭后容器间仍可使用原内部地址通信；完整模拟验收脚本需要 Grafana、Prometheus、Loki 的映射开启。

host_metrics=host 时 node_exporter 使用宿主网络，不添加 Docker -p；其 --web.listen-address 使用 port_bind_address 和 ports.node-exporter，Prometheus 的 node_exporter_target 自动同步使用相同端口。该模式必须配置容器可达的节点主机名/IP；监听地址需为容器可达网卡或 0.0.0.0，不要绑定 127.0.0.1 后仍要求隔离网络的 Prometheus 访问。host 模式监听不受 publish_ports 开关控制，也不能把 node-exporter 端口设为 0/null；如需取消宿主网络监听，使用 container 模式或调整网卡绑定和防火墙。

**所有网卡监听意味着远端可以连接这些服务。** 只在受信任内网放行所需端口；Prometheus、Loki、Exporter 默认没有统一登录鉴权，Grafana 首次使用需修改默认密码。推理服务 Bearer Token 不会给这些新暴露的接口自动加鉴权。

更新源码、修改端口后，先使用原配置正常 down，再用新配置 up；docker restart 不会更新已有映射。无需重建镜像或重启 Docker。可用 `docker ps --format 'table {{.Names}}\t{{.Ports}}'` 查看实际映射；host 模式 node_exporter 直接按配置的监听端口访问。

## 启动依赖

```text
配置校验/渲染 → 离线：全量本地镜像检查；在线：构建缺失的项目镜像 → 创建容器网络
→ 模拟端点（仅模拟）或本机 GPU Exporter（仅真实 local）
→ node_exporter → Loki /ready
→ logrotate → Alloy /-/ready → Prometheus /-/ready → Grafana /api/health
→ 等待所有 Prometheus Targets UP / 核对 GPU 指标族 / 检查被测 /v1/models
→ AISBench 预热及压测 → 保留结果和观测栈
```

就绪检查在同一容器网络中执行，检查 HTTP 结果而不是只等待进程创建。任一检查超时即失败。AISBench 非零退出、报告缺失、失败请求或 perf 完成数不符均判定任务失败；报告中的性能/SLO 是否达标仍需结合业务标准判断。

## Linux NVIDIA 单机

复制 [nvidia-single.json](../configs/stacks/nvidia-single.json)，填写推理服务地址、模型名称和存储路径。实际发压入口是 `inference.base_url`（服务根 URL，不含 `/v1/completions`），指标入口单独填写。服务必须可从 Docker 网络访问；不要把容器内 `localhost` 当成宿主机。可使用宿主机实际局域网 IP。

```bash
llm-bench plan --config /path/to/site.json
llm-bench up --config /path/to/site.json
```

主机需已装 NVIDIA 驱动及 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)。`gpu.vendor: auto` 通过本机可用的设备工具识别厂商；不能唯一识别时会停止，绝不默认当成 NVIDIA。远程指标端点或两厂商工具同时可用时显式指定 `nvidia` / `hygon`。GPU Exporter 的镜像版本在 `images` 中固定，可按目标驱动兼容性更换。

NVIDIA 使用 DCGM 采集/面板，海光使用 HCU v3 采集/面板；选厂商后 job、查询、默认 Exporter 端口与容器参数同时切换。HCU v3 使用官方清单中的 `/dev`、`/opt/hyhal`、`/etc/hostname`、`/etc/vdev` 挂载，需准备宿主机路径与匹配驱动，仍需实机验证。旧 `dcu_*` 不能冒充 `hcu_*`。设备采集不挂 Docker socket，不依赖 Docker 的容器日志接口。

## 配置字段

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 当前为 1 |
| `project` | 独立容器/网络名称前缀；并行栈必须不同 |
| `task` | 推理任务标签，指标/GPU/日志共用；省略时使用 project |
| `runtime` | CLI 参数数组，默认 `["docker"]`；nerdctl 需显式 --namespace NAME |
| `runtime_kind` | 可选 docker/nerdctl；普通命令自动识别，自定义 nerdctl 包装器需明确指定 |
| `offline` | 可选 JSON 布尔值，默认 false；true 时完整本地镜像预检查、禁构建/拉取，并关闭组件已知外联功能 |
| `platform` | 生产固定 linux/amd64；Apple Silicon Mac 模拟 linux/arm64，启动前校验缓存镜像架构 |
| `simulation` | 仅测试时 true；模拟数值不能作为真实性能数据 |
| `storage.root` | 生成配置、组件数据、运行状态、AISBench 输出所在目录 |
| `storage.logs` | 推理服务已写出的日志目录；由 Alloy 只读挂载，由 logrotate 写入 |
| `storage.tokenizer` | 必填本地 tokenizer 目录，两种合成模式的结果汇总都需要；不需要模型权重 |
| `log_sources` | 推荐：每项 path、role、replica、可选 node；统一生成采集及轮转清单 |
| `log_files` | 兼容旧活跃文件列表；设置 log_sources 时忽略此项；不允许 glob 或路径越界 |
| `gpu.vendor` | auto/nvidia/hygon，当前每个观测栈一个厂商 |
| `gpu.nvidia_metrics_profile` | basic（默认）：只读挂载 8 项基础指标列表，避开 profiling；full：使用镜像默认集合，仅在现场已验证兼容时开启 |
| `gpu.deployment` | local：启动本机一个 Exporter；external：只采集已部署的逐节点 Exporter |
| `gpu.nodes` | 节点 name、gpus、可选实际 model；external 还需独立 target: host:port |
| `gpu.model_label` / `gpu.index_label` | 可选 Exporter 型号/卡序号原生标签名；NVIDIA 默认 modelName/gpu，海光默认只映射 minor_number，型号建议填 nodes[].model |
| `host_metrics` | host：Linux 主机 /proc、/sys、根文件系统只读采集；container：容器/VM 视角（Mac 示例） |
| `node_exporter_target` | host 模式必填宿主机可达 IP；端口由 ports.node-exporter 同步，默认 39100；该 exporter 使用 host 网络/PID，以采集真实主机网络指标，其余组件使用隔离网络 |
| `inference` | base_url、model、api（completions/chat）；engine 支持 auto/vllm/sglang，服务由外部管理 |
| `topology.kind` | single/distributed/pd：场景标识，不自动部署推理拓扑 |
| `topology.metrics_endpoints` | 每个独立采集入口的 target、role、replica、可选 group；勿重复采集同一统计边界 |
| `topology.tp/pp` | 拓扑标签信息，不启动推理进程 |
| `network` | 可选复用现有 IB 适配；Mac/单机示例不启用 |
| `images` | 各组件镜像；自建镜像可通过 build 更新，使用固定版本而非 latest |
| `ports` | 各 HTTP 服务的节点端口，缺省项使用高位默认值；0/null 关闭单项映射 |
| `port_bind_address` | 默认 0.0.0.0；可指定节点 IP 或 127.0.0.1 |
| `publish_ports` | 默认 true；false 关闭 Docker 端口映射（host node_exporter 的直接监听除外） |
| `startup_timeout` | 每项就绪检查的超时秒数 |
| `benchmark` | AISBench 合成长度、请求数、并发、速率、预热和压力时长，见 [AISBench 指南](aisbench.md) |

路径相对仓库根目录解析；绝对路径也支持。当前启动器在容器引擎所在主机执行，bind mount 源目录必须位于该主机；不能直接把本机生成目录用于远程 Docker context。不要共享同一个 `storage.root` 给不同项目。数据与日志目录独立；单个文件只由一个轮转实例处理。

Prometheus/Grafana/Loki 默认以发起 CLI 的 UID/GID 运行以写入其目录；Alloy 和 logrotate 运行于容器内 root 以读/轮转日志。GPU Exporter 的设备权限只给予该组件。`down` 不删除持久化数据，也不停止任何未带本项目 ownership label 的容器。

## 分布式及多 P 多 D 接口

`inference.base_url` 指向调度器/代理对外入口，AISBench 不直接决定 Prefill/Decode 路由。`topology.metrics_endpoints` 列出 master、router、prefill、decode、worker，按 task/instance_role/replica 附加标签。`gpu.deployment: external` 可采集多台已部署 Exporter；本启动器不通过 SSH 自动跨节点创建容器。

```json
{
  "topology": {
    "kind": "pd",
    "tp": 8,
    "pp": 1,
    "metrics_endpoints": [
      {"target": "router-host:30000", "role": "router", "replica": "router-0"},
      {"target": "prefill-host:30000", "role": "prefill", "replica": "prefill-0"},
      {"target": "decode-host:30000", "role": "decode", "replica": "decode-0"}
    ]
  },
  "gpu": {
    "vendor": "nvidia",
    "deployment": "external",
    "nodes": [
      {"name": "prefill-host", "gpus": 8, "target": "prefill-host:9400"},
      {"name": "decode-host", "gpus": 8, "target": "decode-host:9400"}
    ]
  },
  "task": "example-inference-task",
  "log_sources": [
    {"path": "prefill/rank-0.log", "role": "prefill", "replica": "prefill-0"},
    {"path": "decode/rank-0.log", "role": "decode", "replica": "decode-0"}
  ]
}
```

此为合并到完整栈配置的片段。只填写实际存在的指标端点；SGLang 某些 worker 没有独立请求指标入口，不能为补全节点数量虚构端点。多角色请求计数可能重复，需仅配置统计边界不重复的端点；统一总览中的 SGLang/vLLM 性能面板按任务聚合，不自动去重，也不宣称覆盖所有 PD 指标。若日志非共享卷，需要逐节点部署 Alloy/轮转并向同一 Loki 写入，避免多采集器重复读取。

## containerd 与后续 Kubernetes

镜像使用标准 Dockerfile/OCI 运行方式；容器只读取配置文件、挂载数据目录并通过网络通信，没有 Docker-in-Docker 或 Docker socket 依赖。配置与镜像保持分离，可复用到 containerd 下的 Pod。

启动器已实现 `runtime: ["nerdctl", "--namespace", "benchmark"]` 适配，包含 hostname 网络解析、明确的 inspect 模式/平台、NVIDIA --gpus 参数及 namespace 清理保护。镜像导入、Linux 冒烟模板与验证边界见 [nerdctl 部署指南](nerdctl.md)。只运行现成镜像不需要 BuildKit；在线构建才需要。此入口管理独立容器，不等同于 Kubernetes 调度支持。[nerdctl 命令说明](https://github.com/containerd/nerdctl/blob/main/docs/command-reference.md)。

迁移到 Kubernetes：配置目录改为 ConfigMap，数据目录改为 PVC，日志目录为受控 hostPath 或共享 PVC，镜像按 Linux 节点架构构建并推送 registry。GPU Exporter 使用 DaemonSet，服务组件使用 Deployment/StatefulSet，AISBench 使用 Job；为其设置等待依赖就绪的 initContainer。GPU 设备注入使用目标集群的 NVIDIA/Hygon device plugin、RuntimeClass 或已配置的 CDI，不挂载 Docker socket。Kubelet 使用 CRI 与 containerd 交互，不运行宿主机 docker CLI。[Kubernetes runtime 文档](https://kubernetes.io/docs/setup/production-environment/container-runtimes/)、[NVIDIA containerd 配置](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)。

Linux GPU 服务器统一为 x86：生产配置固定 `platform: linux/amd64`，build/run/create/probe 命令显式传递架构，发现缓存镜像架构不符会拒绝启动。AISBench 和 toolbox 的生产标签为 `*-amd64`，Mac 模拟标签为 `*-arm64`，不要互相 retag。推荐在 x86 Linux 主机执行 build；Mac 交叉构建的 amd64 镜像仍需在目标 Linux 验证。Loki 当前为单实例本地存储；日志保留仍需按目标环境配置，logrotate 的 7 天保留不等于 Loki 的保留周期。

## 模拟验收

完成 up 后执行：

```bash
python3 scripts/verify_simulation.py --config configs/stacks/mac-simulation.json --exercise-restart
```

检查所有采集目标 UP、选中厂商指标存在/另一厂商不存在、Grafana 面板与数据源、Loki 请求日志、最新 AISBench 报告成功计数；可选项还会强制轮转模拟日志并重启采集/存储组件，验证旧数据与新日志。证据写入 `storage.root/verification.json`。若修改过 Grafana 密码，先通过环境变量 `BENCH_GRAFANA_PASSWORD` 传入。此工具拒绝操作生产栈。

旧 Docker 环境若最小 Python 线程测试失败、且仅在关闭 seccomp 后成功，可在 stack JSON 顶层显式设置 `"legacy_seccomp_unconfined": true`。离线模式默认开启、在线模式默认关闭，显式设 false 可关闭；开启后统一影响本工程新建的所有容器，包括监控组件、Exporter 和临时探测，不影响其他项目；适用范围、安全影响、重建容器及恢复步骤见 [旧 Docker 线程兼容说明](legacy-docker-threads.md)。

网关路径可统一配置 `inference.base_url`（如 `http://host:port/prefix/`），默认自动追加 `v1/chat/completions`、`v1/models` 和 `metrics`，应用指标默认复用推理 Bearer 鉴权；保留显式指标字段时以覆盖值为准。详见 [统一基础 URL](inference-auth.md#统一基础-url自动拼接推理与指标接口)。
