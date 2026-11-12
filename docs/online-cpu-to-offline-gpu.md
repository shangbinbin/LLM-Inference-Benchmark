# 在线 CPU 构建与离线 GPU 部署操作手册

适用场景：在线机器为 **x86_64 Linux CPU 构建机，无需 GPU**；离线目标为 **x86_64 Linux 的 NVIDIA GPU 或海光 DCU 节点**。在线统一构建 CPU 版 AISBench/toolbox，默认下载并交付两种设备 Exporter；离线再选择厂商、运行时和现场服务。

**在线交付选择与离线启动选择是两层配置：** `configs/delivery/offline-images.json` 默认准备 NVIDIA DCGM 和海光 HCU Exporter 的镜像；`site.json` 的 `gpu.vendor` 决定离线启动哪一种采集容器、抓取规则与 Dashboard。海光 DCU 使用配置值 **hygon**，不要填 dcu。备齐两个镜像不会同时启动两个 Exporter。

| 离线选择 | 镜像字段 | 默认设备采集 | Dashboard |
| --- | --- | --- | --- |
| `gpu.vendor=nvidia` | `images.nvidia_exporter` | NVIDIA DCGM，本机模式只启动这一种 | NVIDIA 任务总览 |
| `gpu.vendor=hygon` | `images.hygon_exporter` | 海光 HCU Exporter v3，本机模式只启动这一种 | 海光任务总览 |
| `gpu.deployment=external` | 由设备节点另外部署 | 本项目不启动本机 GPU Exporter，抓取 nodes[].target | 按所选厂商 |

本项目的海光默认镜像为 HCU Exporter v3，**不是任意旧版 dcu-exporter 的可互换替代物**。已有 dcu-exporter 2.4.1/2.4.2 需先核对 `/metrics`、标签和单位，再完成适配，不能只换镜像名。其他 GPU 的镜像交付扩展入口见第 5 节，实际运行适配仍需补齐。

在线构建使用 Docker；离线可选择 Docker 或 nerdctl/containerd。构建机不需要 NVIDIA/海光驱动或设备运行库；真正启动设备采集时，离线节点才需匹配的驱动、设备访问和运行库。真实推理服务由现场另行部署，本工程不部署模型权重或推理引擎。

需要 Bearer Token 时只在 site.json 写 `inference.api_key_env` 变量名，运行时导出对应值；见 [鉴权说明](inference-auth.md)。配置、标签、Dashboard 或 Docker/nerdctl 切换可复用已有兼容镜像；旧 toolbox 尚无鉴权探测能力时，按鉴权文档单独更新镜像。

