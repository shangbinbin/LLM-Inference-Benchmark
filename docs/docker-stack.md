# 配置驱动的容器压测链路

本入口以 AISBench 为唯一发压客户端，启动已有推理服务之外的观测组件。生产模式不会启动或改动被测推理服务；`simulation: true` 才创建明确标识的测试 fixture，以便 Mac 无 GPU 验证。

无公网环境请先按 [离线部署手册](offline-deployment.md)准备完整 amd64 镜像、tokenizer、运行时/驱动与内网配置，并在栈 JSON 中设置 `"offline": true`。未设置时默认保持在线行为。

## Mac 一键模拟

前提：Docker Desktop 已运行，主机有 Python 3.9+，能够拉取镜像及 AISBench 官方源码/依赖。无需 Compose。首次运行会构建缺失的工具与 AISBench 镜像，耗时取决于网络；后续复用镜像。

```bash
python3 scripts/benchmark_stack.py up --config configs/stacks/mac-simulation.json
```

其余操作：

```bash
# 只渲染配置和打印容器计划，不启动容器
python3 scripts/benchmark_stack.py plan --config configs/stacks/mac-simulation.json
# 提前构建客户端/辅助镜像（源码归档有固定提交与 SHA-256）
python3 scripts/benchmark_stack.py build --config configs/stacks/mac-simulation.json
# 只启动观测链路
python3 scripts/benchmark_stack.py up --config configs/stacks/mac-simulation.json --no-benchmark
python3 scripts/benchmark_stack.py status --config configs/stacks/mac-simulation.json
# 删除本项目创建的容器和网络，保留数据/结果/日志
python3 scripts/benchmark_stack.py down --config configs/stacks/mac-simulation.json
```

完整启动使用新的项目状态；已有状态时会拒绝重复启动。更改配置后先 `down`，再执行 `up`。失败时保留容器及日志用于诊断，不继续发压。不要在进程运行中修改配置或执行 down。

## 正式离线模式

在 site.json 顶层设置 `"offline": true`，之后所有命令使用同一个配置文件。该字段也随配置传递到顺序用例和 SLO 搜索，无需每次重新加命令行开关。

```bash
# 检查全部选用镜像的本地存在性和架构，不构建/拉取、不创建容器或网络
python3 scripts/benchmark_stack.py check-images --config /path/to/site.json
python3 scripts/benchmark_stack.py up --config /path/to/site.json
```

`up` 在创建网络/容器前自动执行相同检查，一次列出所有缺失镜像、组件用途和架构错误。相同镜像只检查一次；只检查实际选用厂商的本地 Exporter，simulation/external 不要求本地硬件 Exporter 镜像。`check-images --no-benchmark` 与 `up --no-benchmark` 只要求观测链路镜像；以后运行客户端时仍需 AISBench 镜像。

离线时 build 命令直接拒绝，所有 run/create（包括就绪探测、顺序压测和 SLO 搜索）使用 `--pull=never`。镜像在预检查后被删除也会报错，不会自动拉取。`plan` 只渲染计划，不代替 check-images。

Grafana 自动合并 [离线覆盖配置](../configs/common/grafana/offline.ini)，关闭统计、更新检查、插件预安装/在线管理、签名公钥下载和新闻源；签名校验仍保留。Alloy 关闭统计上报，Loki 保持统计禁用，AISBench 显式使用本地 Hugging Face 数据/模型并禁用其 telemetry。

离线模式保留到推理服务和 Exporter 的内网通信，不自动把生产 Docker 网络改成 internal，也不是主机防火墙。新增插件、远程数据源或更换组件版本时需重新验证其联网行为。已有栈不能原地切换配置：用旧配置 down 后，再修改 offline 并 up，持久化数据保留。

Mac 示例（需已准备 arm64 镜像）：

```bash
python3 scripts/benchmark_stack.py check-images --config configs/stacks/mac-offline.json
python3 scripts/benchmark_stack.py up --config configs/stacks/mac-offline.json
python3 scripts/benchmark_stack.py down --config configs/stacks/mac-offline.json
```

要在独立内部网络中自动验证缺镜像、两种厂商模拟指标、顺序用例及 SLO 搜索，可执行 `python3 docs/examples/verify_offline_mac.py`，见 [正式离线模式验收](reports/mac-offline-mode-2026-11-07.md)。

