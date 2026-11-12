# 离线环境准备与部署

推理服务需要 Bearer Token 时，设置 `inference.api_key_env` 并导出对应环境变量；单次、顺序及 SLO 搜索均支持，详见 [推理鉴权说明](inference-auth.md)。仅更新现场配置、标签、Dashboard 或切换 Docker/nerdctl 时，同步当前源码即可复用已有兼容镜像；若旧 toolbox 尚未包含鉴权探测能力，则按鉴权文档单独更新该镜像。

命令入口与安装步骤见 [CLI 快速上手](cli.md#快速上手)。

**真实单实例服务以 [GPU41 Qwen3-0.6B 完整示例](gpu41-qwen3-random-1k.md)为配置基准：随机 1k/1k、1000 次请求、并发 16。Docker 和 nerdctl 自主选择，生成的 `generated/site/site.json` 需要核对现场参数；它与模拟冒烟配置是两个独立文件。**

推荐流程是：**联网 x86 Linux 机器构建并验证全部镜像 → 导出仓库、镜像、tokenizer、系统依赖与配置 → 离线服务器导入并校验 → 启动观测链路和 AISBench**。不建议在离线服务器第一次运行时才安装依赖或构建镜像。

如果在线构建机没有 GPU、目标机是离线 NVIDIA 服务器，可直接按[无 GPU 在线 Linux 构建与离线 NVIDIA 压测操作手册](online-cpu-to-offline-nvidia.md)逐步执行；其中包含可复制的构建、交付包校验、现场参数填写和小规模验收步骤。

当前正式启动器支持顶层 `"offline": true`：启动前一次检查所有选用镜像及架构；缺失时直接列出清单并退出，不创建容器或网络；禁止构建、源码下载和自动拉取，并关闭已知组件外联功能。默认 offline=false，兼容原有在线流程。

Mac 可运行 `python3 docs/examples/verify_offline_mac.py`，验证入口使用正式离线策略，只额外注入内部网络隔离，不再替正式代码补充禁止拉取参数。需提前备齐 Mac 模板的七个 arm64 镜像；不修改 Docker Desktop 全局网络，不删除镜像或已有模拟栈。见 [正式离线模式验收](reports/mac-offline-mode-2026-11-07.md)。Mac 模拟不等同于真实 Linux/GPU 或整机断网验收。

> **离线机先跑软件全链路冒烟测试：** 按 [生成配置 → 启动 → 验收 → 停止](online-cpu-to-offline-nvidia.md#offline-smoke) 使用已交付 tokenizer 和内置模拟服务。`generated/offline-smoke.json` 需要按步骤生成，仓库不会预置；同一节说明了日志/报告持久化位置和 down 前的诊断采集。

## 1. 必须准备的内容

| 类别 | 准备内容 | 说明 |
| --- | --- | --- |
| 工程 | 固定 Git 提交的源码包，包含 bin、scripts、configs、containers、docs、tests | 启动器依赖仓库相对结构；不要只复制一个 Python 文件 |
| 主机运行工具 | Python 3.9+；Docker Engine/CLI 或 nerdctl/containerd/OCI 后端/CNI 及依赖安装包；可选 Git、tar、校验工具 | 按 runtime 选择，nerdctl 明确 namespace；启动不要求 BuildKit，Compose 不是必需项；用源码包时不要求 Git |
| 项目镜像 | AISBench 与 toolbox 的 **linux/amd64** 完整镜像 | 生产服务器都是 x86，不要带 Mac 的 arm64 镜像冒充 amd64 |
| 观测镜像 | Prometheus、Grafana、Loki、Alloy、node_exporter | logrotate/就绪探测已包含在 toolbox 中 |
| 设备采集 | NVIDIA DCGM Exporter 或海光 HCU Exporter 镜像 | 根据实际厂商准备；external 模式在各设备节点准备，不一定在观测主机启动 |
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

DCGM 按现场指定改为 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nvidia/dcgm-exporter:4.8.4`。本次仅更新配置，不执行拉取或运行验证。下方在线准备脚本将其与其他公共镜像一起拉取、检查 amd64 架构并导出；若已通过其他方式准备好，可跳过该项拉取，但导出和离线配置必须使用相同标签。升级后应在真实 GPU 节点确认驱动兼容性、Exporter 启动及 Prometheus 指标。

NVIDIA 单机链路共需要前七个镜像加 DCGM Exporter。海光替换最后一个设备镜像；同时支持两厂商的交付包可包含二者。官方下载/镜像站如需账号，应在联网侧完成授权和拉取，避免进场后才发现无权下载。[海光官方入口](hygon-official-monitoring.md)。

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

### 3.3 拉取公共镜像并记录架构

将下面代码保存为 `generated/prepare-offline-images.py`，从仓库根目录运行 `python3 generated/prepare-offline-images.py`。海光交付包把 vendor 改为 hygon；此处只读取模板中的镜像名，不启动模板中的服务地址。

```python
import json
import subprocess
from pathlib import Path

vendor = 'nvidia'  # 或 hygon
if vendor not in ('nvidia', 'hygon'):
    raise ValueError('Unsupported vendor')
cfg = json.loads(Path('configs/sites/gpu41-qwen3-0.6b-random-1k.json').read_text())
keys = ['toolbox', 'aisbench', 'prometheus', 'grafana', 'loki', 'alloy',
        'node-exporter', vendor + '_exporter']
images = [cfg['images'][key] for key in keys]
metadata = []
for key, image in zip(keys, images):
    if key not in ('toolbox', 'aisbench'):
        subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', image], check=True)
    info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True))[0]
    if (info['Os'], info['Architecture']) != ('linux', 'amd64'):
        raise RuntimeError('Wrong architecture: ' + image)
    metadata.append({'image': image, 'id': info['Id'], 'digests': info.get('RepoDigests', []),
                     'platform': 'linux/amd64'})