> **离线侧完整操作：** [生成 site.json](offline-deployment.md#offline-site) → [自带 tokenizer 冒烟与停止](#offline-smoke) → [选择 NVIDIA/海光及填写现场参数](#gpu-selection) → [llm-bench 压测](#real-benchmark)。多例输入输出/请求数/并发覆盖见 [多测试例](offline-deployment.md#multiple-cases)。

本文第 3 节生成 `project=llm-bench-gpu`、`storage.root=generated/gpu-site` 的通用小负载配置。GPU41 1k/1k、1000 请求、并发 16 的完整实例仍见 [GPU41 指南](gpu41-qwen3-random-1k.md)；两者的输出目录与负载不同，勿混用。

## 执行目录约定（先阅读）

本文使用以下示例绝对路径。在线机与离线机是两台不同服务器；这些目录不共享。可按现场磁盘规划替换，但同一台机器上的前后引用必须同步替换。

| 机器 / 步骤 | 命令执行目录 | 用途 |
| --- | --- | --- |
| 在线机，第 2 节 clone 前 | `/home/llm-bench-build` | 放置在线构建用仓库 |
| 在线机，第 2 节 clone 后至第 8 节 | `/home/llm-bench-build/LLM-Inference-Benchmark` | 仓库根目录；应能看到 scripts/、configs/、containers/ |
| 离线机，第 9 节 | `/home/llm-bench-delivery` | 放置外层交付压缩包和 .sha256 文件；解包后产生 offline-bundle/ |
| 离线机，第 10.1 节解包开始时 | `/home/llm-bench-delivery` | 使用绝对路径读取交付物，安装到新的运行目录 |
| 离线机，第 10.1 节最后 cd 后及第 11、12 节 | `/home/llm-bench-runtime/LLM-Inference-Benchmark` | 解包后的源码根目录；运行启动器、查看结果 |

**不要进入仓库的 generated/ 目录执行本手册命令。** 在线命令中的 `generated/...` 是仓库根目录下的路径；离线配置中的相对存储路径也按源码根目录解析，与终端当前位置、site.json 所在目录不同。

任意时刻不确定位置，先执行 `pwd`。在源码根目录还可以执行 `test -f scripts/benchmark_stack.py` 确认。每个 Bash 代码块应完整执行；其中 Python heredoc（`python3 - <<'PY'` 至末尾独占一行的 `PY`）属于同一条命令，不要把 Python 代码直接输入 Bash。

## 1. 开始前确认交付范围

| 物料/检查 | 在线 CPU 机器 | 离线 GPU/DCU 机器 |
| --- | --- | --- |
| 仓库源码、AISBench、toolbox | 获取并构建 | 解包、导入 |
| Prometheus、Grafana、Loki、Alloy、node_exporter、DCGM 与海光 HCU Exporter | 默认准备两种 Exporter，共 9 个镜像 | 导入全部，按 gpu.vendor 只启动选中厂商 |
| 真实模型 tokenizer | 收集完整文件并验证无网络加载 | 解包并配置路径 |
| Python、Docker 或 nerdctl/CNI、对应 GPU 驱动及设备运行库 | 按目标 OS/硬件收集安装包（目标机已有可跳过） | 选择实际运行时与设备依赖后检查 |
| 单元测试和无 GPU 模拟 | 可以执行 | 可选 |
| 真实 GPU 指标、推理吞吐和延迟 | 无法验证 | 必须现场验证 |
| 被测推理服务、模型权重 | 由服务部署方准备 | 必须提前部署 |

进场前记录：目标机发行版/版本、内核、CPU 架构、GPU 型号/数量、驱动版本、Docker 或 nerdctl/containerd/CNI、设备运行时版本、推理 API 和模型名称、指标端口、日志路径。DCGM/HCU Exporter 版本需要与现场驱动和指标契约兼容；本机能成功下载镜像不能证明兼容性。

下文所有 Bash 步骤遇到错误都应先解决再继续。构建/导出需容纳镜像层、缓存、镜像 tar 和最终交付包，磁盘需求会明显高于压缩包大小。

## 2. 在线机：获取工程并建立交付目录

准备 Git、curl、tar、gzip、sha256sum、Python 3.9+、Docker Engine/CLI。安装方式使用该 Linux 发行版支持的渠道。Docker daemon 需已运行，当前账户需有操作权限。

```bash
set -euo pipefail
uname -m                  # 本流程预期 x86_64
python3 --version         # >= 3.9
docker info
df -h .

mkdir -p /home/llm-bench-build
cd /home/llm-bench-build
git clone https://github.com/shangbinbin/LLM-Inference-Benchmark.git
cd /home/llm-bench-build/LLM-Inference-Benchmark
pwd  # /home/llm-bench-build/LLM-Inference-Benchmark
git status --short
git rev-parse HEAD
python3 -m unittest discover -s tests -v

mkdir -p generated/offline-bundle/{images,metadata,tokenizer,site,runtime,drivers}
git rev-parse HEAD > generated/offline-bundle/metadata/repository-commit.txt
git archive --format=tar --prefix=LLM-Inference-Benchmark/ HEAD \
  | gzip > generated/offline-bundle/repository.tar.gz
```

后续在线步骤均在这个仓库根目录执行。已有 checkout 可以直接使用；如需指定发布版本，先切换到选定提交再归档和构建。`git archive HEAD` 只包含已提交内容：不要用包含未提交代码改动的工作区构建，却交付另一个版本的源码。运行数据、`.git`、`state.json`、`suite.lock` 不应复制到目标机。

## 3. 在线机：准备通用 GPU 现场配置模板

执行位置：在线源码根目录 `/home/llm-bench-build/LLM-Inference-Benchmark`。下面从完整 GPU41 示例生成交付配置，然后改为通用的小负载底稿。生成器不检查本地 GPU/tokenizer、不接触实际服务，输出存在时拒绝覆盖。已有现场配置应直接维护它，不重新生成覆盖。

```bash
cd /home/llm-bench-build/LLM-Inference-Benchmark
python3 scripts/create_site_config.py --runtime docker \
  --output generated/offline-bundle/site/site.json
# 若离线统一使用 nerdctl，上述生成命令替换为：
# python3 scripts/create_site_config.py --runtime nerdctl --namespace benchmark \
#   --output generated/offline-bundle/site/site.json

python3 - <<'PY'
import json
from pathlib import Path
path = Path('generated/offline-bundle/site/site.json')
cfg = json.loads(path.read_text())
cfg.update(project='llm-bench-gpu', task='gpu-inference', simulation=False,
           platform='linux/amd64', offline=True, legacy_seccomp_unconfined=True)
cfg['storage'] = {'root':'generated/gpu-site', 'logs':'/srv/inference-logs',
                  'tokenizer':'assets/tokenizer'}
cfg['gpu'].update(vendor='nvidia', deployment='local',
                  nodes=[{'name':'gpu-node', 'gpus':1}])
cfg['inference'] = {'base_url':'http://REPLACE_INFERENCE_HOST:30000/',
                    'model':'REPLACE_MODEL', 'api':'chat', 'engine':'auto'}
cfg['topology'] = {'kind':'single', 'tp':1, 'pp':1}
cfg['log_sources'] = [{'path':'server.log', 'role':'master', 'replica':'replica-0'}]
cfg['benchmark'].update(input_length=32, output_length=16, request_count=8,
                        concurrency=2, warmups=0, retry=1, mode='perf')
path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
PY
```

site.json 同时保留 `images.nvidia_exporter` 与 `images.hygon_exporter`，具体版本以现场驱动兼容性为准。vendor=nvidia 只是可修改的起始值，在线镜像选择不由这个值决定；第 5 节默认仍准备两类 Exporter。离线选择海光时改为 hygon，不需要重新构建 CPU 镜像。

现场地址可保留占位符直到离线填写。不要在在线 CPU 机上运行生产 up 或依赖 auto 检测设备。此底稿使用 host_metrics=container，采集容器视角；真实主机指标需另设 host 和可达 node_exporter_target，详见第 10 节。

模板的 offline=true 用于最终运行，不影响下面直接 docker build；正式 llm-bench images build 会校验现场参数且拒绝离线构建。联网侧直接构建不依赖目标 GPU、服务地址和 tokenizer。修改镜像版本或标签时，应在第 5 节拉取前更新交付 site.json，两个项目镜像的标签也必须与第 4 节构建一致。

## 4. 在线机：构建两个项目镜像

执行位置：**在线机源码根目录** `/home/llm-bench-build/LLM-Inference-Benchmark`。开始本节前执行：

```bash
cd /home/llm-bench-build/LLM-Inference-Benchmark
test -f scripts/benchmark_stack.py
```


以下提交、SHA-256 和镜像标签对应本仓库当前版本。更新仓库后，应与 `scripts/benchmark_stack.py`、`containers/aisbench/Dockerfile` 及现场 images 配置重新核对，不要绕过校验。

```bash
set -euo pipefail
docker build --network=host --platform linux/amd64 \
  -t llm-benchmark-toolbox:local-amd64 \
  -f containers/toolbox/Dockerfile . \
  2>&1 | tee generated/offline-bundle/metadata/build-toolbox.log

mkdir -p generated/aisbench-offline-build
curl --fail --location \
  https://codeload.github.com/AISBench/benchmark/tar.gz/e330dc134c33f47671926144d9c143b554bc71cc \
  --output generated/aisbench-offline-build/source.tar.gz
printf '%s  %s\n' \
  87a5434c8fa8b78aa57b588f00fbfb1861435d602496f6473b4a9dece94647c2 \
  generated/aisbench-offline-build/source.tar.gz | sha256sum -c -
cp containers/aisbench/Dockerfile generated/aisbench-offline-build/Dockerfile
docker build --network=host --platform linux/amd64 \
  -t llm-benchmark-aisbench:e330dc1-amd64 \
  generated/aisbench-offline-build \
  2>&1 | tee generated/offline-bundle/metadata/build-aisbench.log

docker run --rm --pull=never --network none --platform linux/amd64 \
  --entrypoint cat llm-benchmark-aisbench:e330dc1-amd64 \
  /opt/aisbench/dependencies.txt \
  > generated/offline-bundle/metadata/aisbench-dependencies.txt
```

成功标准：两个 build 均以 0 退出，AISBench 构建中的 `pip check` 通过。首次构建会下载基础镜像、Debian 包、PyTorch CPU 和 Python 依赖，耗时取决于网络。

在线网络需能访问 Docker Hub 及其下载域名、`codeload.github.com`、Debian 源、清华 PyPI（`pypi.tuna.tsinghua.edu.cn`）、PyTorch CPU 下载站和第 5 步配置的镜像仓库（默认华为云镜像站、harbor.sourcefind.cn:5443）及实际下载域名。企业代理需要同时配置 Docker daemon 拉取和构建步骤的代理/CA 信任；仅宿主机 curl 正常不能证明容器构建正常。遇到证书错误应安装可信 CA，保持 TLS、包签名及源码哈希校验，不使用跳过校验参数。

## 5. 在线机：按交付清单准备 NVIDIA/DCU Exporter 并导出

执行位置：在线源码根目录。镜像角色和选择由 [offline-images.json](../configs/delivery/offline-images.json) 管理，实际镜像引用从 `--site` 指定文件的 images 字段读取。配置默认如下：

```json
{
  "schema_version": 1,
  "gpu_exporters": {"nvidia": "nvidia_exporter", "hygon": "hygon_exporter"},
  "selected_gpu_exporters": ["nvidia", "hygon"],
  "additional_images": []
}
```

| 厂商选择 | site.images 字段 | 当前默认镜像 |
| --- | --- | --- |
| nvidia | nvidia_exporter | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nvidia/dcgm-exporter:4.8.4` |
| hygon（海光 DCU） | hygon_exporter | `harbor.sourcefind.cn:5443/hcu/admin/base/hcu-exporter:v3.0.0` |

先查看计划，再执行下载/检查/导出：

```bash
python3 scripts/prepare_offline_images.py \
  --site generated/offline-bundle/site/site.json --plan
python3 scripts/prepare_offline_images.py \
  --site generated/offline-bundle/site/site.json
```

默认包含 **9 个不同镜像**：本地已构建的 toolbox/AISBench、5 个公共观测镜像、2 个设备 Exporter。脚本不拉取两个项目自建镜像；对其余 7 个执行 Docker pull，再逐个核对 linux/amd64，成功后 docker image save。`--plan` 不调用 Docker、不访问 GPU/tokenizer 或服务、不写交付文件；site 中 offline=true 不会阻止这个明确的**在线交付工具**拉取。

输出为 `generated/offline-bundle/images/images-linux-amd64.tar`、`metadata/images.json` 和 `metadata/image-plan.json`。最后一项记录实际选中的 Exporter 与镜像角色。镜像 ID、架构与导出结果需以本机成功执行为准；本次工程改动不代表已成功从两个仓库下载镜像或验证真实硬件。

仅为已知单厂商交付时，可显式覆盖默认选择，下面按需要选一条执行：

```bash
# 只准备 NVIDIA：7 个通用镜像 + DCGM，共 8 个
python3 scripts/prepare_offline_images.py \
  --site generated/offline-bundle/site/site.json --exporters nvidia
# 只准备海光 DCU：7 个通用镜像 + HCU，共 8 个
python3 scripts/prepare_offline_images.py \
  --site generated/offline-bundle/site/site.json --exporters hygon
```

这些命令会重写同一输出归档，应在最终校验打包之前选择并执行，不要把两次单厂商导出当成归档合并。若后续离线需要任意切换两类设备，保留默认双厂商交付。没有 GPU 不妨碍拉取；选中任一仓库授权、网络、标签或架构检查失败时先解决，不能静默略过所选 Exporter。其他额外运行镜像可填 additional_images（完整镜像引用，会检查、去重并导出）；模型权重、日志和 bind mount 内容需单独交付。不要用 docker export 代替 image save。

### 5.1 其他 GPU 的扩展入口

复制交付配置后，可在 gpu_exporters 增加 `厂商名 → site.images 字段名`，在现场 site.images 增加对应镜像，selected_gpu_exporters 中选中它，然后以 `--config 自定义交付清单.json --site 现场配置.json` 查看计划和导出。也可先通过 additional_images 携带其他厂商工具镜像。

**这是镜像物料入口，不会自动增加第三种 GPU 的运行支持。** 当前真实运行仅接受 nvidia/hygon；新厂商还需扩展 `scripts/benchmark_stack.py` 的 ADAPTERS、厂商校验/识别和 Exporter 启动参数，配置 `configs/gpu/<厂商>/` 的采集规则与指标契约，并同步 `observability_labels.py`、`task_dashboard.py` 的标签和面板逻辑后验证。不能只增加镜像或把未知 vendor 改写成 nvidia/hygon 来启动。

## 6. 在线机：准备真实 tokenizer 并验证

执行位置：**在线机源码根目录** `/home/llm-bench-build/LLM-Inference-Benchmark`。开始本节前执行：

```bash
cd /home/llm-bench-build/LLM-Inference-Benchmark
test -f scripts/benchmark_stack.py
```


取得与被测服务模型版本匹配的完整 tokenizer 目录，不要使用 `configs/fixtures/mock-tokenizer` 进行真实性能测试。可能需要 `tokenizer_config.json`、`tokenizer.json`、词表、merges、special tokens、模型 config 或 chat template 等文件。

下面只需替换源目录。先解引用软链接打包，再从归档解出一份验证，避免 Hugging Face snapshot 指向外部 blobs 的软链接在离线现场失效。

```bash
TOKENIZER_SOURCE=/absolute/path/to/actual-model-tokenizer  # 修改为实际目录
test -f "$TOKENIZER_SOURCE/tokenizer_config.json"
tar -chzf generated/offline-bundle/tokenizer/model-tokenizer.tar.gz \
  -C "$TOKENIZER_SOURCE" .

TOKENIZER_CHECK_DIR=$(mktemp -d "$PWD/generated/tokenizer-check.XXXXXX")
tar -xzf generated/offline-bundle/tokenizer/model-tokenizer.tar.gz \
  -C "$TOKENIZER_CHECK_DIR"
docker run --rm --pull=never --platform linux/amd64 --network none \
  --mount "type=bind,src=$TOKENIZER_CHECK_DIR,dst=/tokenizer,readonly" \
  -e OPENBLAS_NUM_THREADS=1 -e OMP_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 -e NUMEXPR_NUM_THREADS=1 \
  -e TOKENIZERS_PARALLELISM=false \
  --entrypoint python llm-benchmark-aisbench:e330dc1-amd64 \
  -c 'from transformers import AutoTokenizer; t=AutoTokenizer.from_pretrained("/tokenizer", local_files_only=True, trust_remote_code=False); print(type(t).__name__, len(t.encode("offline tokenizer check")))'
```

tokenizer 验证命令显式限制 OpenBLAS、OpenMP、MKL、NumExpr 的计算线程数为 1，并关闭 tokenizer 内部并行。这些参数可用于已导入的旧 AISBench 镜像，无需重新构建；设置仅对当前容器有效。正式启动器已在单次、顺序用例和 SLO 搜索中统一传入同样的环境变量，新构建的 AISBench 镜像也包含这些默认值。

这些设置避免导入 NumPy/Transformers 时按宿主机 CPU 数量创建大量线程，不会把 HTTP 请求并发数改为 1，也不改变远端 GPU 推理的并行设置。若仍报 pthread_create Operation not permitted，需检查容器运行时、安全策略和资源限制；不能据此认定 tokenizer 文件或 NumPy 安装损坏。高并发测试仍应确认压测客户端 CPU 没有成为瓶颈。

成功标准：无网络容器能够加载 tokenizer，并输出类型和编码长度。失败时补齐文件/依赖并重新验证；当前流程不自动启用远程自定义代码。归档应只装 tokenizer 所需物料，不要无意打包整个模型权重目录。当前合成 string/tokenid 数据在本地生成，无需另行下载数据集。

### 6.1 无 GPU 测试：从 ModelScope 下载 Qwen3-0.6B tokenizer

执行位置仍为在线机源码根目录 `/home/llm-bench-build/LLM-Inference-Benchmark`，不是虚拟环境目录，也不是模型下载目录。`MS_PYTHON` 指向虚拟环境解释器，无需 cd 或 source activate。

在线 CPU 机器可下载 `Qwen/Qwen3-0.6B` 的真实 tokenizer，用于下一节模拟验收，无需 GPU、模型权重或本机 PyTorch。它只验证客户端和观测链路；生产交付仍须使用与实际被测模型匹配的 tokenizer。以下使用独立虚拟环境，不改变 AISBench 镜像依赖：

```bash
python3 -m venv generated/modelscope-venv
MS_PYTHON="$PWD/generated/modelscope-venv/bin/python"
if ! "$MS_PYTHON" -c 'import modelscope' >/dev/null 2>&1; then
  "$MS_PYTHON" -m pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple modelscope
fi
"$MS_PYTHON" - <<'PYTHON'
from pathlib import Path
from modelscope.hub.snapshot_download import snapshot_download

target = Path('generated/tokenizers/Qwen3-0.6B').resolve()
snapshot_download(
    model_id='Qwen/Qwen3-0.6B',
    revision='master',
    local_dir=str(target),
    allow_file_pattern=[
        'tokenizer*', 'vocab.json', 'merges.txt', 'special_tokens_map.json',
        'added_tokens.json', 'config.json', 'chat_template*',
    ],
)
for name in ('tokenizer_config.json', 'tokenizer.json'):
    if not (target / name).is_file():
        raise RuntimeError('Missing tokenizer file: ' + name)
print('Tokenizer directory:', target)
PYTHON

docker run --rm --pull=never --platform linux/amd64 --network none \
  --mount "type=bind,src=$PWD/generated/tokenizers/Qwen3-0.6B,dst=/tokenizer,readonly" \
  -e OPENBLAS_NUM_THREADS=1 -e OMP_NUM_THREADS=1 \
  -e MKL_NUM_THREADS=1 -e NUMEXPR_NUM_THREADS=1 \
  -e TOKENIZERS_PARALLELISM=false \
  --entrypoint python llm-benchmark-aisbench:e330dc1-amd64 \
  -c 'from transformers import AutoTokenizer; t=AutoTokenizer.from_pretrained("/tokenizer", local_files_only=True, trust_remote_code=False); print(type(t).__name__, t.encode("Qwen3 tokenizer CPU test"))'
```

需 Python 3.9+ 且具备 venv；在线机须能访问清华 PyPI、ModelScope 及其实际文件下载域名。只下载允许列表中的 tokenizer/config 文件，不下载 `.safetensors` 等权重。`master` 为可变版本，需要复现时将 revision 改为已记录的 ModelScope 提交或标签并保留下载目录。最后一条命令使用已构建的 AISBench 镜像在无网络条件下验证加载，不会拉取镜像。

若将该 tokenizer 作为本次交付测试物料，**必须额外生成第 10 节要解包的归档**。仍在在线机源码根目录执行下面命令；只完成 ModelScope 下载不会生成 model-tokenizer.tar.gz：

```bash
cd /home/llm-bench-build/LLM-Inference-Benchmark  # 使用实际在线源码根目录
TOKENIZER_SOURCE="$PWD/generated/tokenizers/Qwen3-0.6B"
test -f "$TOKENIZER_SOURCE/tokenizer_config.json"
test -f "$TOKENIZER_SOURCE/tokenizer.json"
mkdir -p generated/offline-bundle/tokenizer
tar -chzf generated/offline-bundle/tokenizer/model-tokenizer.tar.gz \
  -C "$TOKENIZER_SOURCE" .
ls -lh generated/offline-bundle/tokenizer/model-tokenizer.tar.gz
```

然后执行第 6 节解包后的无网络加载检查，以及第 8 节校验和、外层交付包重新制作。把新的完整交付包送到离线机；若单独补传 tokenizer 归档，也应携带其 SHA-256 校验文件并在离线机核对。不要用 Qwen3 tokenizer 替代其他实际模型的 tokenizer。

## 7. 可选：在线机执行无 GPU 模拟验收

执行位置：**在线机源码根目录** `/home/llm-bench-build/LLM-Inference-Benchmark`。开始本节前执行：

```bash
cd /home/llm-bench-build/LLM-Inference-Benchmark
test -f scripts/benchmark_stack.py
```


先完成第 6.1 节 Qwen3-0.6B tokenizer 下载和本地加载验证。这一步验证客户端与观测软件链路，可在没有 GPU 的构建机运行。使用独立配置、项目名和数据目录，不覆盖生产 site.json；模拟性能数值不能作为真实 GPU 性能报告。

```bash
python3 - <<'PY'
import json
from pathlib import Path
cfg = json.loads(Path('configs/stacks/mac-offline.json').read_text())
site = json.loads(Path('generated/offline-bundle/site/site.json').read_text())
cfg.update(project='llm-bench-online-smoke', platform='linux/amd64', offline=True, legacy_seccomp_unconfined=True,
           simulation=True, runtime=['docker'])
cfg['images'] = site['images']
cfg['gpu']['vendor'] = 'nvidia'
cfg['storage'] = {'root': 'generated/online-smoke',
                  'logs': 'generated/online-smoke-logs',
                  'tokenizer': 'generated/tokenizers/Qwen3-0.6B'}
Path('generated/online-smoke.json').write_text(json.dumps(cfg, indent=2) + '\n')
PY
python3 scripts/benchmark_stack.py check-images --config generated/online-smoke.json
python3 scripts/benchmark_stack.py up --config generated/online-smoke.json
python3 scripts/verify_simulation.py --config generated/online-smoke.json
cp generated/online-smoke/verification.json \
  generated/offline-bundle/metadata/online-simulation-verification.json
python3 scripts/benchmark_stack.py down --config generated/online-smoke.json
```

此模拟模板的端口 33003、39093、33103 需空闲，与生产模板端口不同。设置了 Docker 代理时，应让容器间的 `fixture`、`node-exporter`、`loki`、`alloy`、`prometheus`、`grafana` 等本地服务名走本地网络；外部下载继续使用所需代理。离线标志不会自动清除 Docker 全局代理设置，也不等于防火墙断网测试。

## 8. 在线机：补齐宿主机安装包、校验并打包

执行位置：**在线机源码根目录** `/home/llm-bench-build/LLM-Inference-Benchmark`。开始本节前执行：

```bash
cd /home/llm-bench-build/LLM-Inference-Benchmark
test -f scripts/benchmark_stack.py
```


如果离线机已经具备并验证了所选容器运行时、Python、驱动和设备运行库，可记录版本后跳过安装包收集。否则需要按**离线目标系统**准备完整依赖：

- `runtime/`：所选 Docker Engine/CLI 或 nerdctl/containerd/OCI 后端/CNI、Python 及依赖安装包和安装说明。
- `drivers/`：匹配 GPU/目标内核的 对应硬件的驱动及运行库（NVIDIA 为驱动/Container Toolkit；海光为 DCU 驱动、hy-smi、hyhal 等），以及依赖和安装说明。
- `site/`：site.json，以及需要的批量用例或 SLO 搜索 JSON。
- `metadata/`：目标 OS/内核/架构、包版本、兼容性说明和验收记录。

deb/rpm 需要在匹配发行版、版本、架构的环境或离线包源中收集依赖；需要编译内核模块的驱动还需匹配的内核头文件和编译依赖。不存在对所有 Linux 通用的一条离线驱动安装命令。GPU 构建机不是必需条件，但版本匹配和现场设备验证是必需条件。

物料放齐后生成内部清单和外层包校验和：

```bash
python3 - <<'PY'
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
(bundle/'SHA256SUMS').write_text('\n'.join(lines) + '\n')
PY
(cd generated/offline-bundle && sha256sum -c SHA256SUMS)
tar -czf generated/llm-bench-offline-amd64.tar.gz -C generated offline-bundle
(cd generated && sha256sum llm-bench-offline-amd64.tar.gz \
  > llm-bench-offline-amd64.tar.gz.sha256)
```

将 `generated/llm-bench-offline-amd64.tar.gz` 和同名 `.sha256` 文件一起带入离线现场。物料修改后必须重新生成校验和与外层归档。

## 9. 离线 GPU 机：校验、导入和设备检查

以下在 NVIDIA 或海光 DCU 目标节点执行。先将第 8 节归档与 .sha256 复制到新的交付目录 `/home/llm-bench-delivery`，再校验解包；不是在在线仓库 generated 下操作。已有交付目录不要重复解包覆盖现场改动。

```bash
set -euo pipefail
cd /home/llm-bench-delivery
sha256sum -c llm-bench-offline-amd64.tar.gz.sha256
tar -xzf llm-bench-offline-amd64.tar.gz
(cd offline-bundle && sha256sum -c SHA256SUMS)
uname -m
python3 --version
```

按离线运行时选择一个导入代码块，导入之后再按第 10 节修改实际 site.json 的 runtime。双厂商归档可全部导入，不会因此启动容器：

```bash
# Docker
cd /home/llm-bench-delivery
docker info
docker image load -i offline-bundle/images/images-linux-amd64.tar
```

```bash
# 或 nerdctl：镜像与后续 site.json 使用同一 namespace
cd /home/llm-bench-delivery
nerdctl --namespace benchmark info
nerdctl --namespace benchmark load -i offline-bundle/images/images-linux-amd64.tar
```

随后在对应代码块中检查 GPU 宿主机：

| 目标节点 | 检查与准备 |
| --- | --- |
| NVIDIA | nvidia-smi 正常；Docker 已注册 nvidia runtime，或 nerdctl 的 --gpus all 与 nvidia-container-cli 可用 |
| 海光 DCU | hy-smi 正常；HCU v3 所需 /dev、/opt/hyhal、/etc/hostname、/etc/vdev 及驱动/运行库按实际部署准备，见 [海光说明](hygon-official-monitoring.md) |

工具缺失时按交付 runtime/、drivers/ 中匹配系统的安装说明补齐。不要在海光节点执行 nvidia-ctk，也不要把 NVIDIA 运行参数套到海光。安装或修改系统设备运行时可能影响现有服务，应按现场维护流程处理；本工程的配置切换本身不要求重启 Docker/containerd。

Docker 导入时，可按 metadata/images.json 核对 image ID 和架构；nerdctl 的 inspect 标识形式可能不同，核对平台、标签和可用的 manifest/config digest，后续 llm-bench images check 采用兼容检查。**不要用 Docker 中存在镜像来证明 nerdctl 已导入。** 默认清单的两类镜像应都在归档中；单厂商交付后切换到另一厂商之前需补齐镜像并重新交付，离线 up 不会自动拉取。

## 10. 离线机：解包源码、tokenizer 并填写现场参数

执行位置：开始时仍在 `/home/llm-bench-delivery`。第 10.1 节最后一条 cd 才切换到 `/home/llm-bench-runtime/LLM-Inference-Benchmark`；后续配置编辑和启动均在该源码根目录。

本节选择**解包交付包中的固定版本源码**。它不会把文件解压到你当前已有的源码目录；例如当前在 `/home/LLM-Inference-Benchmark-new/generated`，原命令会在其下再创建 `work/LLM-Inference-Benchmark`。请先明确交付目录和安装目录，不能把这些示例相对路径直接套用到任意工作目录。

### 10.1 使用源码归档部署到新目录

`BENCH_BUNDLE_DIR` 指向第 9 节外层归档解出的 offline-bundle 目录，内部应包含 repository.tar.gz、tokenizer/、site/ 等内容。下面给出与第 9 节一致的绝对路径；如果使用其他目录，请同时修改这两个变量。`BENCH_INSTALL_DIR` 使用一个尚不存在的新目录，防止重复解包混合旧源码或 tokenizer。

```bash
set -euo pipefail
cd /home/llm-bench-delivery
BENCH_BUNDLE_DIR=/home/llm-bench-delivery/offline-bundle
BENCH_INSTALL_DIR=/home/llm-bench-runtime  # 必须是尚不存在的新目录

# 完整执行本代码块；任一文件缺失就停止，不创建安装目录。
for artifact in repository.tar.gz tokenizer/model-tokenizer.tar.gz site/site.json; do
  if [ ! -f "$BENCH_BUNDLE_DIR/$artifact" ]; then
    printf 'MISSING: %s\n停止解包，请先补齐交付物。\n' "$BENCH_BUNDLE_DIR/$artifact" >&2
    exit 1
  fi
  printf 'OK: %s\n' "$BENCH_BUNDLE_DIR/$artifact"
done
# 第 2 节归档使用 --prefix=LLM-Inference-Benchmark/；先确认目录结构。
tar -tzf "$BENCH_BUNDLE_DIR/repository.tar.gz" | sed -n '1,5p'
# 输出应以 LLM-Inference-Benchmark/ 开头；若不同，先核对归档制作步骤。
mkdir -p "$(dirname "$BENCH_INSTALL_DIR")"
mkdir "$BENCH_INSTALL_DIR"
tar -xzf "$BENCH_BUNDLE_DIR/repository.tar.gz" -C "$BENCH_INSTALL_DIR"
BENCH_REPO_DIR="$BENCH_INSTALL_DIR/LLM-Inference-Benchmark"
test -f "$BENCH_REPO_DIR/scripts/benchmark_stack.py"
mkdir -p "$BENCH_REPO_DIR/assets/tokenizer" "$BENCH_REPO_DIR/generated/site"
tar -xzf "$BENCH_BUNDLE_DIR/tokenizer/model-tokenizer.tar.gz" \
  -C "$BENCH_REPO_DIR/assets/tokenizer"
cp "$BENCH_BUNDLE_DIR/site/site.json" "$BENCH_REPO_DIR/generated/site/site.json"
cd "$BENCH_REPO_DIR"
pwd  # /home/llm-bench-runtime/LLM-Inference-Benchmark
test -f assets/tokenizer/tokenizer_config.json
```

不要将 `model-tokenizer.tar.gz` 解到源码根目录，也不要再增加一层模型目录。第 6 节以 `tar -C "$TOKENIZER_SOURCE" .` 打包，解包后的文件应直接位于 `assets/tokenizer/`。第 6.1 节 ModelScope 下载得到的是 `generated/tokenizers/Qwen3-0.6B/` 目录，**不会自动生成这个 tokenizer 归档**；若选它作为测试物料，须按第 6 节完成打包后重新生成交付包校验和。

`git archive` 的源码归档不含 .git，解出的仓库不能通过 git pull 更新；若要使用后续提交，请重新交付匹配版本的源码；仅配置/运行时改动可复用兼容镜像，涉及镜像内容变更时再重新导出，避免旧交付包覆盖现场工程。

解包完成后的布局：

```text
/home/llm-bench-runtime/LLM-Inference-Benchmark/
├── scripts/benchmark_stack.py
├── containers/
├── configs/
├── assets/tokenizer/tokenizer_config.json
└── generated/site/site.json
```

### 10.2 已有完整源码目录时

若你已在离线机准备了与交付版本匹配的完整源码，可以跳过 repository.tar.gz 解包，直接进入该源码根目录（应能看到 scripts/、configs/、containers/），创建 `generated/site/` 并复制现场配置。将 tokenizer 解到一个新的独立目录，并让配置中的 `storage.tokenizer` 指向实际目录；也可填写已存在的完整 tokenizer 目录的绝对路径。不要在已有目录上重复解包，也不要保留无效的 `assets/tokenizer` 默认值。

例如源码已在 `/home/LLM-Inference-Benchmark-new` 时，先执行 `cd /home/LLM-Inference-Benchmark-new`，确认 `scripts/benchmark_stack.py` 存在，再复制配置和解包 tokenizer；第 11、12 节的 cd 也相应改为此路径。不要使用 `/home/LLM-Inference-Benchmark-new/generated` 作为源码根目录。

<a id="gpu-selection"></a>

### 10.3 填写现场配置并选择 NVIDIA 或海光 DCU

需要逐字段解释和完整启动顺序时，使用 [site.json 配置与启动压测手册](site-config-and-benchmark.md)，涵盖设备、引擎、占位符、Token、单档/多测试例和结果位置。

生成或复制只需选择一种：第 10.1 节已经将交付 site.json 复制到 `generated/site/site.json`，直接编辑它。若交付包没有现场配置，按 [离线生成步骤](offline-deployment.md#offline-site)从仓库完整示例生成（不要覆盖已有文件），再修改。不要跳过本节直接运行占位模板。

**最关键的选择是 `gpu.vendor`，不是只修改 images 中的镜像名。** 在源码根目录执行下面脚本，先将 BENCH_GPU_VENDOR 改成目标硬件，BENCH_RUNTIME 改成已导入镜像的运行时：

```bash
python3 - <<'PY'
import json
from pathlib import Path

BENCH_GPU_VENDOR = 'nvidia'  # NVIDIA 用 nvidia；海光 DCU 用 hygon，不是 dcu
BENCH_RUNTIME = 'docker'    # 或 nerdctl
BENCH_NAMESPACE = 'benchmark'
if BENCH_GPU_VENDOR not in ('nvidia', 'hygon') or BENCH_RUNTIME not in ('docker', 'nerdctl'):
    raise SystemExit('请先完成新厂商/运行时适配，不要使用未知选择')
path = Path('generated/site/site.json')
cfg = json.loads(path.read_text())
cfg['gpu']['vendor'] = BENCH_GPU_VENDOR
cfg['gpu']['deployment'] = 'local'
cfg['gpu']['nodes'] = [{'name':'gpu-node', 'gpus':1}]  # 修改实际节点名、卡数
cfg['runtime'] = ['docker'] if BENCH_RUNTIME == 'docker' else ['nerdctl','--namespace',BENCH_NAMESPACE]
cfg.pop('runtime_kind', None)
path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
print('Runtime：', cfg['runtime'], 'GPU：', cfg['gpu']['vendor'])
PY
```

这段是为尚未启动的本机单节点部署选择参数，保留两类 images、服务和负载配置；如需保留自定义 socket/snapshotter/sudo 包装，直接编辑 runtime，不运行脚本替换。已有 Exporter 应改 deployment=external、nodes[].target，而不是按本机 local 重新部署。

| 字段 | 现场填写方式 |
| --- | --- |
| gpu.vendor / deployment | nvidia 或 hygon；本机单节点 local；远端/已有 Exporter 用 external 并填写逐节点 target |
| gpu.nodes[].name / gpus | 实际节点标识、卡数，供 GPU 联动筛选；单机 local 只有一个节点 |
| images.nvidia_exporter / hygon_exporter | 保留已经导入的镜像名；当前选择哪类 GPU 就使用对应字段；另一个字段不要求对应容器启动 |
| runtime | Docker 或 nerdctl + 明确 namespace，与第 9 节导入一致 |
| offline / simulation / platform | true / false / linux/amd64；legacy_seccomp_unconfined 默认为 true |
| project / task | 本文 llm-bench-gpu / gpu-inference；project 区分资源，task 区分业务指标与日志 |
| storage.root | 本文 generated/gpu-site；与日志目录分开，持久化状态、指标、日志数据及结果 |
| storage.logs / log_sources | 服务真实节点日志目录和活跃文件，如 /srv/inference-logs/server.log；角色 master、副本 replica-0，路径相对 storage.logs |
| storage.tokenizer | assets/tokenizer 或被测模型真实 tokenizer 目录，不能使用模拟词表 |
| inference.base_url / model / api / engine | 实际服务根 URL（可含网关前缀）、模型 ID、chat/completions、vllm/sglang/auto |
| inference.api_key_env | 需要 Bearer Token 时只写变量名，并在启动终端导出纯 Token；无鉴权则省略 |
| topology | 单实例 single，tp/pp 与服务实际部署一致；缺省 metrics 从 base_url 派生，独立指标端点才设覆盖值 |
| host_metrics / node_exporter_target | 示例 container，只采容器视角；host 模式须提供容器可达的节点 IP，端口跟随 ports.node-exporter |
| ports | 默认高位端口须空闲，Grafana 33000、Prometheus 39090、Loki 33100；按实际需求修改 |
| benchmark | 本文先使用 32 输入、16 输出、8 正式请求、并发 2；参数必须被后端接受 |

相对 storage 路径按工程根目录解析。日志由服务提前写到节点可访问路径，启动器负责挂载给 Alloy/logrotate；不会自动进入推理 Pod 读取日志，也不要用空文件冒充。避免与已有轮转任务同时管理同一文件。容器网络需能访问推理服务、应用 metrics、各 Exporter；不能将整个栈设为 --network none。

真实日志路径必须替换通用示例：storage.logs=/srv/inference-logs 配合 path=server.log 只会读取该实际文件。真实模式默认启动前检查；不要为绕过缺失错误创建空文件冒充服务日志。用 `./bin/llm-bench logs check --config generated/site/site.json` 定位，完整案例见 [日志路径修复](loki-log-troubleshooting.md)。

真实服务字段的完整修改脚本、网关统一 base_url、日志映射和 Token 步骤见 [离线对接说明](offline-deployment.md#real-service)。海光配置同样使用相同的推理 API 和任务标签，但其 Exporter 的设备路径、采集指标与 NVIDIA 不同。

#### 10.3.1 离线切换后实际会发生什么

| 模式 | 启动 gpu-exporter 的镜像 | 内部采集端口 / 代表指标 |
| --- | --- | --- |
| nvidia + local + simulation=false | images.nvidia_exporter，NVIDIA 设备注入与 basic collectors | 9400 / DCGM_FI_DEV_GPU_UTIL |
| hygon + local + simulation=false | images.hygon_exporter，海光设备/运行库挂载 | 16080 / hcu_memorycap_bytes |
| external | 不启动本机设备 Exporter，读取逐节点 target | 按厂商约定的真实 /metrics |
| simulation=true | 使用 toolbox fixture 模拟，不启动真实设备 Exporter | 模拟应用/GPU 指标，不代表真实硬件 |

对两类本机模式，宿主机的设备服务发布端口都由 ports.gpu-exporter 控制（默认 39400），映射到各自内部端口。llm-bench 只检查所选模式需要的镜像，按厂商生成 Prometheus job、设备标签和任务 Dashboard，不会因 images 同时含两类引用就启动两个采集容器。

已启动的栈切换厂商或运行时：**先使用原配置 down → 修改 site.json → 确认目标镜像/设备依赖就位 → plan/images check → up**。bench/status/down 读取原 manifest，编辑 vendor 不会热切换已有容器。切换服务或 GPU 前先正确停止当前压测；project/storage.root 应在 down 后再改。双厂商已交付时不需要重新打包或重建 CPU 镜像，仍需目标机真实设备和驱动可用。

### 10.4 离线机：使用已交付 tokenizer 验证线程与加载

执行位置：离线源码根目录。先完成第 10.3 节填写。下面读取所选 runtime/namespace、镜像和 tokenizer 路径，无需 GPU、真实服务或 Token，并通过正式启动器的环境/安全参数完成无网络本地检查：

```bash
python3 - <<'PY'
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path('scripts').resolve()))
from benchmark_stack import load_config, aisbench_environment, runtime_command

