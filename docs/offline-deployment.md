# 离线环境准备与部署

推理服务需要 Bearer Token 时，设置 `inference.api_key_env` 并导出对应环境变量；单次、顺序及 SLO 搜索均支持，详见 [推理鉴权说明](inference-auth.md)。仅更新现场配置、标签、Dashboard 或切换 Docker/nerdctl 时，同步当前源码即可复用已有兼容镜像；若旧 toolbox 尚未包含鉴权探测能力，则按鉴权文档单独更新该镜像。

命令入口与安装步骤见 [CLI 快速上手](cli.md#快速上手)。已经完成镜像导入、准备编辑 site.json 时，可直接使用 [site.json 配置与启动压测手册](site-config-and-benchmark.md)，逐项核对字段、NVIDIA/海光差异、占位符以及单档/多例执行顺序。

**真实单实例服务以 [GPU41 Qwen3-0.6B 完整示例](gpu41-qwen3-random-1k.md)为接口和负载示例：随机 1k/1k、1000 次请求、并发 16。离线按硬件选择 gpu.vendor=nvidia 或 hygon（海光 DCU），Docker/nerdctl 自主选择；它与模拟冒烟配置是两个独立文件。在线交付默认准备两个厂商 Exporter，实际只启动选中厂商，选择规则见第 2、3.3、4.4.3 节。**

推荐流程是：**联网 x86 Linux 机器构建并验证全部镜像 → 导出仓库、镜像、tokenizer、系统依赖与配置 → 离线服务器导入并校验 → 启动观测链路和 AISBench**。不建议在离线服务器第一次运行时才安装依赖或构建镜像。

如果在线构建机是 CPU 机器、目标机是离线 NVIDIA GPU 或海光 DCU 节点，可直接按[在线 CPU 构建与离线 GPU 部署操作手册](online-cpu-to-offline-gpu.md)逐步执行；其中包含可复制的构建、交付包校验、现场参数填写和小规模验收步骤。

当前正式启动器支持顶层 `"offline": true`：启动前一次检查所有选用镜像及架构；缺失时直接列出清单并退出，不创建容器或网络；禁止构建、源码下载和自动拉取，并关闭已知组件外联功能。默认 offline=false，兼容原有在线流程。

Mac 可运行 `python3 docs/examples/verify_offline_mac.py`，验证入口使用正式离线策略，只额外注入内部网络隔离，不再替正式代码补充禁止拉取参数。需提前备齐 Mac 模板的七个 arm64 镜像；不修改 Docker Desktop 全局网络，不删除镜像或已有模拟栈。见 [正式离线模式验收](reports/mac-offline-mode-2026-11-07.md)。Mac 模拟不等同于真实 Linux/GPU 或整机断网验收。

> **离线现场操作顺序：** [解压、导入镜像和 tokenizer](#offline-unpack) → [生成 site.json](#offline-site) → [自带 tokenizer 全链路冒烟与停止](#offline-smoke) → [修改真实服务配置](#real-service) → [单档压测全流程](#real-benchmark) 或 [多测试例](#multiple-cases)。`site.json` 与 `offline-smoke.json` 都需要按步骤准备，仓库不会预置 generated 下的文件；以下第 4 节包含可复制命令及持久化位置。

## 1. 必须准备的内容

| 类别 | 准备内容 | 说明 |
| --- | --- | --- |
| 工程 | 固定 Git 提交的源码包，包含 bin、scripts、configs、containers、docs、tests | 启动器依赖仓库相对结构；不要只复制一个 Python 文件 |
| 主机运行工具 | Python 3.9+；Docker Engine/CLI 或 nerdctl/containerd/OCI 后端/CNI 及依赖安装包；可选 Git、tar、校验工具 | 按 runtime 选择，nerdctl 明确 namespace；启动不要求 BuildKit，Compose 不是必需项；用源码包时不要求 Git |
| 项目镜像 | AISBench 与 toolbox 的 **linux/amd64** 完整镜像 | 生产服务器都是 x86，不要带 Mac 的 arm64 镜像冒充 amd64 |
| 观测镜像 | Prometheus、Grafana、Loki、Alloy、node_exporter | logrotate/就绪探测已包含在 toolbox 中 |
| 设备采集 | 默认备齐 NVIDIA DCGM 和海光 HCU Exporter 镜像，可配置单厂商或追加镜像 | 离线按 gpu.vendor 只启动选中厂商；external 模式在各设备节点另行部署 |
| 设备驱动/运行时 | 目标 OS/内核兼容的驱动、设备容器运行时及完整依赖 | 容器镜像不包含可替代宿主机驱动的安装环境 |
| tokenizer | 被测模型对应的完整本地 tokenizer 文件 | string 和 tokenid 的性能汇总都需要，不能使用模拟词表替代真实模型词表 |
| 测试配置 | site.json、suite JSON、SLO search JSON | 设置 offline=true，包含内网地址、模型 ID、GPU 厂商、挂载路径、负载与停止条件 |
| 网络与证书 | 内网 DNS/IP、必要端口、内网 CA、时间同步源 | 离线指无公网，不是容器之间或到推理服务完全无网络 |
| 存储 | 配置/状态目录、日志目录、指标/日志数据、AISBench 报告空间 | 预估解包、镜像层、压测明细与监控保留空间，不能只按压缩包大小规划 |
| 被测服务 | 推理服务镜像、权重、模型配置和部署依赖 | 由服务部署方另外准备，本工程不部署真实推理服务；AISBench 客户端本身不需要模型权重 |

Docker/驱动安装包应在与目标发行版、版本、CPU 架构匹配的联网环境收集依赖，使用组织已有的离线软件源或安装包仓库。单独一个 docker 二进制通常不等于完整 Engine 安装；还需对应的 daemon、containerd/runc、网络与系统依赖。只有运行设备 Exporter 的主机才需要对应设备访问能力。

## 2. 当前版本的镜像清单

以 `configs/sites/gpu41-qwen3-0.6b-random-1k.json` 为 NVIDIA 单机示例。五个公共观测镜像已切换为渡渡鸟提供的华为云地址，仅验证 linux/amd64；Mac 模板保留原地址。完整上游名称、拉取命令与结果见 [镜像拉取验证记录](reports/dodo-images-2026-11-10.md)。下面是当前默认值；如果 site.json 修改过标签，以现场配置中的镜像名为准，并保存实际 image ID/digest。

| 组件 | 镜像 |
| --- | --- |
| AISBench | `llm-benchmark-aisbench:e330dc1-amd64`，需本项目构建 |
| toolbox（探测、logrotate） | `llm-benchmark-toolbox:local-amd64`，需本项目构建 |
| Prometheus | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/prom/prometheus:v3.5.0` |
| Grafana | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/grafana/grafana:12.0.2` |
| Loki | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/grafana/loki:3.5.0` |
| Alloy | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/grafana/alloy:v1.8.3` |
| node_exporter | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/prom/node-exporter:v1.9.1` |
| NVIDIA，按需 | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nvidia/dcgm-exporter:4.8.4` |
| 海光，按需 | `harbor.sourcefind.cn:5443/hcu/admin/base/hcu-exporter:v3.0.0` |

Python 基础镜像现按现场要求使用 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim-bookworm`，此前在本在线环境测试返回 Forbidden，本次仅修改名称，不重新拉取或构建。

DCGM 按现场指定改为 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nvidia/dcgm-exporter:4.8.4`。本次仅更新配置，不执行拉取或运行验证。下方在线准备脚本将其与其他公共镜像一起拉取、检查 amd64 架构并导出；如使用另行制作的镜像归档，导出和离线配置仍必须使用相同标签；本节新工具会拉取清单中选中的公共/设备镜像。升级后应在真实 GPU 节点确认驱动兼容性、Exporter 启动及 Prometheus 指标。

**默认交付包共 9 个镜像：7 个通用镜像 + NVIDIA DCGM + 海光 HCU Exporter。** 配置 [configs/delivery/offline-images.json](../configs/delivery/offline-images.json) 默认 selected_gpu_exporters=[nvidia,hygon]；镜像名仍从现场 site.images.nvidia_exporter/hygon_exporter 读取。离线 NVIDIA 单机 local 实际需要前七个 + DCGM（8 个），海光则为前七个 + HCU（8 个），不会同时启动两类采集。海光 DCU 的配置值为 hygon，不是 dcu；默认 HCU v3 不与旧 dcu-exporter 2.4.x 自动兼容。官方下载/镜像站如需账号，应在联网侧完成授权和拉取，避免进场后才发现无权下载。[海光官方入口](hygon-official-monitoring.md)。

AISBench 镜像包含 Python 3.11、CPU Torch、AISBench 与其 Python 依赖；toolbox 包含 Python、logrotate 与探测程序。使用这些**已构建镜像运行**时，离线主机不需要另装 pip 包，也不需要 python 基础镜像。只有离线重建时才需要基础镜像和全部构建依赖。

## 3. 联网侧：制作交付包

以下命令在仓库根目录、联网 **x86 Linux** 构建机执行，使用 Docker 构建/导出；离线目标机可独立选择 Docker 或 nerdctl 导入同一归档。构建 CPU 客户端不要求构建机有 GPU。若已经用本工程 build 命令完成 amd64 镜像构建，可跳过 3.2；不要直接拿 Mac 模拟镜像改标签。

### 3.1 固定仓库版本与目录

```bash
mkdir -p generated/offline-bundle/images generated/offline-bundle/metadata \
  generated/offline-bundle/tokenizer generated/offline-bundle/runtime \
  generated/offline-bundle/drivers generated/offline-bundle/site
git rev-parse HEAD > generated/offline-bundle/metadata/repository-commit.txt
git archive --format=tar --prefix=LLM-Inference-Benchmark/ HEAD \
  | gzip > generated/offline-bundle/repository.tar.gz
```

git archive 只包含已提交文件，不包含当前未提交修改、generated、运行数据或 .git。需要保留 Git 历史时可另外准备 git bundle。现场配置放在交付包 site/，不要复制另一台主机的 `generated/.../state.json` 或 suite.lock；它们含运行状态和绝对路径，应在目标机重新生成。

### 3.2 构建两个项目镜像

所有项目镜像构建命令统一使用 `--network=host`，让 apt/pip 等构建步骤使用构建机网络，避免 Docker bridge 网络不可达造成软件源超时。构建机本身仍须能访问 Debian、Python 包索引等依赖源；此参数不能修复宿主机的软件源访问限制。Linux Docker Engine 支持此模式；Docker Desktop 需支持并启用 host networking，远程 BuildKit 还需允许 `network.host` entitlement。此设置只作用于镜像构建，不改变运行时容器网络或离线禁构建策略。

直接构建可避免联网构建机必须具备真实服务地址、tokenizer 路径和 GPU 自动识别条件：

```bash
docker build --network=host --platform linux/amd64 -t llm-benchmark-toolbox:local-amd64 \
  -f containers/toolbox/Dockerfile .

mkdir -p generated/aisbench-offline-build
curl --fail --location \
  https://codeload.github.com/AISBench/benchmark/tar.gz/e330dc134c33f47671926144d9c143b554bc71cc \
  --output generated/aisbench-offline-build/source.tar.gz
printf '%s  %s\n' \
  87a5434c8fa8b78aa57b588f00fbfb1861435d602496f6473b4a9dece94647c2 \
  generated/aisbench-offline-build/source.tar.gz | sha256sum -c -
cp containers/aisbench/Dockerfile generated/aisbench-offline-build/Dockerfile
docker build --network=host --platform linux/amd64 -t llm-benchmark-aisbench:e330dc1-amd64 \
  generated/aisbench-offline-build
```

源码提交和归档校验值与当前启动器一致。构建会访问基础镜像仓库、Debian apt 源、Python 包索引及 PyTorch CPU 下载站；这些访问在联网构建机完成。保留构建日志与镜像内 `/opt/aisbench/dependencies.txt`，便于追溯实际依赖版本。

### 3.3 配置双厂商镜像交付并导出

在线 CPU 机使用 [prepare_offline_images.py](../scripts/prepare_offline_images.py)，交付选择由 [offline-images.json](../configs/delivery/offline-images.json) 管理。默认配置为：

```json
{
  "schema_version": 1,
  "gpu_exporters": {"nvidia": "nvidia_exporter", "hygon": "hygon_exporter"},
  "selected_gpu_exporters": ["nvidia", "hygon"],
  "additional_images": []
}
```

在源码根目录执行。下面默认从 GPU41 示例的 images 读取引用，不读取实际 Token、不检查本地 tokenizer/GPU、不访问推理服务：

```bash
python3 scripts/prepare_offline_images.py --plan
python3 scripts/prepare_offline_images.py
```

--plan 只输出选择，不调用 Docker、不拉取或写文件。实际执行默认拉取 5 个观测镜像和 2 个设备 Exporter；toolbox/AISBench 使用第 3.2 节本地已构建镜像，不会从仓库拉取它们。逐个核对 linux/amd64 后，导出 `generated/offline-bundle/images/images-linux-amd64.tar`，保存 `metadata/images.json`（镜像 ID/digest/平台）和 `metadata/image-plan.json`（角色/选择）。不满足架构或任何选中镜像获取失败时直接终止，不能当作已完成交付。

如果已经按 3.5 节生成现场配置，或改过 images 标签，以**最终现场文件**为准重新执行，随后再生成校验和：

```bash
python3 scripts/prepare_offline_images.py \
  --site generated/offline-bundle/site/site.json --plan
python3 scripts/prepare_offline_images.py \
  --site generated/offline-bundle/site/site.json
```

只交付单厂商可用 `--exporters nvidia` 或 `--exporters hygon` 覆盖默认选择（共 8 个）；后续离线切换到缺失厂商前需重新准备并导入其镜像。每次执行重写目标归档，不会把两个单厂商归档自动合并。需要通用包时保留默认两种。

**其他 GPU 的预留入口：** 复制该交付 JSON，在 gpu_exporters 增加 `厂商 → site.images 字段` 映射，在 site.images 中填对应镜像并选入 selected_gpu_exporters；使用 `--config 自定义交付JSON --site 现场JSON`。additional_images 可直接追加其他完整镜像引用。工具去重后检查/导出，但不会自动添加设备运行适配：当前启动器只接受 nvidia/hygon，新厂商仍需补齐 ADAPTERS、校验/识别、Exporter 参数、GPU 采集与标签/Dashboard，见 [扩展说明](online-cpu-to-offline-gpu.md#51-其他-gpu-的扩展入口)。

site.offline=true 限制的是最终运行，不阻止这个明确的在线交付工具拉取。默认 HCU v3 镜像来自官方 YAML，实际 registry 权限、镜像架构与硬件兼容性仍需验证；旧 dcu-exporter 不能只改标签就视为兼容。docker image save 导出层和标签，不要用 docker export 代替；CPU 镜像无需因 NVIDIA/DCU 切换而重建。归档不含 tokenizer、模型权重、日志和其他 bind mount 内容，仍需单独交付。

### 3.4 准备真实 tokenizer 并做无网络加载检查

在线无 GPU 测试可使用 [ModelScope 下载 Qwen3-0.6B tokenizer 的步骤](online-cpu-to-offline-gpu.md#61-无-gpu-测试从-modelscope-下载-qwen3-06b-tokenizer)，缺少 ModelScope 时通过清华 PyPI 安装；生产仍需匹配真实被测模型。

AISBench 客户端默认设置 `OPENBLAS_NUM_THREADS=1`、`OMP_NUM_THREADS=1`、`MKL_NUM_THREADS=1`、`NUMEXPR_NUM_THREADS=1`、`TOKENIZERS_PARALLELISM=false`。正式启动器对单次、顺序和 SLO 压测都传入这些变量，已导入的旧镜像也能使用；手工 docker run 须显式传入下方参数。它们限制客户端计算库线程，不改变请求并发。新 Dockerfile 同时内置默认值；本次调整不要求仅为这些变量重新构建离线镜像。旧离线源码需更新启动器，既有压测容器需结束后重新创建才生效。

至少包括 tokenizer_config.json，以及模型所需的 tokenizer.json 或 vocab/merges/SentencePiece 文件。还可能需要 special_tokens_map.json、added_tokens.json、config.json、chat template 文件等；应携带模型发布的完整 tokenizer 目录，而非只挑一个文件。Hugging Face snapshots 常含指向 blobs 的软链接，打包时需解引用或同时携带完整目标内容。

```bash
tar -chzf generated/offline-bundle/tokenizer/model-tokenizer.tar.gz \
  -C /absolute/path/to/model-tokenizer .

docker run --rm --platform linux/amd64 --network none \
  --mount type=bind,src=/absolute/path/to/model-tokenizer,dst=/tokenizer,readonly \
  -e OPENBLAS_NUM_THREADS=1 -e OMP_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 -e NUMEXPR_NUM_THREADS=1 \
  -e TOKENIZERS_PARALLELISM=false \
  --entrypoint python llm-benchmark-aisbench:e330dc1-amd64 \
  -c 'from transformers import AutoTokenizer; t=AutoTokenizer.from_pretrained("/tokenizer", local_files_only=True, trust_remote_code=False); print(type(t).__name__, len(t.encode("offline tokenizer check")))'
```

镜像已经设置 HF_HUB_OFFLINE=1。自定义 tokenizer 若依赖额外包或自定义 Python 代码，应在联网构建阶段解决并重新封装/验证镜像；当前配置不自动开启 trust_remote_code。仅有词表但运行时还需下载 tokenizer 代码或依赖，不能认为已经准备完整。[Transformers 离线说明](https://huggingface.co/docs/transformers/installation)。

固定长度合成数据由 AISBench 在本地生成。混合 suite 另需交付 `cases.json`、它引用的业务 JSONL 和 ShareGPT 文件，并保留工程内的 `containers/aisbench/llm_bench_jsonl.py`。两个文件加载器均从本地只读挂载读取，已有固定版本 AISBench 镜像即可运行。拷贝后通过 `llm-bench plan --config site.json --cases cases.json` 校验路径和数据格式。[完整混合用例](aisbench-sequential.md#在同一份-casesjson-混用三种数据集)

### 3.5 系统、驱动与完整性校验

将匹配目标系统的运行时安装包放 runtime/，设备驱动/设备运行时包及安装说明放 drivers/，现场配置放 site/。同一套 rpm/deb 不能默认适用于其他发行版、版本或内核。NVIDIA 需准备 NVIDIA Container Toolkit 及驱动；海光需准备对应驱动/运行库、hy-smi 与 HCU Exporter 所需宿主机路径。

**先生成要交付的 site.json，再修改现场值。** 以下命令从 GPU41 完整示例生成文件，不覆盖已存在文件，选择目标运行时执行一条：

```bash
python3 scripts/create_site_config.py --runtime docker \
  --output generated/offline-bundle/site/site.json
# 或：python3 scripts/create_site_config.py --runtime nerdctl --namespace benchmark \
#   --output generated/offline-bundle/site/site.json
```

默认源文件为 `configs/sites/gpu41-qwen3-0.6b-random-1k.json`；指定 --source 可换其他完整模板。保留 `offline: true`、`platform: linux/amd64`、`simulation: false`，核对 project/task、runtime、存储、接口/模型/鉴权变量、GPU 厂商/节点、日志角色和空闲端口。NVIDIA 选 nvidia，海光 DCU 选 hygon，保留两个 images 引用以便通用交付；不要在在线 CPU 构建机运行生产 auto 检测。GPU41 默认 host_metrics=container；如需宿主机指标，选择 host 并填写可达的 node_exporter_target。`inference.base_url` 自动派生 API 和 metrics 路径，独立指标端点仅在需要覆盖时填写。逐项说明见 [GPU41 指南](gpu41-qwen3-random-1k.md)。

观测机没有设备、采集远端 Exporter 时应明确厂商及 external 配置，不能靠观测机识别远端卡。顺序用例和搜索参数分别参考 [顺序压测手册](aisbench-sequential.md) 与 [自动 SLO 搜索手册](aisbench-auto-slo.md)。私有 CA 还需按实际使用方式导入运行时/registry 和有关容器的信任库，仅在宿主机安装证书不会自动让所有容器信任它。

在全部物料放齐后生成 SHA-256 清单（流式计算，避免把大镜像归档一次读入内存）：

```python
import hashlib
from pathlib import Path

bundle = Path('generated/offline-bundle')
lines = []
for path in sorted(bundle.rglob('*')):
    if not path.is_file() or path.name == 'SHA256SUMS':
        continue
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    lines.append(f'{digest.hexdigest()}  {path.relative_to(bundle).as_posix()}')
(bundle / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')
```

## 4. 离线侧：从解压到真实服务压测

<a id="offline-unpack"></a>

### 4.1 解压源码、导入镜像和 tokenizer

先安装所选运行时及依赖。以下约定交付包已经解压到 `/home/llm-bench-delivery/offline-bundle`，源码解到 `/home/llm-bench-runtime`；若现场不同，统一替换路径。已经完成解压、导入的步骤可跳过，**不要重复解压覆盖已经修改的源码、配置或 tokenizer**。

在交付包目录核对并解压源码：

```bash
cd /home/llm-bench-delivery/offline-bundle
sha256sum -c SHA256SUMS
mkdir -p /home/llm-bench-runtime
tar -xzf repository.tar.gz -C /home/llm-bench-runtime
```

按运行时选择一种镜像导入方式，两个代码块不需要都执行：

```bash
# Docker
cd /home/llm-bench-delivery/offline-bundle
docker info
docker image load -i images/images-linux-amd64.tar
```

```bash
# nerdctl / containerd；后续配置也必须使用 benchmark namespace
cd /home/llm-bench-delivery/offline-bundle
nerdctl --namespace benchmark info
nerdctl --namespace benchmark load -i images/images-linux-amd64.tar
```

Docker 与 containerd 的镜像存储独立，nerdctl 的不同 namespace 也不自动共享镜像名称；在 Docker 中导入成功不能替代 nerdctl 导入。镜像归档不包含宿主机 bind mount 数据，tokenizer 和日志需单独准备。详见 [nerdctl 部署](nerdctl.md)。

以下回到**源码根目录**，把已交付的 tokenizer 解到一个新建空目录。这里的“自带 tokenizer”指随交付包提供的模型 tokenizer，不是仓库中的 mock-tokenizer，也不是自动从镜像内寻找词表：

```bash
cd /home/llm-bench-runtime/LLM-Inference-Benchmark
mkdir -p assets/tokenizer
tar -xzf /home/llm-bench-delivery/offline-bundle/tokenizer/model-tokenizer.tar.gz \
  -C assets/tokenizer
test -f assets/tokenizer/tokenizer_config.json
./bin/llm-bench --help
```

若 assets/tokenizer 已有其他模型文件，请另选空目录解压并相应修改 storage.tokenizer，避免混合词表。至少应有 tokenizer_config.json 以及该模型所需的 tokenizer.json、词表等完整文件；不要拿模拟词表解释真实服务性能。若源码已位于其他目录，从实际源码根目录继续以下步骤。

<a id="offline-site"></a>

### 4.2 解压后先生成 generated/site/site.json

**`generated/site/site.json` 是现场输入文件，不随 Git 仓库预置，也不会由 up 自动生成。** 生成步骤只依赖已交付源码和主机 Python，不联网、不构建镜像、不启动容器，也不访问模板里的推理服务地址。

在源码根目录执行以下命令之一，从 GPU41 完整示例生成配置：

```bash
# 使用 Docker
python3 scripts/create_site_config.py --runtime docker
```

```bash
# 或使用 nerdctl，namespace 与镜像导入保持一致
python3 scripts/create_site_config.py --runtime nerdctl --namespace benchmark
```

两条命令仅选择运行时，尚未选择目标硬件；后续在 gpu.vendor 设置 nvidia/hygon。默认源文件均为 `configs/sites/gpu41-qwen3-0.6b-random-1k.json`，默认输出均为 `generated/site/site.json`。需要显式指定路径时，例如：

```bash
python3 scripts/create_site_config.py \
  --source configs/sites/gpu41-qwen3-0.6b-random-1k.json \
  --output generated/site/another-site.json --runtime docker
```

**已存在输出文件时会拒绝覆盖。** 若已从交付包复制了 site.json，直接编辑已有文件；若要在离线机复制交付配置，可执行下面的独立替代步骤，不再运行上述生成命令：

```bash
python3 - <<'PY'
from pathlib import Path
source = Path('/home/llm-bench-delivery/offline-bundle/site/site.json')
target = Path('generated/site/site.json')
content = source.read_text()
target.parent.mkdir(parents=True, exist_ok=True)
with target.open('x') as stream:
    stream.write(content)
print('已复制：', target.resolve())
PY
```

生成器保留模板中的接口、模型、日志和负载参数；**GPU41 的地址与 NFS 路径不会自动变成当前节点的路径**。生成时允许 tokenizer 尚未就位；plan/up 才会检查其文件存在。先把 storage.tokenizer 指向刚解压的目录，并检查镜像标签及 runtime；其余真实服务字段在 4.4 节填写。使用上节默认解包位置时，可执行：

```bash
python3 - <<'PY'
import json
from pathlib import Path
path = Path('generated/site/site.json')
cfg = json.loads(path.read_text())
cfg['storage']['tokenizer'] = 'assets/tokenizer'
path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
print('Tokenizer：', Path(cfg['storage']['tokenizer']).resolve())
print('Runtime：', cfg['runtime'])
PY
```

相对 storage 路径按**源码根目录**解析，不按 JSON 文件所在目录解析。`runtime` 为 `["docker"]` 或 `["nerdctl","--namespace","benchmark"]`；自定义 socket/snapshotter 等全局参数也写在该数组中。运行时 CLI 覆盖及迁移规则见 [CLI 指南](cli.md)。后续统一通过 `./bin/llm-bench` 执行，无需额外设置 PATH。

<a id="offline-smoke"></a>

### 4.3 使用自带 tokenizer 做离线连通性和全链路冒烟测试

**先使用独立的模拟配置验证软件链路，再对真实服务发压。** 本节不需要真实推理服务、GPU 或 Bearer Token。AISBench 使用交付的真实 tokenizer 生成随机输入并汇总结果；内置 fixture 返回模拟流式响应，同时提供模拟应用/GPU 指标与日志。成功说明 tokenizer、客户端、容器网络、Prometheus、Grafana、Alloy、Loki 和报告链路可工作；模拟吞吐和 GPU 指标不能作为真实设备性能结论。

#### 4.3.1 生成独立的 offline-smoke.json

确认 4.2 节的 site.json 已存在、镜像名和 runtime 正确、tokenizer 已解压。下面只从 site.json 继承运行时、镜像名称和 tokenizer 路径，**不会继承真实服务地址、Token 配置或真实日志目录**，也不会修改 site.json：

```bash
python3 - <<'PY'
import json
from pathlib import Path

output = Path('generated/offline-smoke.json')
if output.exists():
    raise SystemExit('配置已存在，请直接复用或检查后手动编辑：' + str(output))
site = json.loads(Path('generated/site/site.json').read_text())
tokenizer = Path(site['storage']['tokenizer']).expanduser()
if not (tokenizer / 'tokenizer_config.json').is_file():
    raise SystemExit('Tokenizer 不完整，请先修改 site.json 的 storage.tokenizer：' + str(tokenizer))
cfg = json.loads(Path('configs/stacks/nerdctl-offline-smoke.json').read_text())
cfg.update(project='llm-bench-offline-smoke', task='offline-smoke',
           runtime=site['runtime'], images=site['images'],
           offline=True, legacy_seccomp_unconfined=True, simulation=True,
           platform='linux/amd64', startup_timeout=180)
if 'runtime_kind' in site:
    cfg['runtime_kind'] = site['runtime_kind']
cfg['storage'] = {
    'root': 'generated/offline-smoke',
    'logs': 'generated/offline-smoke-logs',
    'tokenizer': site['storage']['tokenizer'],
}
cfg['benchmark'].update(dataset_type='tokenid', input_length=64, output_length=16,
                        prefix_length=0, request_count=8, concurrency=2,
                        warmups=0, retry=1, request_rate=0, mode='perf')
cfg['ports'].update(grafana=33003, prometheus=39093, loki=33103,
                    alloy=32348, **{'node-exporter':39103, 'gpu-exporter':39403},
                    fixture=38083)
output.parent.mkdir(parents=True, exist_ok=True)
with output.open('x') as stream:
    stream.write(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
print('已生成：', output.resolve())
PY
```

它采用 Linux amd64 模拟模板；源文件名含 nerdctl，但最终 runtime 按 site.json 选择，可为 Docker。冒烟任务为 offline-smoke，日志由 fixture 写入 `generated/offline-smoke-logs/server.log`；本节中的角色是 master、replica-0。端口与生产示例不同，若仍被占用，先修改 offline-smoke.json 的 ports。不要为了避开名称冲突接管其他项目网络或删除不明容器。

#### 4.3.2 用 llm-bench 预检查并启动冒烟

```bash
./bin/llm-bench plan --config generated/offline-smoke.json
./bin/llm-bench images check --config generated/offline-smoke.json
set -o pipefail
./bin/llm-bench up --config generated/offline-smoke.json \
  2>&1 | tee generated/offline-smoke-up.log
./bin/llm-bench status --config generated/offline-smoke.json
```

plan 校验 tokenizer_config.json 存在并渲染配置，**不能单独证明 tokenizer 能完整加载或服务可达**。images check 检查同一 runtime/namespace 中的本地镜像。up 才会实际启动 fixture、node_exporter、Loki、logrotate、Alloy、Prometheus、Grafana，等待目标就绪并执行 8 次请求、并发 2 的随机 tokenid 压测。不要给 up 添加 --no-benchmark，否则不会执行本节 AISBench 验证。客户端离线加载挂载到 `/tokenizer` 的词表；不下载权重或 tokenizer。

本节显式开启 `legacy_seccomp_unconfined=true`，为本项目全部新建容器取消 seccomp 系统调用过滤，兼容旧运行时拒绝创建线程的环境；保留线程数限制，并不改变请求并发。不重启 Docker/containerd，也不影响其他项目。若现场要求保留过滤，在启动前显式改为 false 并验证运行时支持，影响详见 [兼容说明](legacy-docker-threads.md)。

#### 4.3.3 验收、查看实时日志与再次冒烟

up 返回 0 后，执行完整的软件链路验收：

```bash
python3 scripts/verify_simulation.py --config generated/offline-smoke.json
```

该工具仅用于 simulation，检查容器、Prometheus targets、模拟 GPU 指标及标签、Grafana 数据源/任务面板、Loki 请求日志和**最新一次默认 up**的 AISBench 报告，结果写入 `generated/offline-smoke/verification.json`。它需要本机能访问映射端口，Grafana 使用默认 admin/admin；自定义密码时在同一终端设置 BENCH_GRAFANA_PASSWORD。

| 验收项 | 查看位置与成功标准 |
| --- | --- |
| 运行状态 | status 显示长期观测组件 running；默认 up 的 AISBench 容器退出码为 0 属于正常完成 |
| AISBench | up 日志和最新 synthetic.json 中 Total/Success 为 8、Failed 为 0；无 tokenizer 加载错误 |
| Prometheus | `http://节点IP:39093/targets` 中采集目标 UP；GPU 数据来自 fixture，是模拟值 |
| Grafana | `http://节点IP:33003` → Inference Benchmark → 任务总览，task 选择 offline-smoke |
| 日志 | Grafana Explore 选择 Benchmark Loki，查询 `{task="offline-smoke"}`，或在任务总览查看日志与 Logs Volume |

Grafana Explore 可打开 Live 查看实时日志；任务总览通过自动刷新更新日志和 Logs Volume。不要把 Data sources 配置页当成日志页面。节点 IP 为运行观测栈的机器 IP；端口以实际 JSON 为准。

已有冒烟栈再次发压使用 bench，不要再次 up：

```bash
./bin/llm-bench bench run --config generated/offline-smoke.json
```

bench run 在控制台输出 START/PASS/FAIL，详细 AISBench 进度写入本次 `single/client.log`。重复 bench run 的结果在 suites/ 下，需查看本次 suite-results.json；verify_simulation 不读取这类 suite 结果来替代默认 up 报告。

#### 4.3.4 保存诊断并关闭冒烟

```bash
# 排障或需要回传证据时，先打包再关闭
./bin/llm-bench logs collect --config generated/offline-smoke.json
./bin/llm-bench down --config generated/offline-smoke.json
```

| 数据 | 路径 |
| --- | --- |
| 冒烟配置 / up 控制台记录 | `generated/offline-smoke.json` / `generated/offline-smoke-up.log` |
| 原始模拟日志 | `generated/offline-smoke-logs/server.log` |
| 默认 up 报告 | `generated/offline-smoke/data/results/<AISBench时间目录>/performances/` |
| 再次 bench run | `generated/offline-smoke/data/results/suites/<运行目录>/suite-results.json` 及 `single/client.log`、`single/results/` |
| 指标、Loki、Grafana 等持久数据 | `generated/offline-smoke/data/` 下各组件目录 |
| 诊断包 | `generated/offline-smoke/diagnostics/` |

down 删除本项目拥有的容器和网络，保留以上文件以及真实 tokenizer。下次可复用同一冒烟配置重新 up。冒烟失败会保留容器与日志用于诊断；先 status/logs collect，确认原因后 down，再修改配置重新 up，避免 Name collision。

<a id="real-service"></a>

### 4.4 生成并修改对接真实推理服务的 site.json

**4.2 节生成的 site.json 就是真实服务配置底稿，本节继续编辑它，不再生成或覆盖。** 若跳过前文，请先按 4.2 节从 GPU41 示例生成；不要把 offline-smoke.json 改成生产文件，以免遗留 fixture 地址、模拟日志和模拟指标。

以下以单实例 Qwen3-0.6B、压测与推理在同一台 GPU41 为例。模型和服务由现场另行部署；本工程只启动压测和观测链路，不重启推理 Pod。修改前逐项核对：

| 配置项 | 作用与现场修改要求 |
| --- | --- |
| `project` | 容器、网络和 ownership 前缀，示例 llm-bench-qwen-real；节点上已有同名栈时复用 bench 或先 down，不能重复 up |
| `task` | 推理业务任务标签，示例 qwen3-0.6b；应用/GPU/日志共用，Grafana 用它筛选 |
| `runtime` | Docker 或 nerdctl；nerdctl namespace 与导入镜像一致，普通容器运行时与 GPU 设备注入是不同配置 |
| `platform/offline/simulation` | 真实场景保持 linux/amd64 / true / false；simulation=false 才会访问真实服务并按配置采集真实设备 |
| `legacy_seccomp_unconfined` | 示例 true，作用于本项目所有新容器；false 可恢复过滤，需要重新创建项目容器生效 |
| `storage.root` | 示例 generated/qwen-real；状态、监控数据、配置快照及结果都保存在这里，不能与日志目录相同 |
| `storage.tokenizer` | 交付的 assets/tokenizer 或真实模型目录；必须匹配被测模型，不需要包含模型权重 |
| `storage.logs` | 节点本地可访问、真实服务持续写入的日志目录，如 /srv/inference-logs；容器不会自动到 Pod 内寻找或拉取日志 |
| `log_sources` | 活跃日志相对路径、角色、副本与可选节点，例如 server_log.log/master/replica-0/gpu41；必须与真实文件相符，优先于旧 log_files |
| `inference.base_url` | 服务根 URL，示例含网关前缀 /publishaddress/inference/qwen306b/；不带 v1/chat/completions 或 metrics 后缀 |
| `inference.model/api/engine` | 服务接受的模型 ID / chat 或 completions / vllm、sglang 或 auto；engine 是描述标签，不启动对应引擎 |
| `inference.api_key_env` | Bearer Token 环境变量名，如 INFERENCE_API_KEY；只写变量名，不写 Token；无鉴权服务删除该字段 |
| `topology` | 单实例 kind=single，tp/pp 填实际部署值，作为观测标签；不据此启动或调整服务并行 |
| `gpu.vendor/deployment/nodes` | NVIDIA 选 nvidia、海光 DCU 选 hygon（不是 dcu）；local 启动所选厂商 Exporter，填一个真实节点/卡数；已有 Exporter 用 external、逐节点填写 target |
| `gpu.nvidia_metrics_profile` | NVIDIA 推荐先 basic；本机 DCGM 默认 profiling 字段失败时不必先启用 full |
| `host_metrics` | GPU41 示例 container，只采容器视角主机指标；需要宿主机指标用 host 并填写容器可达的 node_exporter_target |
| `images` | 所选 runtime 中已经导入的完整标签与 amd64 架构；不要只改标签忽略镜像内容 |
| `ports/port_bind_address` | Grafana 33000、Prometheus 39090、Loki 33100、Alloy 32345、node_exporter 39100、GPU Exporter 39400；默认绑定 0.0.0.0，按占用和访问范围修改 |
| `benchmark` | 默认单档负载。多测试例由独立 cases.json 覆盖，见 4.6 节 |

#### 4.4.1 按实际文件位置修改服务、日志与负载

下面脚本更新 4.2 节的已有文件，**执行前先把脚本中的地址、模型、日志目录和节点改成现场值**。它保留已选择的 runtime、镜像标签和端口；本例仍使用示例的 container 主机指标模式。若使用原 NFS 模型目录日志，storage.logs 填该 NFS 绝对路径，不要照抄 /srv/inference-logs。

```bash
python3 - <<'PY'
import json
from pathlib import Path

path = Path('generated/site/site.json')
cfg = json.loads(path.read_text())
cfg.update(project='llm-bench-qwen-real', task='qwen3-0.6b',
           simulation=False, platform='linux/amd64', offline=True,
           legacy_seccomp_unconfined=True, host_metrics='container')
cfg['storage'].update(root='generated/qwen-real', logs='/srv/inference-logs',
                      tokenizer='assets/tokenizer')
cfg['inference'] = {
    'base_url': 'http://10.142.118.41:38000/publishaddress/inference/qwen306b/',
    'model': 'Qwen3-0.6B', 'api': 'chat', 'engine': 'vllm',
    'api_key_env': 'INFERENCE_API_KEY',
}
cfg['topology'] = {'kind':'single', 'tp':1, 'pp':1}
cfg['gpu'].update(vendor='nvidia', deployment='local',  # 海光 DCU 改 vendor='hygon'
                  nodes=[{'name':'gpu41', 'gpus':1}], nvidia_metrics_profile='basic')
cfg['log_sources'] = [
    {'path':'server_log.log', 'role':'master', 'replica':'replica-0', 'node':'gpu41'}
]
cfg['benchmark'] = {
    'dataset_type':'tokenid', 'input_length':1024, 'output_length':1024,
    'prefix_length':0, 'request_count':1000, 'concurrency':16,
    'request_rate':0, 'warmups':0, 'retry':1, 'mode':'perf',
    'generation_kwargs':{'temperature':0, 'ignore_eos':True},
}
path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
print('已更新：', path.resolve())
PY
```

`storage.logs=/srv/inference-logs` 配合上述 log_sources 时，Alloy 和 logrotate 读取宿主机的 `/srv/inference-logs/server_log.log`。启动器会挂载该目录，不要求手写 docker/nerdctl -v；但文件必须已经是推理服务真实输出，不要创建空文件来冒充。日志目录、轮转目录需有写权限，避免本工程 logrotate 与节点已有轮转任务同时管理同一文件。tokenizer 会只读挂载为客户端的 `/tokenizer`。

本例 benchmark 表示随机输入请求长度 1024、输出请求上限 1024、正式请求 1000、并发 16；warmups=0 无额外预热，retry=1 表示总尝试次数为 1，request_rate=0 表示不额外限 QPS。AISBench 本地生成随机数据，无需先运行独立数据集生成命令。聊天模板可能增加服务实际 prompt token 数，ignore_eos 需要后端支持，实际输出长度和成功数以报告为准。服务最大上下文需容纳输入、模板及输出，详见 [长度口径](aisbench.md)。

#### 4.4.2 基础 URL、metrics 和鉴权

使用本例 base_url 时，自动派生：

| 用途 | 实际 URL |
| --- | --- |
| chat 压测 | `http://10.142.118.41:38000/publishaddress/inference/qwen306b/v1/chat/completions` |
| 模型就绪探测 | `http://10.142.118.41:38000/publishaddress/inference/qwen306b/v1/models` |
| 应用指标 | `http://10.142.118.41:38000/publishaddress/inference/qwen306b/metrics` |

更换模型服务时改 base_url、model、tokenizer、日志和 task，三个派生地址跟随 base_url。网关需要转发流式推理、v1/models 和 metrics。若现场指标独立提供，配置 topology.metrics_endpoints、metrics_path、metrics_scheme 等覆盖值；希望自动派生时删除旧覆盖字段，不能保留通用模板中的 REPLACE_ 占位符。上面的脚本用单实例 topology 替换旧值，因此不会残留旧指标覆盖。

压测、models 探测和应用指标默认共用 api_key_env；在执行 up/bench 的同一 Bash 终端输入纯 Token，不带 Bearer 前缀：

```bash
read -r -s -p 'Inference Token: ' INFERENCE_API_KEY
export INFERENCE_API_KEY
```

重新登录或换终端时重新导出；Token 不写入 JSON、命令参数值或 Git。Prometheus 按启动时 Token 生成凭据文件。更换基础服务配置或 metrics Token 时，先 down 原栈，再导出新值并 up，避免旧监控配置继续使用。无鉴权服务删除 inference.api_key_env，且不要遗留独立 metrics 鉴权覆盖。详见 [鉴权说明](inference-auth.md)。

#### 4.4.3 设备与网络补充

**双厂商默认交付不等于双厂商同时启动。** 离线编辑 `generated/site/site.json` 的 `gpu.vendor`、节点和卡数即可选择本机采集；无需删除另一个 images 字段或重建 CPU 镜像。默认调用关系如下：

| 现场选择（simulation=false） | 实际使用的镜像 | 启动/就绪与面板 |
| --- | --- | --- |
| nvidia + local | images.nvidia_exporter | 仅启动 DCGM，内部 9400，检查 DCGM_FI_DEV_GPU_UTIL；NVIDIA 总览 |
| hygon + local | images.hygon_exporter | 仅启动 HCU v3，内部 16080，检查 hcu_memorycap_bytes；海光总览 |
| nvidia/hygon + external | 设备节点已有的对应 Exporter | 不启动本机 GPU 采集容器，按 nodes[].target 抓取，按厂商渲染面板 |

本机两类 Exporter 的宿主发布端口都由 ports.gpu-exporter 控制（默认 39400），映射到各自内部端口。NVIDIA 或海光的 images check 只要求所选模式实际用到的镜像，默认本机单卡场景均为 8 个，不会因为包里有 9 个而启动两种。simulation=true 只使用 fixture 模拟指标，不启动真实设备采集。

可先在未启动的完整 site.json 中改成以下 GPU 片段来选择海光（实际节点/卡数替换），无需改变推理服务 API 配置：

```json
{
  "gpu": {
    "vendor": "hygon",
    "deployment": "local",
    "nodes": [{"name": "dcu-node", "gpus": 1}]
  }
}
```

这是字段片段，不可替换整个 site.json。切换已有栈时必须**先 down 原栈，再改 vendor/runtime/节点，最后 plan、images check、up**；bench/status/down 仍用原 manifest，不会自动热切换。Docker/nerdctl 镜像存储独立，换运行时时仍需向目标 namespace 导入归档。新设备的驱动、挂载与指标契约也必须匹配。

NVIDIA 本机 Exporter：Docker 需已注册 nvidia runtime；nerdctl 使用 --gpus all，需可用的 NVIDIA Container Toolkit/nvidia-container-cli，不把 Docker runtime 别名直接当 containerd shim。真实设备/驱动需在现场确认。海光改 gpu.vendor=hygon 并核对对应设备和运行库；如果已有 dcu-exporter，先检查它真实暴露的指标，不假定旧 dcu-exporter 2.4.1 符合 HCU v3 指标契约。

使用已有设备 Exporter 时，例如将 GPU 部分改为以下片段，`target` 换成真实可达地址：

```json
{
  "gpu": {
    "vendor": "nvidia",
    "deployment": "external",
    "nodes": [{"name": "gpu41", "gpus": 1, "target": "10.142.118.41:9400"}]
  }
}
```

这是合并到完整 site.json 的片段，不是可独立运行的配置。external 模式不再启动本项目本机 GPU Exporter。host_metrics=host 时另填 node_exporter_target 为容器可达的节点地址；端口随 ports.node-exporter，不要把容器 localhost 当宿主机。

离线仍需容器互通及访问真实服务、Exporter；不要给整个栈设置 --network none。确认容器账户有数据目录和日志权限，内网路由、时间和 CA 正确；Docker bridge 或 nerdctl CNI 与网关可达。node_exporter 的采集视角取决于 host_metrics，选择 container 不代表已经监控宿主机全部资源。

<a id="real-benchmark"></a>

### 4.5 用 llm-bench 完成真实服务压测全流程

以下命令在源码根目录执行，使用**修改后的同一份** generated/site/site.json。若此前已启动该真实栈，先按本节末尾说明决定复用或 down；不要重复 up。

#### 4.5.1 预检查并只启动观测栈

先确保 4.4 节 Token 已在当前终端导出，并且真实服务已经启动：

```bash
./bin/llm-bench plan --config generated/site/site.json
./bin/llm-bench images check --config generated/site/site.json
set -o pipefail
./bin/llm-bench up --config generated/site/site.json --no-benchmark \
  2>&1 | tee generated/site/real-up.log
./bin/llm-bench status --config generated/site/site.json
```

plan 展示生效 runtime、路径、厂商、接口与 workload，并渲染配置快照；它不访问服务、不检查镜像，也不读取 Token。images check 检查全部选用的本地镜像。up --no-benchmark 启动所选 GPU/主机采集、Loki、logrotate、Alloy、Prometheus、Grafana，等待采集目标 UP 和真实 v1/models 就绪，**此时尚未执行 AISBench 请求**。Prometheus 指标抓取的鉴权也在这里验证；只确认模型接口正常不能替代 metrics 连通。

`--no-benchmark` 会省略 up 对 AISBench 镜像的要求；因此本流程的 images check 不加此参数，以便提前发现客户端镜像缺失。出现 `bind: address already in use` 时修改 ports；部分组件已启动的失败场景先保留诊断、down，再以新端口 up，不要直接重试造成资源重名。

#### 4.5.2 小规模确认后再正式发压

```bash
# 仍使用配置的 1024 输入/1024 输出，先验证 8 次、并发 2
./bin/llm-bench bench run --config generated/site/site.json --requests 8 --concurrency 2
# 上一步通过后，执行 site.json 默认的 1000 次、并发 16
./bin/llm-bench bench run --config generated/site/site.json
```

小规模参数只覆盖这次用例，不会修改 site.json 或后续默认值。成功应看到 PASS single 和 Suite report 路径，suite-results.json 中 passed=true；perf 报告 Success Requests 等于指定正式请求数、Failed Requests 为 0。AISBench 错误、报告缺失或完成数不符都会返回非零；HTTP 200 的单次手工请求并不证明后端接受随机输入、流式参数、ignore_eos 和长度上限。

bench run 的控制台仅显示 START/PASS/FAIL 等状态，逐请求进度与错误在本次 client.log。可在另一终端定位文件后实时查看：

```bash
find generated/qwen-real/data/results/suites -name client.log
# 将“本次运行目录”换为刚才新建的目录，不要查看旧成功记录
tail -f generated/qwen-real/data/results/suites/本次运行目录/single/client.log
```

如希望首次启动后直接执行正式单档压测，也可在尚未启动该栈时执行 `./bin/llm-bench up --config generated/site/site.json`，省略 --no-benchmark；它在控制台输出 AISBench 进度，结果在默认 up 的目录中。已有观测栈继续使用 bench run，不需要 down/up。

#### 4.5.3 查看指标、实时日志和结果

以默认端口、观测栈部署于 GPU41 为例，Grafana 为 `http://10.142.118.41:33000`，Prometheus 为 `http://10.142.118.41:39090`；改过节点或 ports 时替换。Grafana 登录后打开 Inference Benchmark → 任务总览，选择 task=qwen3-0.6b；GPU 指标按节点、型号、卡序号筛选；日志按 instance_role=master、replica=replica-0 筛选。

Loki 查询 `{task="qwen3-0.6b", instance_role="master", replica="replica-0"}`。在 Explore 打开 Live，或通过任务总览的日志面板自动刷新，同时查看 Logs Volume。Alloy 读取实际本地日志文件；若没有新请求日志，先确认服务确实持续写入该文件、文件权限及配置路径，而不是只修改 Grafana 数据源地址。NVIDIA/海光面板按所选厂商生成；服务未暴露的 vLLM/SGLang 指标会显示 No data。

| 运行方式 / 数据 | 默认路径，相对源码根目录 |
| --- | --- |
| site 输入 | `generated/site/site.json`，由用户维护 |
| 生效配置快照 | `generated/qwen-real/config/<哈希>/manifest.json`，启动器生成，勿手改 |
| 当前运行状态 | `generated/qwen-real/state.json` |
| bench run 的本次汇总 | `generated/qwen-real/data/results/suites/<运行目录>/suite-results.json` |
| bench run 的进度 / 请求错误 | 同一运行目录下 `single/client.log` |
| bench run 的 AISBench 性能报告 | 同一运行目录下 `single/results/<AISBench时间目录>/performances/aisbench-target/synthetic.json` |
| 默认 up 的 AISBench 报告 | `generated/qwen-real/data/results/<AISBench时间目录>/performances/aisbench-target/synthetic.json` |
| 监控/轮转持久数据 | `generated/qwen-real/data/` 下 prometheus、loki、grafana、alloy、logrotate |
| 推理服务原始日志 | 本节示例为 `/srv/inference-logs/server_log.log`；原 GPU41 模板则是其 NFS storage.logs 下同名文件 |

改变 storage.root 后以上输出随之改变。查看**当前运行目录**，不要用历史成功报告判断本次结果。失败时先检查本次 client.log 和 AISBench 的 failed.json/调试文件；Bad Request 需查看网关/后端实际返回内容，再核对模型、随机输入长度和请求参数。

#### 4.5.4 保存诊断、停止及修改后重启

```bash
./bin/llm-bench logs collect --config generated/site/site.json
./bin/llm-bench down --config generated/site/site.json
```

down 只删除本项目拥有的容器和网络，保留指标、Loki 数据、原始服务日志、结果和 tokenizer，也不停止真实推理服务。仅修改本次 workload 可用 bench 参数或 4.6 节用例覆盖；修改服务地址、日志、基础 benchmark 默认值、GPU、端口、runtime 等基础配置，需要先 down，再修改 site.json、重新导出 Token、plan/images check/up。

**bench/status/down 使用启动时保存的 manifest；修改 site.json 不会热更新运行栈。** 用 --config 仍需能通过 project/storage.root 定位原栈，先 down 后再修改这两个定位字段。切换 Docker/nerdctl 时先按原栈 down，将镜像导入目标引擎/namespace，再按新配置 up；无需为本次文档流程重建兼容镜像或重启 Docker/containerd。正在发压持有锁时，等当前用例结束再 down。

<a id="multiple-cases"></a>

### 4.6 多个测试例：一份 site.json + 一份 cases.json

**不同输入/输出长度、请求数、并发的多个测试例，不需要复制多份 site.json，也不要把 cases 数组塞进 site.json。** 当前 CLI 的职责为：

| 文件 | 保存的内容 |
| --- | --- |
| site.json | 共用服务/鉴权、tokenizer、日志/GPU/端口等环境；benchmark 是每个用例的默认值 |
| cases.json | 有序测试例；每个 case.benchmark 覆盖该用例的输入长度、输出长度、请求数、并发等 |

#### 4.6.1 site.json 如何修改

保持 4.4 节的完整服务配置，benchmark 使用单档默认对象即可，例如：

```json
{
  "benchmark": {
    "dataset_type": "tokenid",
    "input_length": 1024,
    "output_length": 1024,
    "prefix_length": 0,
    "request_count": 1000,
    "concurrency": 16,
    "request_rate": 0,
    "warmups": 0,
    "retry": 1,
    "mode": "perf",
    "generation_kwargs": {"temperature": 0, "ignore_eos": true}
  }
}
```

以上是字段片段，不能替换整个 site.json。已按 4.4 节脚本修改的文件无需再改：它已有这些默认值。若旧文件使用 concurrency_levels/requests_per_level 原生并发扫描，建议删除它们，改用下方 cases 清单统一管理；suite 会为各例移除原生扫描设置。不要将 input_length、output_length、request_count 或 concurrency 改成数组；这些默认字段仍是正整数。

每个测试例单独从 site.benchmark 继承，再用自己的 benchmark 覆盖；不会继承上一例的修改。generation_kwargs 整体替换，不递归合并。示例统一使用 tokenid、perf、无预热、一次总尝试；每例都显式写出所关心的四个参数，便于复查。

#### 4.6.2 创建不同长度、请求数和并发的测试例

在源码根目录执行，写入新文件；已有文件时拒绝覆盖，可直接编辑它：

```bash
python3 - <<'PY'
import json
from pathlib import Path

suite = {
    'cooldown_seconds': 5,
    'stop_on_failure': True,
    'cases': [
        {'name':'in512-out128-r100-c4', 'benchmark':{
            'input_length':512, 'output_length':128, 'request_count':100, 'concurrency':4}},
        {'name':'in1024-out1024-r1000-c16', 'benchmark':{
            'input_length':1024, 'output_length':1024, 'request_count':1000, 'concurrency':16}},
        {'name':'in4096-out256-r300-c8', 'benchmark':{
            'input_length':4096, 'output_length':256, 'request_count':300, 'concurrency':8}},
    ],
}
path = Path('generated/site/cases.json')
path.parent.mkdir(parents=True, exist_ok=True)
with path.open('x') as stream:
    stream.write(json.dumps(suite, ensure_ascii=False, indent=2) + '\n')
print('已生成：', path.resolve())
PY
```

这三例按顺序运行，正式请求总数为 1400，**不是每例 1400，也不是同时启动三份压测**。warmups=0、retry=1 且无失败时，发压请求数按表执行；如增加预热或重试，服务收到的 HTTP 调用数可能更多。每例 concurrency 只控制该例内部的并发上限。根据真实模型上下文容量调整 4096 等长度。

用例 name 必须唯一，使用小写字母、数字和短横线，最长 40 字符。cooldown_seconds 控制例间冷却；stop_on_failure=true 默认失败即停止，false 会在已完成但报告未通过时继续后续例，整套最终仍返回非零；运行时基础设施异常可能直接终止。

#### 4.6.3 验证并执行整套用例

先渲染并核对每例生效参数，plan 不会发请求：

```bash
./bin/llm-bench plan --config generated/site/site.json --cases generated/site/cases.json
```

如果 4.5 节已经启动观测栈，直接复用：

```bash
# 在当前终端保持所需 Token 环境变量
./bin/llm-bench bench suite --config generated/site/site.json \
  --cases generated/site/cases.json
```

若尚未启动真实栈，可选择一次启动并执行清单：

```bash
./bin/llm-bench images check --config generated/site/site.json
./bin/llm-bench up --config generated/site/site.json \
  --cases generated/site/cases.json
```

上述两种执行方式按栈是否已启动选择，不能对同一运行栈再次 up。`up --cases` 只执行清单，不会先额外执行 site.benchmark 的 1000 次默认单档；--cases 与 --no-benchmark 互斥。也可先 up --no-benchmark，再 bench suite。

每次 suite 在 `generated/qwen-real/data/results/suites/<运行目录>/` 新建结果，各例按自己的 name 建子目录，分别含 stack.json、aisbench.py、client.log 和 results/；suite-results.json 列出每例生效参数、passed、退出码和报告路径。控制台 START/PASS/FAIL 对应当前用例，实时进度查看该用例的 client.log。多例共用同一监控数据和 task 标签，可按运行时间范围关联性能和日志。完成后仍按 4.5.4 节 logs collect/down，观测栈不会因清单结束自动删除。

JSONL/ShareGPT 等文件型用例还需在 case 的 dataset 指定 type/path 并交付数据文件，路径相对 cases.json 所在目录解析；输入长度由文件内容决定，不能用合成 input_length 将其强行截成指定长度，完整格式见 [混合数据集说明](aisbench-sequential.md#在同一份-casesjson-混用三种数据集)。SLO 自适应搜索另需准备策略 JSON，见 [搜索指南](aisbench-auto-slo.md)。

## 5. 正式离线策略与错误处理

| 环节 | offline=true 的行为 |
| --- | --- |
| images check / up | 一次检查全部所需镜像，汇总缺失、架构错误和检查故障；up 在创建容器/网络前完成检查 |
| build | 直接拒绝，包括缓存已有源码的情况；不会执行 apt/pip 或下载源码 |
| up 缺少 toolbox/AISBench/公共镜像 | 列出缺失项和使用组件，不自动构建或拉取 |
| run/create | 全部带 --pull=never，防止预检查后镜像被删除而意外补拉 |
| 顺序用例 / SLO 搜索 | 继承 offline，提前检查 AISBench/toolbox；探测和客户端均禁拉取 |
| tokenizer / 数据 | 显式设置 HF_HUB_OFFLINE、TRANSFORMERS_OFFLINE、HF_DATASETS_OFFLINE、HF_HUB_DISABLE_TELEMETRY 为 1 |
| Loki | common 配置持续禁用 analytics.reporting_enabled |
| Grafana | 渲染时合并 offline.ini，关闭统计、主程序/插件更新、预安装、在线插件管理、签名公钥下载、新闻源和反馈链接 |
| Alloy | 正式启动参数自动追加 --disable-reporting |

配置采用 JSON 布尔值 `true`，不要写成字符串 `"true"`。选择 offline 不影响生产固定 linux/amd64，也不改变厂商指标选择。simulation 不需要真实 DCGM/HCU 镜像；external 的逐节点 Exporter 由节点部署方负责准备。

缺失清单示例：

```text
ERROR: Local image preflight failed; no new containers or network created.
Missing images (2):
  - llm-benchmark-aisbench:e330dc1-amd64 [aisbench]
  - grafana/grafana:12.0.2 [grafana]
Import matching images into the configured runtime/namespace and retry.
```

补齐并导入清单中的镜像后，重新运行 images check 和 up 即可。只启动观测链路时可为两条命令加 `--no-benchmark`，此时不要求 AISBench 镜像；后续压测仍必须准备它。检查失败可能留下生成配置目录，但不会产生运行 state.json 或本次容器/网络，不需要 down。

[Grafana 离线覆盖配置](../configs/common/grafana/offline.ini)基于固定 12.0.2 镜像。`check_for_plugin_updates=false` 同时关闭动态 Angular 检测规则更新；`public_key_retrieval_disabled=true` 使用内置公钥，**不关闭插件签名校验**。当前面板使用内置组件，不要求额外插件。更换组件版本或添加插件时需重新核对其离线兼容性。[Grafana 配置说明](https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/)、[固定版本 Angular 更新条件](https://github.com/grafana/grafana/blob/v12.0.2/pkg/services/pluginsintegration/angulardetectorsprovider/dynamic.go)、[Alloy run 参数](https://grafana.com/docs/alloy/latest/reference/cli/run/)。

这些策略自动作用于新生成的配置，不需要手改启动脚本。已有栈请使用旧配置 down 后修改 offline 并重新 up；不要直接编辑生成目录。顺序用例/SLO 搜索必须使用与启动时一致的配置。

**离线模式不是全局防火墙。** 它禁止本工程管理的构建/拉取，关闭固定组件的已知自动外联；生产容器仍需访问内网服务，故不强制使用 internal 网络。新增远程数据源、第三方插件、外部认证或自行调用 docker pull 不受该设置统一拦截。严格无公网验收仍需现场网络策略与出站审计；不能用 --network none 切断必要服务通信。

## 6. containerd / 高版本 Kubernetes 的额外准备

Docker 镜像本地存储与独立 containerd namespace 不互通，不能认为执行过 docker load 后 Kubernetes 就能看到镜像。

裸 containerd/nerdctl 场景需准备 containerd、runc、nerdctl、CNI 及设备运行时；只有计划在内网重建镜像时才需要 BuildKit。把镜像导入与 CLI 配置相同的 namespace，例如：

```bash
nerdctl --namespace benchmark load -i images/images-linux-amd64.tar
```

对应 stack runtime 为 `["nerdctl", "--namespace", "benchmark"]`，可由 `python3 scripts/create_site_config.py --runtime nerdctl --namespace benchmark` 生成。Docker 可选择 `--runtime docker`；已运行栈先 down 再切换，不重启系统运行时。已验证 nerdctl 1.7.7 和 2.1.3 的软件链路，CNI/namespace、GPU 注入、删除兼容行为与真实硬件覆盖边界见 [nerdctl 部署指南](nerdctl.md)。[nerdctl 官方命令文档](https://github.com/containerd/nerdctl/blob/main/docs/command-reference.md)。

Kubernetes 还需逐节点可用的离线镜像或内网 registry、CNI/CSI/device plugin 镜像与安装清单、运行时/CDI/RuntimeClass 配置、ConfigMap/PVC/Job 等资源、证书与存储。镜像通常要导入 CRI 使用的 k8s.io namespace，具体由目标发行版确认；设置与离线策略匹配的 imagePullPolicy（IfNotPresent 或已逐节点导入时的 Never），避免 Always 在无可达 registry 时解析镜像失败。[Kubernetes 镜像拉取策略](https://kubernetes.io/docs/concepts/containers/images/)。现有 CLI 不会自动生成或部署这些 Kubernetes 资源。

## 7. 如果必须在离线环境重建镜像

这与“离线运行现成镜像”是另一套交付要求，还需准备：

- `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim-bookworm` 的 amd64 基础镜像、对应 Dockerfile/源码归档及校验值。
- apt 完整依赖闭包或离线镜像源，包含 build-essential、libgomp1、git、logrotate 等及传递依赖。
- Python 3.11/Linux x86 兼容的全部 wheel/sdist 及构建依赖；特别包含 CPU Torch 下载源对应的包，不能拿 macOS/arm64 wheel 替代。
- pip/构建工具、缓存或内网索引，以及能够完全离线解析这些依赖的构建配置。

**offline=true 时本工程 build 命令始终拒绝执行。现有 Dockerfile 直接执行 apt-get 和 pip 联网安装，在线构建流程还使用公网源码 URL；仅提供 source.tar.gz 不足以离线重建。** 需要改用内网 apt/PyPI/镜像仓库，或重写为本地包源，并在隔离环境重新验证。除非确实需要离线开发，优先交付已验证的完整 amd64 镜像，可显著减少现场准备项。

## 8. 最小验收清单

- 校验和、仓库提交、镜像标签/ID/架构、真实 tokenizer 均匹配。
- 无公网条件下能启动所有所选容器；没有因缺镜像触发失败的 build/pull。
- Prometheus targets UP、正确厂商 GPU 指标可见、Grafana provisioning 正常、Loki 可查请求日志。
- AISBench 小规模单档成功并落盘报告，再验证顺序清单或 SLO 搜索。
- 停止/重启后数据路径、日志轮转和采集续读正常。
- 记录目标 OS/内核、驱动、运行时、镜像、服务与 tokenizer 版本，以及离线网络验证结果。

排障优先区分：缺镜像、错误架构、缺 tokenizer/依赖、内网地址不可达、驱动/设备权限、挂载权限以及后台外联告警。不要把“无公网”误诊为所有服务端口都应禁止。

现场异常时可执行 `llm-bench logs collect --config site.json`，打包组件日志、失败用例和运行环境，再离线回传。[诊断包操作手册](offline-diagnostics.md)

两个项目 Dockerfile 的 Debian 主仓库及安全更新仓库均使用清华 TUNA HTTPS 镜像：`https://mirrors.tuna.tsinghua.edu.cn/debian`、`https://mirrors.tuna.tsinghua.edu.cn/debian-security`。保留 bookworm、bookworm-updates、bookworm-security 及原有签名验证设置，兼容传统 sources.list 和 deb822 .sources 格式。构建机需能访问该域名并信任 HTTPS 证书；apt 索引下载失败时立即终止构建。Python 通用依赖统一使用清华 PyPI 索引 `https://pypi.tuna.tsinghua.edu.cn/simple`，包括 pip/setuptools/wheel、AISBench 及其构建依赖。PyTorch 固定 `torch==2.6.0+cpu`，通过 `--find-links https://download.pytorch.org/whl/cpu/torch/` 获取官方 CPU wheel；其余依赖仍由清华 PyPI 解析。构建时需同时允许清华 PyPI 及 PyTorch 官方下载域名。

Docker 模式的本地 NVIDIA DCGM Exporter 显式使用 `--runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=compute,utility`，保留 `SYS_ADMIN` 能力；宿主机需注册名称为 `nvidia` 的容器运行时，不要求将其设为默认值。启动器不使用 Docker 的 `--gpus all` 设备请求机制。仅修改启动参数，无需因此重建镜像；已有栈需先用原配置正常 down，再以当前源码 up。观测/压测客户端无需 GPU，external Exporter 模式和无 GPU 模拟不使用这些参数。nerdctl 已单独适配为 `--gpus all`，需要驱动及可执行的 nvidia-container-cli，不使用 Docker 的 runtime 别名；详见 [nerdctl GPU 说明](nerdctl.md#对接真实推理服务)。

旧 Docker 环境若最小 Python 线程测试失败、且仅在关闭 seccomp 后成功，可在 stack JSON 顶层显式设置 `"legacy_seccomp_unconfined": true`。离线模式默认开启、在线模式默认关闭，显式设 false 可关闭；开启后统一影响本工程新建的所有容器，包括监控组件、Exporter 和临时探测，不影响其他项目；适用范围、安全影响、重建容器及恢复步骤见 [旧 Docker 线程兼容说明](legacy-docker-threads.md)。