默认本机入口：Grafana `http://localhost:33000`，Prometheus `http://localhost:39090`，Loki `http://localhost:33100`。端口可在配置中修改，默认仅绑定回环地址。Grafana 初始账号遵循镜像默认值 admin/admin，首次登录修改密码；数据目录会持久化此修改。

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
python3 scripts/benchmark_stack.py plan --config /path/to/site.json
python3 scripts/benchmark_stack.py up --config /path/to/site.json
```

主机需已装 NVIDIA 驱动及 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)。`gpu.vendor: auto` 通过本机可用的设备工具识别厂商；不能唯一识别时会停止，绝不默认当成 NVIDIA。远程指标端点或两厂商工具同时可用时显式指定 `nvidia` / `hygon`。GPU Exporter 的镜像版本在 `images` 中固定，可按目标驱动兼容性更换。

NVIDIA 使用 DCGM 采集/面板，海光使用 HCU v3 采集/面板；选厂商后 job、查询、默认 Exporter 端口与容器参数同时切换。HCU v3 使用官方清单中的 `/dev`、`/opt/hyhal`、`/etc/hostname`、`/etc/vdev` 挂载，需准备宿主机路径与匹配驱动，仍需实机验证。旧 `dcu_*` 不能冒充 `hcu_*`。设备采集不挂 Docker socket，不依赖 Docker 的容器日志接口。

## 配置字段

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 当前为 1 |
| `project` | 独立容器/网络名称前缀；并行栈必须不同 |
| `runtime` | CLI 参数数组，默认 `["docker"]` |
| `offline` | 可选 JSON 布尔值，默认 false；true 时完整本地镜像预检查、禁构建/拉取，并关闭组件已知外联功能 |
| `platform` | 生产固定 linux/amd64；Apple Silicon Mac 模拟 linux/arm64，启动前校验缓存镜像架构 |
| `simulation` | 仅测试时 true；模拟数值不能作为真实性能数据 |
| `storage.root` | 生成配置、组件数据、运行状态、AISBench 输出所在目录 |
| `storage.logs` | 推理服务已写出的日志目录；由 Alloy 只读挂载，由 logrotate 写入 |
| `storage.tokenizer` | 必填本地 tokenizer 目录，两种合成模式的结果汇总都需要；不需要模型权重 |
| `log_files` | 相对 logs 的活跃文件列表，可含 rank/角色子目录；不允许 glob 或路径越界 |
| `gpu.vendor` | auto/nvidia/hygon，当前每个观测栈一个厂商 |
| `gpu.deployment` | local：启动本机一个 Exporter；external：只采集已部署的逐节点 Exporter |
| `gpu.nodes` | 节点名、卡数；external 每个节点还需独立 `target: host:port` |
| `host_metrics` | host：Linux 主机 /proc、/sys、根文件系统只读采集；container：容器/VM 视角（Mac 示例） |
| `node_exporter_target` | host 模式必填宿主机可达 IP:9100；该 exporter 使用 host 网络/PID，以采集真实主机网络指标，其余组件使用隔离网络 |
| `inference` | base_url、model、api（completions/chat）；服务由外部管理 |
| `topology.kind` | single/distributed/pd：场景标识，不自动部署推理拓扑 |
| `topology.metrics_endpoints` | 每个独立采集入口的 target、role、可选 group |
| `topology.tp/pp` | 拓扑标签信息，不启动推理进程 |
| `network` | 可选复用现有 IB 适配；Mac/单机示例不启用 |
| `images` | 各组件镜像；自建镜像可通过 build 更新，使用固定版本而非 latest |
| `ports` | Grafana、Prometheus、Loki 宿主机端口 |
| `startup_timeout` | 每项就绪检查的超时秒数 |
| `benchmark` | AISBench 合成长度、请求数、并发、速率、预热和压力时长，见 [AISBench 指南](aisbench.md) |

路径相对仓库根目录解析；绝对路径也支持。当前启动器在容器引擎所在主机执行，bind mount 源目录必须位于该主机；不能直接把本机生成目录用于远程 Docker context。不要共享同一个 `storage.root` 给不同项目。数据与日志目录独立；单个文件只由一个轮转实例处理。

Prometheus/Grafana/Loki 默认以发起 CLI 的 UID/GID 运行以写入其目录；Alloy 和 logrotate 运行于容器内 root 以读/轮转日志。GPU Exporter 的设备权限只给予该组件。`down` 不删除持久化数据，也不停止任何未带本项目 ownership label 的容器。

## 分布式及多 P 多 D 接口

`inference.base_url` 指向调度器/代理对外入口，AISBench 不直接决定 Prefill/Decode 路由。`topology.metrics_endpoints` 列出 master、router、prefill、decode、worker，按 role/group 附加标签。`gpu.deployment: external` 可采集多台已部署 Exporter；本启动器不通过 SSH 自动跨节点创建容器。

```json
{
  "topology": {
    "kind": "pd",
    "tp": 8,
    "pp": 1,
    "metrics_endpoints": [
      {"target": "router-host:30000", "role": "router", "group": "router"},
      {"target": "prefill-host:30000", "role": "prefill", "group": "p0"},
      {"target": "decode-host:30000", "role": "decode", "group": "d0"}
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
  "log_files": ["prefill/rank-0.log", "decode/rank-0.log"]
}
```

此为合并到完整栈配置的片段。只填写实际存在的指标端点；SGLang 某些 worker 没有独立请求指标入口，不能为补全节点数量虚构端点。多角色请求计数可能重复，应按 role/group 选择统计边界；现有通用应用面板是 SGLang 指标面板，不宣称覆盖所有推理引擎或所有 PD 指标。若日志非共享卷，需要逐节点部署 Alloy/轮转并向同一 Loki 写入，避免多采集器重复读取。

## containerd 与后续 Kubernetes

镜像使用标准 Dockerfile/OCI 运行方式；容器只读取配置文件、挂载数据目录并通过网络通信，没有 Docker-in-Docker 或 Docker socket 依赖。配置与镜像保持分离，可复用到 containerd 下的 Pod。

启动器预留 `runtime: ["nerdctl", "--namespace", "benchmark"]`，使用 Docker 兼容的 run/build/inspect/network 子命令。需配置 containerd、CNI、BuildKit 和设备运行时；本次 Mac 实测是 Docker Desktop，尚未实测裸 containerd/nerdctl。不能把这一 CLI 参数入口等同于 Kubernetes 调度支持。[nerdctl 命令说明](https://github.com/containerd/nerdctl/blob/main/docs/command-reference.md)。

迁移到 Kubernetes：配置目录改为 ConfigMap，数据目录改为 PVC，日志目录为受控 hostPath 或共享 PVC，镜像按 Linux 节点架构构建并推送 registry。GPU Exporter 使用 DaemonSet，服务组件使用 Deployment/StatefulSet，AISBench 使用 Job；为其设置等待依赖就绪的 initContainer。GPU 设备注入使用目标集群的 NVIDIA/Hygon device plugin、RuntimeClass 或已配置的 CDI，不挂载 Docker socket。Kubelet 使用 CRI 与 containerd 交互，不运行宿主机 docker CLI。[Kubernetes runtime 文档](https://kubernetes.io/docs/setup/production-environment/container-runtimes/)、[NVIDIA containerd 配置](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)。

Linux GPU 服务器统一为 x86：生产配置固定 `platform: linux/amd64`，build/run/create/probe 命令显式传递架构，发现缓存镜像架构不符会拒绝启动。AISBench 和 toolbox 的生产标签为 `*-amd64`，Mac 模拟标签为 `*-arm64`，不要互相 retag。推荐在 x86 Linux 主机执行 build；Mac 交叉构建的 amd64 镜像仍需在目标 Linux 验证。Loki 当前为单实例本地存储；日志保留仍需按目标环境配置，logrotate 的 7 天保留不等于 Loki 的保留周期。

## 模拟验收

完成 up 后执行：

```bash
python3 scripts/verify_simulation.py --config configs/stacks/mac-simulation.json --exercise-restart
```

检查所有采集目标 UP、选中厂商指标存在/另一厂商不存在、Grafana 面板与数据源、Loki 请求日志、最新 AISBench 报告成功计数；可选项还会强制轮转模拟日志并重启采集/存储组件，验证旧数据与新日志。证据写入 `storage.root/verification.json`。若修改过 Grafana 密码，先通过环境变量 `BENCH_GRAFANA_PASSWORD` 传入。此工具拒绝操作生产栈。