cfg = load_config(Path('generated/site/site.json'))
cfg['inference'].pop('api_key_env', None)  # 仅本次本地检查不传鉴权，不修改文件
code = """
import threading
thread = threading.Thread(target=lambda: print('THREAD OK'))
thread.start()
thread.join()
from transformers import AutoTokenizer
tokenizer = AutoTokenizer.from_pretrained('/tokenizer', local_files_only=True, trust_remote_code=False)
text = '你好，这是离线 tokenizer 验证。Hello, Qwen!'
tokens = tokenizer.encode(text, add_special_tokens=False)
assert tokens, 'Empty token IDs'
print('Tokenizer:', type(tokenizer).__name__)
print('Token count:', len(tokens))
print('Decoded:', tokenizer.decode(tokens))
print('PASS: offline tokenizer validation')
"""
args = ['run','--rm','--platform',cfg['platform'],'--network','none',
        *aisbench_environment(cfg), '--mount',
        'type=bind,src='+cfg['storage']['tokenizer']+',dst=/tokenizer,readonly',
        '--entrypoint','python',cfg['images']['aisbench'],'-c',code]
subprocess.run(runtime_command(cfg, args), check=True)
PY
```

成功标准为 THREAD OK、tokenizer 类型、编码数量及 PASS，退出码为 0。runtime_command 沿用 offline 禁拉取、legacy_seccomp_unconfined 和 nerdctl 适配；客户端环境限制 OpenBLAS 等线程并开启离线模型加载。它不会启用 GPU 注入或联系推理服务，也不会打印实际 Token。此检查只证明线程和 tokenizer 可加载，后续全链路仍需冒烟和真实服务请求验证。

offline=true 默认取消所有项目新容器的 seccomp 系统调用过滤，兼容旧运行时线程创建限制，不修改系统 Docker/containerd，也不影响其他项目；显式 false 可恢复，详见 [兼容说明](legacy-docker-threads.md)。

<a id="offline-smoke"></a>

### 10.5 离线全链路冒烟：生成、启动、验收和停止

**generated/offline-smoke.json 需要先生成，不随仓库预置。** 先按 [离线部署 4.3.1 的完整生成脚本](offline-deployment.md#offline-smoke)从 site.json 继承 runtime、镜像和已交付 tokenizer，生成独立模拟配置。它不复制真实服务地址、Token 或日志目录，不修改真实 site.json；模拟厂商与真实节点硬件无关。Docker/nerdctl 共用以下命令：

```bash
./bin/llm-bench plan --config generated/offline-smoke.json
./bin/llm-bench images check --config generated/offline-smoke.json
set -o pipefail
./bin/llm-bench up --config generated/offline-smoke.json \
  2>&1 | tee generated/offline-smoke-up.log