bundle = Path('generated/offline-bundle')
(bundle / 'metadata/images.json').write_text(json.dumps(metadata, indent=2) + '\n')
subprocess.run(['docker', 'image', 'save', '-o', str(bundle / 'images/images-linux-amd64.tar'),
                *images], check=True)
```

如果实际 site.json 的 images 字段不同，修改脚本读取的文件及选择项，与现场清单保持一致。docker save 导出镜像层和标签；不要用 docker export 代替。若本机保存了同镜像的多架构变体，默认 save 可能全部导出；Docker API 1.48+ 可增加 `--platform linux/amd64` 缩小归档，旧版本不要使用此选项。[Docker save 文档](https://docs.docker.com/reference/cli/docker/image/save/)。

### 3.4 准备真实 tokenizer 并做无网络加载检查

在线无 GPU 测试可使用 [ModelScope 下载 Qwen3-0.6B tokenizer 的步骤](online-cpu-to-offline-nvidia.md#61-无-gpu-测试从-modelscope-下载-qwen3-06b-tokenizer)，缺少 ModelScope 时通过清华 PyPI 安装；生产仍需匹配真实被测模型。

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

默认源文件为 `configs/sites/gpu41-qwen3-0.6b-random-1k.json`；指定 --source 可换其他完整模板。保留 `offline: true`、`platform: linux/amd64`、`simulation: false`，核对 project/task、runtime、存储、接口/模型/鉴权变量、GPU 节点、日志角色和空闲端口。GPU41 默认 host_metrics=container；如需宿主机指标，选择 host 并填写可达的 node_exporter_target。`inference.base_url` 自动派生 API 和 metrics 路径，独立指标端点仅在需要覆盖时填写。逐项说明见 [GPU41 指南](gpu41-qwen3-random-1k.md)。

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

## 4. 离线侧：导入、预检查与运行

先安装已准备的系统运行时、驱动及设备运行时，在交付包目录执行：

```bash
sha256sum -c SHA256SUMS
# 按 site.json 运行时选择一条导入命令
nerdctl --namespace benchmark load -i images/images-linux-amd64.tar
# Docker 则使用：docker image load -i images/images-linux-amd64.tar
tar -xzf repository.tar.gz -C /path/to/workspace
mkdir -p /srv/model-tokenizer
tar -xzf tokenizer/model-tokenizer.tar.gz -C /srv/model-tokenizer
```

导入恢复镜像和标签；Docker 与 containerd 的存储独立，nerdctl load 的 namespace 必须与现场配置一致。镜像只读层不包含主机 bind mount 数据。[Docker load 文档](https://docs.docker.com/reference/cli/docker/image/load/)。建议在 **新建空 tokenizer 目录**解包，避免与旧模型词表混合。将 site.json 的 storage.tokenizer 指向该目录。

离线运行前逐项确认：

1. 所选运行时的 `docker info` 或 `nerdctl --namespace benchmark info` 成功；主机 Python 版本满足要求；容器运行账户具有实际所需权限。
2. site.json 的所有选用镜像标签存在、架构为 linux/amd64，并与 metadata/images.json 的 image ID 对照。导入后如重命名标签，需同步修改 site.json；不要只修改镜像名字而忽略内容。
3. storage.root、storage.logs、tokenizer 和 log_sources[].path 路径真实有效；log_sources 优先于旧 log_files。目标机可创建数据目录，容器 UID/GID 有读写权限。
4. 被测服务、逐节点 Exporter、host 模式 node_exporter_target 地址在容器内可达；不能把容器 localhost 当成宿主机。NVIDIA 驱动/容器设备注入可用，或海光 `/dev`、`/opt/hyhal`、`/etc/hostname`、`/etc/vdev` 已准备。
5. 内网 DNS、时间同步、HTTPS CA/证书链可用；允许所选运行时的容器网络与推理服务/Exporter/日志接收端通信，nerdctl 需完整 CNI。项目 HTTP 服务默认映射到 0.0.0.0 的高位端口，可通过节点 IP 访问；使用 port_bind_address 指定网卡或仅回环，按现场规则放行可信客户端。
6. 若要求严格禁止公网请求，按下一节关闭相关功能并由现场网络策略控制出站访问；不要用 `--network none` 启动整栈，因为这会同时切断压测与观测必需通信。

从解包后的仓库根目录执行。`generated/site/site.json` 是现场输入文件，需要从交付包的 site/site.json 复制到该路径（存在时先保留自己的修改），或在本机生成：

```bash
# 仅在尚无现场配置时生成；Docker 改为 --runtime docker，省略 --namespace
python3 scripts/create_site_config.py --runtime nerdctl --namespace benchmark
# 编辑 generated/site/site.json：实际地址、模型、路径、GPU、端口与 runtime
# GPU41 示例需要鉴权；在同一 Bash 终端输入实际 Token
read -r -s -p 'Inference Token: ' INFERENCE_API_KEY
export INFERENCE_API_KEY
./bin/llm-bench plan --config generated/site/site.json
./bin/llm-bench images check --config generated/site/site.json
./bin/llm-bench up --config generated/site/site.json --no-benchmark
./bin/llm-bench bench run --config generated/site/site.json --requests 8 --concurrency 2
# 小规模验证通过后，按示例执行 1000 次、并发 16、随机 1k/1k
./bin/llm-bench bench run --config generated/site/site.json
./bin/llm-bench status --config generated/site/site.json
./bin/llm-bench logs collect --config generated/site/site.json
./bin/llm-bench down --config generated/site/site.json
```

site.json 必须包含顶层 `"offline": true`。`images check` 检查全部选用镜像，`up` 自动复查；`plan` 校验本地 tokenizer 并渲染计划，不检查镜像或服务。要启动并直接运行单档 AISBench，可 up 不带 --no-benchmark；已有栈再次压测用 bench run。down 保留监控数据、原始日志和报告，路径见 [GPU41 数据与停止说明](gpu41-qwen3-random-1k.md#5-数据停止与切换引擎)。SLO 搜索需另行准备 search.json，按 [搜索指南](aisbench-auto-slo.md)操作。

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
