# 修改 site.json 后启动压测：NVIDIA / 海光，Docker / nerdctl

本手册适用于已经交付源码、加载 amd64 镜像、解压真实 tokenizer，并且推理服务已经部署的离线 Linux 节点。目标是修改一份完整 `site.json`，依次完成观测启动、小规模验证、正式压测、结果查询和停止。推理服务可以是同机 Pod、独立容器或远端服务；本工程不会启动、停止或修改真实推理服务。

尚未准备交付物时见 [在线 CPU → 离线 GPU](online-cpu-to-offline-gpu.md)；尚未验证软件链路时先完成 [自带 tokenizer 冒烟](offline-deployment.md#offline-smoke)。模拟冒烟使用 `generated/offline-smoke.json`，真实服务使用 `generated/site/site.json`，两者的状态与网络独立。

现场阅读顺序：[NVIDIA/海光改动](#2-nvidia-与海光需要改什么) → [字段含义](#3-sitejson-各配置项的含义与修改要求) → [修改示例](#4-一次性修改单实例配置的示例) → [启动压测](#6-鉴权预检查启动和单档压测) → [多测试例](#7-多个不同输入输出请求数和并发的测试例)。

## 1. 执行位置与配置文件

以下命令都在离线机的**源码根目录**执行。路径换成实际安装位置，不进入 generated/ 或 site/ 执行：

```bash
cd /home/llm-bench-runtime/LLM-Inference-Benchmark
test -f scripts/benchmark_stack.py
./bin/llm-bench --help
```

| 文件 | 作用 | 是否手动修改 |
| --- | --- | --- |
| `generated/site/site.json` | 完整现场输入，包含服务、运行时、设备、日志及默认 workload | 是，本手册修改对象 |
| `generated/offline-smoke.json` | 使用模拟服务的独立冒烟输入 | 仅模拟验证时修改 |
| `storage.root/config/<哈希>/manifest.json` | up 保存的生效配置快照 | 否，交给启动器生成 |
| `storage.root/state.json` | 本项目容器、网络和状态 | 否，不靠删除它绕过启动检查 |
| `generated/site/cases.json` | 可选多测试例清单 | 是，每例覆盖 workload |

如果已有 `generated/site/site.json`，直接编辑。没有时选一条生成命令，默认从 GPU41 完整示例复制，已有输出会拒绝覆盖：

```bash
# Docker
python3 scripts/create_site_config.py --runtime docker
```

```bash
# 或 nerdctl，namespace 必须与镜像导入一致
python3 scripts/create_site_config.py --runtime nerdctl --namespace benchmark
```

该生成器不联网、不访问模板里的真实地址，也不会启动容器；GPU41 的接口和 NFS 路径只是示例，不会自动转换成当前节点路径。省略 --runtime 时沿用源文件设置。自定义底稿可加 `--source 完整配置.json --output 另一现场配置.json`。

**已有栈时先处理旧状态，再修改。** 如果此前已经成功 up 或失败后保留了部分容器，先检查并停止原栈：

```bash
./bin/llm-bench status --config generated/site/site.json
# 确认没有正在执行的压测后，再停止原观测栈
./bin/llm-bench down --config generated/site/site.json
```

首次部署、提示 No active stack 时无需 down。project/storage.root 在 down 前应保持原值，便于定位旧栈；down 读取原 manifest 中的 runtime/namespace，不会停止真实推理服务。不要为了切换厂商或引擎直接删除 state.json、网络或整个 namespace。

## 2. NVIDIA 与海光需要改什么

GPU 厂商与容器引擎分别选择，可以组合为 Docker+NVIDIA、nerdctl+NVIDIA、Docker+海光、nerdctl+海光。海光 DCU 在配置中的厂商值是 **hygon**，不要填写 dcu。

| 对比项 | NVIDIA | 海光 DCU |
| --- | --- | --- |
| `gpu.vendor` | `nvidia` | `hygon` |
| 实际镜像字段 | `images.nvidia_exporter` | `images.hygon_exporter` |
| 当前默认镜像 | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nvidia/dcgm-exporter:4.8.4` | `harbor.sourcefind.cn:5443/hcu/admin/base/hcu-exporter:v3.0.0` |
| 节点设备检查 | `nvidia-smi` | `hy-smi` |
| Docker 本机采集参数 | 启动器使用已注册的 nvidia runtime、设备环境变量和 SYS_ADMIN | 启动器使用 privileged、海光设备/运行库挂载及 HCU 参数 |
| nerdctl 本机采集参数 | 启动器使用 --gpus all，依赖可用的 nvidia-container-cli/Toolkit | 启动器使用海光设备/运行库挂载，不配置 NVIDIA runtime |
| 本机设备依赖 | 匹配驱动和 NVIDIA Container Toolkit | 匹配驱动及 `/dev`、`/opt/hyhal`、`/etc/hostname`、`/etc/vdev` 等 HCU v3 所需路径 |
| 内部 metrics 端口 | 9400 | 16080 |
| 默认发布到节点端口 | 39400，由 ports.gpu-exporter 控制 | 同样 39400，映射到海光内部端口 |
| 代表指标 / job | DCGM_FI_DEV_GPU_UTIL / nvidia-dcgm-exporter | hcu_memorycap_bytes / hygon-hcu-exporter，容量单位 bytes |
| 原生型号 / 卡序号标签 | 默认 modelName / gpu | 默认卡序号 minor_number；建议填写 nodes[].model 为实际型号 |
| Grafana | NVIDIA 任务总览 | 海光任务总览 |

运行时配置示例：

| 引擎 | 顶层 runtime |
| --- | --- |
| Docker | `["docker"]` |
| nerdctl | `["nerdctl", "--namespace", "benchmark"]` |

默认交付包备齐两类 Exporter 共 9 个镜像，Docker/nerdctl load 会全部导入；本机真实 local 部署只使用 7 个通用镜像和**选中厂商的 1 个 Exporter**。切换 vendor 会切换设备容器、采集规则和面板，不会同时启动两个 Exporter。AISBench/toolbox 是 CPU 镜像，无需为厂商切换重建。

Docker 与 containerd 的镜像存储独立，nerdctl 不同 namespace 也不自动共享镜像名称。若要使用 k8s.io 中已有镜像，可以显式填写该 namespace，或将交付包导入独立 benchmark namespace；本项目仍只管理自己创建的独立容器。rootful 权限、CNI 及 1.7.7 要求见 [nerdctl 指南](nerdctl.md)。

**海光默认是 HCU Exporter v3 的指标契约。** 如果现场已有旧版 dcu-exporter 2.4.1/2.4.2，不能仅替换 images.hygon_exporter 就认定兼容；先核对 /metrics、卡序号/型号标签、指标名与单位，再完成适配。external 模式同样不会自动把 dcu_* 转成 hcu_*。其他 GPU 的镜像交付入口不等于运行支持，见 [扩展入口](online-cpu-to-offline-gpu.md#51-其他-gpu-的扩展入口)。

## 3. site.json 各配置项的含义与修改要求

下面的“必须核对”表示要与实际环境一致，不一定每次都要改值。普通单实例优先保留模板的结构，不加入未被程序读取的自定义字段。

### 3.1 项目、运行时与离线设置

| 字段 | 含义 | 需要怎样修改 |
| --- | --- | --- |
| `schema_version` | 配置版本 | 保持整数 1 |
| `project` | 容器、网络、ownership 名称前缀 | 节点上唯一；小写字母开头，仅小写字母/数字/短横线，最多 40 字符 |
| `task` | 应用指标、GPU、日志共用的业务任务标签 | 建议显式填稳定任务名；省略使用 project；不同模型/任务按需要区分 |
| `runtime` | 所选 CLI 及全局参数数组 | 按 Docker/nerdctl 选择，nerdctl 明确 namespace；socket/snapshotter/sudo 参数也放这里 |
| `runtime_kind` | 自定义包装器的引擎类型 | 普通 docker/nerdctl 无需设置；包装脚本显式 docker/nerdctl，并保证真实参数透传 |
| `platform` | 容器平台 | 真实生产节点保持 linux/amd64 |
| `simulation` | 是否使用内置模拟服务/设备指标 | 对接真实服务必须 false；不会因此启动模型引擎 |
| `offline` | 禁构建/拉取并关闭已知自动外联 | 离线保持 true；镜像缺失时报错，不在线补拉 |
| `legacy_seccomp_unconfined` | 关闭本项目新容器的 seccomp 系统调用过滤 | 离线默认 true，兼容线程创建问题；显式 false 可恢复；不改系统策略或其他项目 |
| `startup_timeout` | 每项就绪探测超时，单位秒 | 示例 180；不是整场 benchmark 的最长时间 |
| `metrics_retention` | Prometheus 数据保留时长 | 可省略，默认 7d；不控制 Loki、原始文件日志或 AISBench 报告保留 |

布尔值使用 JSON 的 true/false，不写成字符串。标签值应稳定且简短，不能含控制字符、双引号、反斜杠、反引号；不要把请求 ID 或时间戳作为 task。

### 3.2 存储与日志

| 字段 | 含义 | 需要怎样修改 |
| --- | --- | --- |
| `storage.root` | 状态、配置快照、观测数据和结果根目录 | 必须可写，每项目独立，如 generated/qwen-real；不能等于 storage.logs |
| `storage.tokenizer` | 客户端只读挂载为 /tokenizer 的真实 tokenizer | 必须匹配被测模型，如 assets/tokenizer；含 tokenizer_config.json 和所需词表，不需要模型权重 |
| `storage.logs` | 节点上真实服务已经持续写入的日志目录 | 必须填实际可访问目录，如 /srv/inference-logs；不会自动去 Pod 内拉取日志 |
| `log_sources[].path` | 日志目录下的具体活跃文件 | 相对 storage.logs，如 server_log.log；禁止绝对路径、通配符、.. 和重复文件 |
| `log_sources[].role` | 实例角色 | 例如 master/worker/router/prefill/decode；生成 Loki instance_role 标签 |
| `log_sources[].replica` | 同角色实例的副本名 | 例如 replica-0；多副本分别配置，不随单次请求变化 |
| `log_sources[].node` | 可选日志来源节点标签 | 填真实稳定节点名；不会据此自动远程挂载 |
| `log_files` | 旧版活跃文件清单 | 新配置推荐 log_sources；设置 log_sources 后该旧字段被忽略 |

storage 的相对路径按**工程根目录**解析，不按 site.json 所在目录。bind mount 的源目录必须对运行容器的节点可见。logrotate 会写入/轮转指定日志，目录应有适当权限，避免与现场已有轮转机制同时操作同一文件；不要创建空文件冒充真实服务日志。多节点日志需先通过共享目录或另行部署采集器汇集，节点名标签本身不会传输文件。

### 3.3 推理服务、指标地址与鉴权

| 字段 | 含义 | 需要怎样修改 |
| --- | --- | --- |
| `inference.base_url` | 真实服务根 URL，可以包含网关前缀 | 必须改成容器可达地址；不带 v1/chat/completions、v1/completions、metrics 后缀 |
| `inference.model` | API 接受的模型 ID | 必须与服务一致，不是随意填写的展示名 |
| `inference.api` | OpenAI 兼容协议类型 | chat 或 completions；服务需支持所选流式接口 |
| `inference.engine` | 观测描述标签 | vllm/sglang/auto；不部署、不调整实际引擎 |
| `inference.api_key_env` | Bearer Token 环境变量名 | 需鉴权才填，如 INFERENCE_API_KEY；无鉴权删除该字段；不写实际 Token 或 api_key |
| `topology.kind` | 单实例/分布式/PD 场景描述 | 普通实例 single；distributed/pd 需按真实拓扑配置多个采集入口 |
| `topology.tp` / `pp` | 模型并行标签 | 按真实部署填写，不控制推理进程启动 |
| `topology.metrics_endpoints` | 可选独立应用指标入口列表 | 默认省略，从 base_url 派生；显式时 target 填 host:port，不带协议/路径 |
| `metrics_endpoints[].role/replica/group` | 应用指标入口的角色、副本与分组 | 按实际实例填写，避免重复采集同一请求统计边界；位于 topology 内 |
| `topology.metrics_path` | 可选应用 metrics 的绝对 URL 路径 | 默认 base_url 路径前缀 + /metrics；同一列表共用该路径 |
| `topology.metrics_scheme` | 可选应用指标协议 | 默认继承 base_url；覆盖时 http 或 https |
| `topology.metrics_api_key_env` | 可选独立 metrics 鉴权 | 默认继承 inference.api_key_env；另一变量名可覆盖，null 表示应用指标不带 Bearer Token |

例如 base_url 为 `http://10.142.118.41:38000/publishaddress/inference/qwen306b/`，会派生：

| 用途 | 路径 |
| --- | --- |
| chat 请求 | /publishaddress/inference/qwen306b/v1/chat/completions |
| 模型就绪检查 | /publishaddress/inference/qwen306b/v1/models |
| 应用指标 | /publishaddress/inference/qwen306b/metrics |

换模型服务时同步改 URL、model、tokenizer、日志和 task。使用自动派生时删除旧的 topology.metrics_endpoints、metrics_path、metrics_scheme、metrics_api_key_env 覆盖项，尤其是其中的 REPLACE_ 占位符。网关必须转发 models、流式 API 和 metrics；models 能返回不能代替 metrics 验收。完整鉴权与独立 metrics 示例见 [鉴权说明](inference-auth.md)。

### 3.4 GPU、主机采集与网络

| 字段 | 含义 | 需要怎样修改 |
| --- | --- | --- |
| `gpu.vendor` | 当前栈的 GPU 厂商 | NVIDIA 填 nvidia，海光填 hygon；auto 仅在本机能唯一识别厂商时可用，离线建议显式选择 |
| `gpu.deployment` | 设备 Exporter 的部署位置 | local 启动本机一个 Exporter；external 抓取已有逐节点 Exporter，不启动本机设备采集容器 |
| `gpu.nodes[].name/gpus` | GPU 节点名和实际卡数 | 必须核对；local 只配置一个节点，多节点用 external |
| `gpu.nodes[].model` | 可选实际设备型号标签 | 海光建议填写；无型号映射时面板显示 unspecified，不能编造型号 |
| `gpu.nodes[].target` | external 的设备指标入口 | external 必填每节点独立的 host:port；如已发布到节点 39400，应填实际节点IP:39400，不能照抄内部端口 |
| `gpu.model_label/index_label` | 可选 Exporter 原生型号/卡序号标签名 | 按 /metrics 实际标签设置；它们不能转换旧指标名或单位 |
| `gpu.nvidia_metrics_profile` | NVIDIA basic/full collectors | NVIDIA 默认保留 basic；full 需确认 profiling 支持；海光不使用此字段 |
| `host_metrics` | 主机指标采集视角 | container 为容器视角；host 通过主机 /proc、/sys、根目录及宿主网络采集 |
| `node_exporter_target` | host 模式节点的可达地址 | host 必填实际 IP/主机名，端口随 ports.node-exporter；不能用容器 localhost；container 通常不填 |
| `network` | 可选已适配的 IB 等网络采集对象 | 常规单机省略或 null；需要时按已有适配器配置，不由 GPU 厂商自动开启 |

一个栈当前对应一个 GPU 厂商，不能在同一 gpu.nodes 列表混配 NVIDIA 与海光。external 仍要求对应指标契约，target 可达并不证明指标正确。使用本机海光 HCU v3 时，设备路径和权限由该 Exporter 需要；真实硬件部署与软件模拟是不同验收范围。

### 3.5 镜像、访问端口

| 字段 | 含义 | 需要怎样修改 |
| --- | --- | --- |
| `images.toolbox/aisbench` | 已交付的探测/轮转工具和压测客户端 | 必须在所选 runtime/namespace 中存在、架构 linux/amd64；仅更改标签不会更新镜像内容 |
| `images.prometheus/grafana/loki/alloy/node-exporter` | 通用观测镜像 | 保留交付清单中的精确标签，版本变化需验证 |
| `images.nvidia_exporter/hygon_exporter` | 两类设备 Exporter 的镜像引用 | 默认可同时保留，按 gpu.vendor 选择一个启动；已有 external 不使用本机镜像 |
| `port_bind_address` | 节点发布地址 | 默认 0.0.0.0，可改指定 IP 或 127.0.0.1；后者仅本机访问 |
| `publish_ports` | 是否发布容器服务端口 | 默认 true；false 关闭端口映射，但 host node_exporter 仍按其配置直接监听 |
| `ports` | 各服务的节点端口 | 按下表修改，避开已有服务；0/null 关闭单项映射，空对象表示沿用默认值 |

| ports 中的键 | 默认节点端口 | 常用用途 |
| --- | --- | --- |
| grafana | 33000 | 浏览器面板 |
| prometheus | 39090 | /targets、查询 |
| loki | 33100 | Loki API；实时查看使用 Grafana Explore |
| alloy | 32345 | /、/-/ready |
| node-exporter | 39100 | /metrics；host 模式直接监听 |
| gpu-exporter | 39400 | /metrics；local 模式映射到所选厂商内部端口 |
| fixture | 38080 | 仅 simulation=true 启动；真实模式不启动 fixture |

AISBench、logrotate 和探测容器没有对外 HTTP 服务。不要用修改 Grafana 数据源 URL 来修复宿主端口冲突；容器间使用渲染后的内部地址，浏览器才使用运行观测栈的节点 IP + 发布端口。详见 [端口规则](docker-stack.md#节点-ip-访问与可配置高位端口)。

### 3.6 benchmark：压测负载

| 字段 | 含义 | 需要怎样修改 |
| --- | --- | --- |
| `dataset_type` | 合成数据方式 | 随机 tokenizer 输入用 tokenid；string 为重复字符串，输入 token 口径不同；两者性能汇总均需要真实 tokenizer |
| `input_length` | 合成输入长度参数 | 正整数；chat 模板可能增加服务实际 prompt tokens |
| `output_length` | 请求输出长度上限 | 正整数，后端可能提前结束；实际长度以报告为准 |
| `prefix_length` | tokenid 的共享前缀长度 | 0 到 input_length；无共享前缀基线用 0 |
| `request_count` | perf 模式正式请求数 | 如 1000；与预热分开统计，不等于并发 |
| `concurrency` | 本例最大并发 | 如 16；不是总请求数或每秒请求数 |
| `request_rate` | 请求速率限制参数 | 默认 0，不额外限速；可按所需 QPS 设置，仍受并发和服务处理能力约束 |
| `warmups` | AISBench 预热数量 | 0 表示无额外预热；预热不计入正式请求目标 |
| `retry` | 请求总尝试次数 | 当前固定 AISBench 版本用 1 表示不重试，不是首次失败后再重试 1 次 |
| `mode` | perf 或 pressure | 固定请求数验收用 perf；持续压力用 pressure |
| `pressure_seconds` | pressure 的持续时间 | pressure 必填正数秒数；该模式不按 perf 的固定完成数规则解释 |
| `generation_kwargs` | 传给模型后端的生成参数 | 如 temperature=0、ignore_eos=true；只填写服务支持项，不能保证服务一定生成满输出上限 |
| `concurrency_levels/requests_per_level` | 可选原生多档扫描数组 | 普通单档/独立 suite 建议删除；扫描须在 perf 模式成对配置，详见 AISBench 指南 |

普通随机 1k/1k、1000 次、并发 16 可用 1024/1024/1000/16。服务上下文容量应容纳输入、chat 模板与输出。tokenid 在本地生成随机输入，不需要预先生成数据集；JSONL/ShareGPT 用单独 cases.json 的 dataset 入口。simulation_latency 是仅供模拟测试的容量/过载延迟对象，真实服务配置省略。长度口径与报告解释见 [AISBench 指南](aisbench.md)。

## 4. 一次性修改单实例配置的示例

已有运行栈先完成第 1 节 down。下面脚本用于**单实例、本机 Exporter、应用指标与 API 共用 base_url**；先将开头各值改成实际环境，再执行。它保留 images 和 ports，但替换 runtime、服务、GPU、日志和默认 benchmark；自定义包装器或独立指标拓扑请按第 3 节直接编辑，不用此脚本覆盖。

```bash
python3 - <<'PY'
import json
from pathlib import Path

GPU_VENDOR = 'nvidia'       # 海光 DCU 改为 'hygon'
CONTAINER_ENGINE = 'docker' # 使用 nerdctl 改为 'nerdctl'
NAMESPACE = 'benchmark'
NODE_NAME = 'gpu41'         # 改实际节点名；海光例如 dcu-node
GPU_COUNT = 1              # 改实际卡数
GPU_MODEL = ''             # 可填实际型号；尤其建议海光填写，不知道时留空
SERVICE_BASE_URL = 'http://10.142.118.41:38000/publishaddress/inference/qwen306b/'
MODEL_ID = 'Qwen3-0.6B'
API = 'chat'               # 或 completions，按服务接口
INFERENCE_ENGINE = 'vllm'   # 或 sglang/auto，作为观测标签
TP, PP = 1, 1              # 改成真实部署并行信息
TOKENIZER_DIR = 'assets/tokenizer'
LOG_DIR = '/srv/inference-logs'
LOG_FILE = 'server_log.log'
AUTH_ENV = 'INFERENCE_API_KEY' # 不需要鉴权则改为 ''；这里只填变量名
PROJECT = 'llm-bench-qwen-real'
TASK = 'qwen3-0.6b'
DATA_DIR = 'generated/qwen-real'

if GPU_VENDOR not in ('nvidia', 'hygon') or CONTAINER_ENGINE not in ('docker', 'nerdctl'):
    raise SystemExit('GPU_VENDOR 使用 nvidia/hygon，CONTAINER_ENGINE 使用 docker/nerdctl')
path = Path('generated/site/site.json')
cfg = json.loads(path.read_text())
cfg.update(project=PROJECT, task=TASK, simulation=False, offline=True,
           platform='linux/amd64', legacy_seccomp_unconfined=True,
           host_metrics='container', startup_timeout=180)
cfg['runtime'] = ['docker'] if CONTAINER_ENGINE == 'docker' else ['nerdctl','--namespace',NAMESPACE]
cfg.pop('runtime_kind', None)
cfg.pop('node_exporter_target', None)  # 本例使用 container，移除旧 host 占位地址
cfg['storage'] = {'root':DATA_DIR, 'logs':LOG_DIR, 'tokenizer':TOKENIZER_DIR}
node = {'name':NODE_NAME, 'gpus':GPU_COUNT}
if GPU_MODEL:
    node['model'] = GPU_MODEL
cfg['gpu'] = {'vendor':GPU_VENDOR, 'deployment':'local', 'nodes':[node],
              'nvidia_metrics_profile':'basic'}
cfg['inference'] = {'base_url':SERVICE_BASE_URL, 'model':MODEL_ID, 'api':API, 'engine':INFERENCE_ENGINE}
if AUTH_ENV:
    cfg['inference']['api_key_env'] = AUTH_ENV
cfg['topology'] = {'kind':'single', 'tp':TP, 'pp':PP}
cfg['log_sources'] = [{'path':LOG_FILE, 'role':'master', 'replica':'replica-0', 'node':NODE_NAME}]
cfg['benchmark'] = {'dataset_type':'tokenid', 'input_length':1024, 'output_length':1024,
                    'prefix_length':0, 'request_count':1000, 'concurrency':16,
                    'request_rate':0, 'warmups':0, 'retry':1, 'mode':'perf',
                    'generation_kwargs':{'temperature':0, 'ignore_eos':True}}
path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
print('已更新：', path.resolve())
print('Runtime：', cfg['runtime'], 'GPU：', GPU_VENDOR)
PY
```

NVIDIA+Docker 保留两个选择变量；海光+nerdctl 将它们改成 hygon、nerdctl，其余地址/模型/日志/tokenizer/设备型号按海光节点实际情况修改。该例使用 container 主机采集视角；如需 host，再按第 3.4 节设置 host_metrics 和 node_exporter_target。根据实际推理服务调整 api、engine、tp/pp 与 generation_kwargs，不要把示例的服务信息当成自动发现值。

## 5. 找到并清理所有占位符

手动编辑的位置就是 `generated/site/site.json`，可以 `vi generated/site/site.json`，输入 `/REPLACE_` 回车搜索，按 n 查下一处，按 i 修改，Esc 后输入 `:wq` 保存退出。JSON 不支持注释或末尾多余逗号。

也可以用主机 Python 列出占位符的**完整字段路径**，避免只改 inference 而漏掉旧 metrics 或主机字段：

```bash
python3 -m json.tool generated/site/site.json > /dev/null
python3 - <<'PY'
import json
from pathlib import Path
cfg = json.loads(Path('generated/site/site.json').read_text())
def walk(value, path=''):
    if isinstance(value, dict):
        for key, item in value.items():
            walk(item, path+'.'+key if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            walk(item, path+'['+str(index)+']')
    elif isinstance(value, str) and 'REPLACE_' in value:
        print(path, '=', value)
walk(cfg)
PY
```

第二段无输出表示未发现 REPLACE_，不代表路径、网络或服务已经正确。常见占位在 inference.base_url、inference.model、storage.tokenizer、node_exporter_target、topology.metrics_endpoints[].target。检查器扫描整个配置，暂时不用的字段里也不能留下 REPLACE_；无用的可选覆盖项应删除。

## 6. 鉴权、预检查、启动和单档压测

### 6.1 在当前终端导出 Token

仅当配置了 inference.api_key_env 时执行，变量名与 JSON 一致。填纯 Token，不带 Bearer 前缀：

```bash
read -r -s -p 'Inference Token: ' INFERENCE_API_KEY
export INFERENCE_API_KEY
```

换终端或经过 sudo/作业调度器时，确认运行进程仍能读取该变量。Token 不写入 JSON 或 Git。应用 metrics 默认共用该变量；显式独立指标鉴权则额外导出其对应变量。Prometheus 的凭据文件在 up 时生成，Token 轮换后需要重建观测栈才能让采集使用新值。

### 6.2 校验配置和镜像

```bash
./bin/llm-bench plan --config generated/site/site.json
./bin/llm-bench images check --config generated/site/site.json
```

plan 检查 JSON、参数和 tokenizer_config.json 存在，渲染快照及生效计划；不访问服务、不读取实际 Token，也不检查镜像存在或完整 tokenizer 能否加载。images check 调用所选引擎/namespace 检查镜像与架构；实际完整 tokenizer 验证由本地检查和 AISBench 完成。缺镜像时按 [离线导入步骤](offline-deployment.md#offline-unpack)补齐，不在离线运行 images build。

### 6.3 首次启动观测栈

```bash
set -o pipefail
./bin/llm-bench up --config generated/site/site.json --no-benchmark \
  2>&1 | tee generated/site/real-up.log
./bin/llm-bench status --config generated/site/site.json
```

up 创建项目网络、所选设备 Exporter、node_exporter、Loki、logrotate、Alloy、Prometheus、Grafana，检查 targets 和真实 v1/models 就绪；external 省略本机设备 Exporter。--no-benchmark 不执行 AISBench 请求。正常状态为观测组件 running，后续才发压；真实模式不会创建 fixture 或模型服务。

**命令是 `llm-bench up`，没有 `llm-bench bench up`。** `bench` 下只有 run、suite、slo。首次直接 bench run 提示 No active stack 时，先运行上面的 up；冒烟栈的状态不能供真实 site.json 复用。

### 6.4 先小规模，再按配置正式发压

```bash
# 保留配置中的 1k/1k 长度，先验证 8 次、并发 2
./bin/llm-bench bench run --config generated/site/site.json --requests 8 --concurrency 2
# 成功后执行默认 1000 次、并发 16
./bin/llm-bench bench run --config generated/site/site.json
```

小规模参数只覆盖这次运行，不修改现场 JSON 或默认负载。返回 0 且 PASS single、报告 Success Requests 等于正式请求数、Failed Requests 为 0，才算这例完成；性能是否符合业务 SLO 另行判断。随机输入、流式参数、ignore_eos 或长度不被后端接受时，可能出现手工短请求成功而压测失败，要看本次错误响应。

也可在已有栈覆盖一次正式参数：

```bash
./bin/llm-bench bench run --config generated/site/site.json \
  --input-len 1024 --output-len 1024 --requests 1000 --concurrency 16
```

首次启动并立即执行默认 benchmark 可用不带 --no-benchmark 的 up；已有栈反复压测用 bench run，不再次 up。bench run 的控制台输出 START/PASS/FAIL，AISBench 实时进度和请求错误写入本次 client.log，不一定直接打印在终端。

## 7. 多个不同输入输出、请求数和并发的测试例

site.json 保留共用环境和默认 benchmark。各例放在独立 `generated/site/cases.json` 中，不向 site 增加 cases 数组，也不把 input_length/concurrency 等标量改为数组。

创建清单，已有文件时拒绝覆盖：

```bash
python3 - <<'PY'
import json
from pathlib import Path
suite = {'cooldown_seconds':5, 'stop_on_failure':True, 'cases':[
    {'name':'in512-out128-r100-c4', 'benchmark':{
        'input_length':512, 'output_length':128, 'request_count':100, 'concurrency':4}},
    {'name':'in1024-out1024-r1000-c16', 'benchmark':{
        'input_length':1024, 'output_length':1024, 'request_count':1000, 'concurrency':16}},
    {'name':'in4096-out256-r300-c8', 'benchmark':{
        'input_length':4096, 'output_length':256, 'request_count':300, 'concurrency':8}},
]}
path = Path('generated/site/cases.json')
path.parent.mkdir(parents=True, exist_ok=True)
with path.open('x') as stream:
    stream.write(json.dumps(suite, ensure_ascii=False, indent=2) + '\n')
print('已生成：', path.resolve())
PY
```

先用 plan 检查每例实际参数，已有观测栈直接运行 suite：

```bash
./bin/llm-bench plan --config generated/site/site.json --cases generated/site/cases.json
./bin/llm-bench bench suite --config generated/site/site.json --cases generated/site/cases.json
```

各例按顺序执行、例间冷却 5 秒，共 1400 次正式请求；并发控制各例内部请求，不是三例同时启动。未指定的字段每例独立继承 site.benchmark；generation_kwargs 整体替换；suite 会移除原生 concurrency_levels/requests_per_level 扫描。失败默认停止，报告仍保留。

尚未启动栈时也可 `./bin/llm-bench up --config generated/site/site.json --cases generated/site/cases.json` 一次完成，只执行清单，不额外执行默认单档，且不能同时带 --no-benchmark。JSONL/ShareGPT 的文件路径、请求计数和挂载规则见 [混合数据集](aisbench-sequential.md#在同一份-casesjson-混用三种数据集)；输入长度由真实文件内容决定。

## 8. 看实时日志、指标和本次报告

以下路径以脚本中的 storage.root=generated/qwen-real 为例，换 root 后同步替换：

| 内容 | 路径或入口 |
| --- | --- |
| bench run/suite 本次汇总 | `generated/qwen-real/data/results/suites/<运行目录>/suite-results.json` |
| 单档进度 / 请求错误 | 本次运行目录的 `single/client.log` |
| 多例进度 | 本次运行目录的 `<测试例name>/client.log` |
| AISBench 性能报告 | 对应用例目录的 `results/<AISBench时间目录>/performances/aisbench-target/synthetic.json` |
| 默认 up 压测报告 | `generated/qwen-real/data/results/<AISBench时间目录>/performances/aisbench-target/synthetic.json` |
| 持久观测数据 | `generated/qwen-real/data/` 下 prometheus、loki、grafana、alloy、logrotate |
| 原始推理日志 | 示例 `/srv/inference-logs/server_log.log`；以 storage.logs + log_sources[].path 为准 |
| 诊断包 | `generated/qwen-real/diagnostics/` |

在另一终端定位本次 client.log 后实时查看，示例路径中的“本次运行目录”需替换：

```bash
find generated/qwen-real/data/results/suites -name client.log
tail -f generated/qwen-real/data/results/suites/本次运行目录/single/client.log
```

Grafana 默认 `http://运行观测栈的节点IP:33000`，打开 Inference Benchmark → 当前厂商任务总览；task 选择 qwen3-0.6b，再筛选 GPU 节点/型号/卡序号，日志选择角色/副本。应用性能图按任务聚合，不因 GPU 筛选自动变成单卡吞吐。

实时推理日志可在 Grafana Explore 选择 Benchmark Loki，查询 `{task="qwen3-0.6b", instance_role="master", replica="replica-0"}` 并开启 Live；Dashboard 自动刷新日志和 Logs Volume。Data sources 是连接配置页。Prometheus `http://节点IP:39090/targets` 查看应用/GPU/主机采集目标；GPU 空闲利用率为 0 属于可能的正常值，服务未暴露的指标显示 No data。Grafana 初始默认 admin/admin；如已修改，沿用数据目录中持久化的实际密码。

## 9. 停止、再次运行和修改后生效规则

需要保留诊断时先采集，再关闭观测栈：

```bash
./bin/llm-bench logs collect --config generated/site/site.json
./bin/llm-bench down --config generated/site/site.json
```

down 只清理当前项目登记且 ownership 匹配的容器和网络，保留 tokenizer、原始日志、监控数据和压测报告，也不关闭外部推理服务。当前压测持有锁时先等它结束，再清理。

| 要做的变更 | 操作 |
| --- | --- |
| 只改本次输入/输出、请求数、并发 | 已有栈使用 bench run 参数覆盖，或修改独立 cases.json 后 bench suite |
| 改 site 默认 benchmark | 先 down，再改 site、plan、images check、up，使新默认进入 manifest |
| 改服务、日志、GPU 厂商/节点、端口或 runtime | 先 down 原栈，再编辑、重新提供所需 Token、plan、images check、up |
| 改 project/storage.root | 先用原值 down，修改后重新 up；新 root 有独立状态和持久数据 |
| 切换 Docker/nerdctl 或 namespace | 先 down、向目标存储/namespace 导入镜像、改 runtime 后重建栈，不重启系统 daemon |
| 只换客户端请求 Token | 后续 bench 使用当前进程的值；metrics Token 在 up 时写文件，轮换采集凭据须重建栈 |

**bench/status/down 从保存的 manifest 读取生效配置，现场 JSON 不会热更新正在运行的栈。** 停止后再次启动可复用 site 和持久目录；与更换 GPU 相关的设备/驱动依赖仍需现场准备。修改配置、标签、面板和运行时本身不需要重建已兼容的镜像。

## 10. 常见报错如何处理

| 报错或现象 | 检查与下一步 |
| --- | --- |
| Fill all REPLACE_ placeholders first | 按第 5 节列出所有字段路径；替换实际值或删除不用的可选覆盖项，然后重新 plan |
| No active stack | 当前 site 对应的观测栈未启动；先 up --no-benchmark；确认 project/root 指向正确现场配置 |
| bench: invalid choice: up | 使用 llm-bench up；bench 下只有 run/suite/slo |
| Stack state exists / Name collision | 先 status、按归属采集诊断并正常 down；复用栈发压用 bench；不要批量删其他项目资源 |
| 缺镜像 / Wrong architecture | 用相同 runtime/namespace 导入匹配的 amd64 镜像，不靠修改标签冒充 |
| bind: address already in use | 某节点端口冲突；若已部分启动，采集诊断后 down，改 ports 再 up |
| tokenizer_config.json 不存在 / 加载失败 | 校对模型、目录层级、软链接和完整词表；plan 只查存在，完整加载还需实际检查 |
| 401/403 或就绪失败 | 检查 base_url、models/metrics 转发、当前 Token 和指标鉴权，主机可达还要容器可达 |
| Bad Request / 全请求失败 | 看本次 client.log 和 failed.json 的真实响应，核对 model、API、上下文、流式参数和 generation_kwargs |
| 只有 START 没有逐请求进度 | 查看本次用例的 client.log；up --no-benchmark 本身没有 AISBench 请求 |
| 海光面板没有型号/卡序号 | 校对 nodes[].model 与原生 minor_number 等标签；旧 dcu_* 需先适配，不能只靠面板改名 |
| Grafana 看不到实时日志 | 检查真实日志是否更新、storage.logs/path、权限和 Alloy；在 Explore/日志面板查看，非 Data sources |

现场验证应记录操作系统、设备与驱动、运行时/namespace、镜像、模型/tokenizer 和实际 site/cases 的版本。本手册的配置/计划校验不等同于真实 NVIDIA/海光硬件性能验收。