python3 scripts/verify_simulation.py --config generated/offline-smoke.json
./bin/llm-bench status --config generated/offline-smoke.json
```

up 不加 --no-benchmark，自动执行 8 次请求、并发 2 的随机 tokenid；verify_simulation 验证最新默认 up 的报告、Prometheus、GPU 模拟指标、Grafana 和 Loki。可选 --exercise-restart 验证轮转、采集重启和续读，只操作本模拟栈。真实性能不能用模拟结果代替。

Grafana 默认节点IP:33003，Prometheus 节点IP:39093，Loki 节点IP:33103，需确认端口空闲。冒烟原始日志在 generated/offline-smoke-logs/server.log；监控和结果在 generated/offline-smoke/data/，证据在 verification.json。已有冒烟栈再次发压使用 bench run，输出写到本次 suites/<运行目录>/single/client.log；verify_simulation 不以 suite 结果替代默认 up 结果。

完成后先保留诊断再停止：

```bash
./bin/llm-bench logs collect --config generated/offline-smoke.json
./bin/llm-bench down --config generated/offline-smoke.json
```

down 只删除本模拟项目拥有的容器和网络，保留 tokenizer、日志、指标和结果，不重启系统运行时或真实推理服务。完整路径表、实时日志及再次冒烟说明见 [离线冒烟全流程](offline-deployment.md#offline-smoke)。

<a id="real-benchmark"></a>

## 11. 离线 GPU 机：预检查、小规模压测与验收

执行位置：源码根目录。site.json 中应无 REPLACE_ 占位符，vendor 和 runtime 与目标节点一致；需要鉴权时先在当前终端导出 inference.api_key_env 所指变量（纯 Token），步骤见 [真实服务对接](offline-deployment.md#real-service)。

```bash
cd /home/llm-bench-runtime/LLM-Inference-Benchmark
./bin/llm-bench plan --config generated/site/site.json
./bin/llm-bench images check --config generated/site/site.json
./bin/llm-bench up --config generated/site/site.json --no-benchmark
./bin/llm-bench status --config generated/site/site.json
./bin/llm-bench bench run --config generated/site/site.json --requests 8 --concurrency 2
```

单机真实 local 模式 images check 需要 8 个镜像（7 个通用 + 所选设备 Exporter）；默认交付了 9 个不代表检查或启动 9 个。NVIDIA 启动 DCGM，海光启动 HCU，不启动另一厂商 Exporter 或 mock fixture；external 不启动本机设备 Exporter。就绪阶段要求应用指标和设备/主机 targets UP、真实 v1/models 可用，失败保留容器和日志，不回退成模拟或其他厂商。

| 验收项 | NVIDIA | 海光 DCU |
| --- | --- | --- |
| Prometheus 设备 job | nvidia-dcgm-exporter | hygon-hcu-exporter |
| 代表设备指标 | DCGM_FI_DEV_GPU_UTIL | hcu_memorycap_bytes，单位 bytes，不能当百分比 |
| 设备标签与面板 | task / gpu_node / gpu_model / gpu_index，NVIDIA 总览 | 同一筛选链，海光总览 |

Grafana 默认 `http://节点IP:33000`，Prometheus `http://节点IP:39090/targets`，按 ports 变化。任务选择 gpu-inference（或现场 task），日志查询 `{task="gpu-inference"}` 并筛选角色/副本。空闲利用率为 0 不能判定失败，未暴露的服务指标会显示 No data；海光旧 dcu 指标需要完成适配后才能使用默认面板。

本次 bench run 的结果在 `generated/gpu-site/data/results/suites/<运行目录>/suite-results.json`，详细进度在 single/client.log，性能报告在 single/results/<时间目录>/performances/aisbench-target/synthetic.json。正式请求应为 8、成功 8、失败 0，只检查当前目录，不用历史成功报告证明本次通过。verify_simulation 仅用于模拟栈，不能验收真实 NVIDIA/DCU。

需要初次 up 直接执行默认小负载时，也可在尚未启动栈时省略 --no-benchmark；其报告在 generated/gpu-site/data/results/<AISBench时间目录>/ 下。已有栈再次压测使用 bench，不重复 up。以上真实硬件验证均需现场执行，代码测试不代替驱动和 Exporter 兼容性验收。

## 12. 调整负载、多个用例、厂商切换和停机

第一次小规模请求仅用于连通性验证。可直接在已有栈覆盖本次负载，例如随机输入/输出 1024、1000 请求、并发 16：

```bash
./bin/llm-bench bench run --config generated/site/site.json \
  --input-len 1024 --output-len 1024 --requests 1000 --concurrency 16
```

多个不同输入输出、请求数和并发使用独立 cases.json，格式和生成脚本见 [多测试例](offline-deployment.md#multiple-cases)；site.json 保留共用环境和默认 benchmark，不把标量改成数组。已有观测栈运行：

```bash
./bin/llm-bench bench suite --config generated/site/site.json \
  --cases generated/site/cases.json
```

未启动栈时可用 up --cases 一次完成；不能同时加 --no-benchmark。详细进度在逐例 client.log，汇总在本次 suite-results.json，默认请求失败时停止后续例。

配置变更和厂商切换不热生效。修改基础服务、GPU、端口或 runtime 前先停止已有栈：

```bash
./bin/llm-bench logs collect --config generated/site/site.json
./bin/llm-bench down --config generated/site/site.json
# 编辑 vendor、节点、runtime、实际服务/日志等；Token 需要时重新导出
./bin/llm-bench plan --config generated/site/site.json
./bin/llm-bench images check --config generated/site/site.json
./bin/llm-bench up --config generated/site/site.json --no-benchmark
```

结束后只执行 logs collect/down 即可，不执行最后三条重新启动。down 保留日志、监控数据和报告，不停止真实推理服务。双厂商归档已导入时只选中其中一类；跨运行时还需在目标镜像存储/namespace 导入归档。无须为切换厂商重建 AISBench/toolbox；目标设备依赖、指标契约和目录权限必须满足。

CLI 定位、单档/清单/SLO、日志诊断详见 [离线压测流程](offline-deployment.md#real-benchmark)、[CLI 手册](cli.md)和[诊断包](offline-diagnostics.md)。完整源码包含 JSONL/ShareGPT 加载器，文件型用例需另行交付数据。记录实际 site/cases/search、结果目录、版本、硬件和驱动，用于验收与复现。

## 13. 常见故障定位

| 现象 | 优先检查/处理 |
| --- | --- |
| 在线无 GPU，提示无法识别厂商 | 不运行生产 up；在线仅准备物料，不用生产 auto 检测；离线明确 nvidia/hygon；构建采用第 4 步直接方式 |
| 在线 build 报占位符/tokenizer 错误 | 误用了现场配置入口；直接构建两个 Dockerfile，不需要现场路径 |
| 源码 SHA-256 不一致 | 停止构建，核对提交/下载内容；不修改期望哈希以强行通过 |
| 在线拉取或 pip/apt 失败 | 查看具体失败域名、代理、CA、仓库授权/标签；解决后重跑失败步骤 |
| 离线 Missing images / Wrong architecture | 按清单补齐 amd64 镜像，在在线侧重新 save；不能把 arm64 改标签冒充 |
| `could not select device driver` / `nvidia-smi` 失败 | 检查宿主机驱动、Toolkit 配置、Docker runtime；先检查第 9 节宿主机工具及设备运行时，再看本机 Exporter 启动/就绪错误 |
| DCGM profiling 模块 watch 指标失败 | 默认 gpu.nvidia_metrics_profile=basic，只读挂载仓库中的 dcgm/basic.csv；同步当前源码及 CSV 后重建项目容器，无需重做镜像；full 模式须先现场验证兼容 |
| 另一厂商镜像已导入却没有启动 | 正常：交付清单决定可用物料，site.gpu.vendor 决定实际容器；只应启动当前厂商，切换前 down |
| 海光 HCU 启动或指标失败 | 检查 hy-smi、/dev、hyhal/vdev 路径、镜像版本和 hcu_* 契约；旧 dcu-exporter 不自动兼容 |
| DCGM Exporter 启动失败 | `docker logs llm-bench-gpu-gpu-exporter`；nerdctl 用对应 namespace 的 logs；核对镜像/驱动兼容性与设备权限 |
| 采集或 API 就绪超时 | 确认容器内可达 IP、监听地址、端口、防火墙、DNS/代理；主机 curl 成功不代表容器成功 |
| `model-tokenizer.tar.gz: Cannot open: No such file or directory` | 交付包缺少 tokenizer 归档；第 6.1 节下载目录不等于归档，须完成打包并补传；源码已解包时只补齐 tokenizer，不重复创建安装目录 |
| OpenBLAS `pthread_create failed`，随后 NumPy 导入失败 | 使用第 6 节验证命令中的线程环境变量；实际压测使用当前启动器源码，旧镜像也由启动器传入限制；若仍失败，检查线程创建权限/资源限制 |
| tokenizer 离线加载失败 | 文件不全、外部软链接、自定义依赖或模型版本错误；回到第 6 步验证实际归档 |
| 挂载目录 Permission denied | 检查 UID/GID、目录权限、rootless 映射及 SELinux 策略；按服务最小权限处理，不递归放宽整个生产目录 |
| AISBench 非零退出或报告含失败请求 | 本次 `single/client.log`；默认 up 可查看 llm-bench-gpu-aisbench 日志；核对模型 ID、API 类型、请求参数、上下文长度、服务错误和负载 |
| Grafana 有指标但 Loki 无日志 | 检查服务是否实际写文件、路径/文件名、Alloy 读权限及已有轮转机制 |
| 已有 state / suite.lock | 检查任务状态；完成后用原配置 down，不盲删锁或状态文件 |

可用 `docker logs --tail 200 <容器名>` 或 `nerdctl --namespace benchmark logs --tail 200 <容器名>` 查看单组件错误；改过 project 时容器名前缀也相应变化。完整交付要求与离线模式行为见[离线部署总说明](offline-deployment.md)。

两个项目 Dockerfile 的 Debian 主仓库及安全更新仓库均使用清华 TUNA HTTPS 镜像：`https://mirrors.tuna.tsinghua.edu.cn/debian`、`https://mirrors.tuna.tsinghua.edu.cn/debian-security`。保留 bookworm、bookworm-updates、bookworm-security 及原有签名验证设置，兼容传统 sources.list 和 deb822 .sources 格式。构建机需能访问该域名并信任 HTTPS 证书；apt 索引下载失败时立即终止构建。Python 通用依赖统一使用清华 PyPI 索引 `https://pypi.tuna.tsinghua.edu.cn/simple`，包括 pip/setuptools/wheel、AISBench 及其构建依赖。PyTorch 固定 `torch==2.6.0+cpu`，通过 `--find-links https://download.pytorch.org/whl/cpu/torch/` 获取官方 CPU wheel；其余依赖仍由清华 PyPI 解析。构建时需同时允许清华 PyPI 及 PyTorch 官方下载域名。

旧 Docker 环境若最小 Python 线程测试失败、且仅在关闭 seccomp 后成功，可在 stack JSON 顶层显式设置 `"legacy_seccomp_unconfined": true`。离线模式默认开启、在线模式默认关闭，显式设 false 可关闭；开启后统一影响本工程新建的所有容器，包括监控组件、Exporter 和临时探测，不影响其他项目；适用范围、安全影响、重建容器及恢复步骤见 [旧 Docker 线程兼容说明](legacy-docker-threads.md)。

网关路径可统一配置 `inference.base_url`（如 `http://host:port/prefix/`），默认自动追加 `v1/chat/completions`、`v1/models` 和 `metrics`，应用指标默认复用推理 Bearer 鉴权；保留显式指标字段时以覆盖值为准。详见 [统一基础 URL](inference-auth.md#统一基础-url自动拼接推理与指标接口)。

需要从其他机器直接访问 Grafana 等服务时，使用运行本栈的节点 IP 加配置端口。所有 HTTP 服务的高位默认端口、port_bind_address、单项禁用及 host node_exporter 的监听规则见 [节点访问与端口配置](docker-stack.md#节点-ip-访问与可配置高位端口)。

本地 NVIDIA Exporter 默认使用基础指标采集模式 `gpu.nvidia_metrics_profile=basic`，避免现场已确认的默认 profiling 采集失败；基础列表随仓库挂载。切换 full 模式及缺失面板的说明见 [NVIDIA 指标配置](../configs/gpu/nvidia/README.md#本地-dcgm-默认使用已验证的基础指标)。
