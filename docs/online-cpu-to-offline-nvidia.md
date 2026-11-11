# 无 GPU 在线 Linux 构建，离线 NVIDIA 服务器压测操作手册

推理服务需要 Bearer Token 时，设置 `inference.api_key_env` 并导出对应环境变量；单次、顺序及 SLO 搜索均支持，详见 [推理鉴权说明](inference-auth.md)。离线升级需重新交付 toolbox 镜像和当前源码。

适用场景：在线构建机是 **x86_64 Linux，没有 NVIDIA GPU**；离线目标机是 **x86_64 Linux，有 NVIDIA GPU**。在在线机准备完整镜像和物料，在离线机运行 NVIDIA 采集与 AISBench 压测。

构建机不需要 NVIDIA 驱动、CUDA Toolkit 或 NVIDIA Container Toolkit。AISBench 镜像使用 CPU Torch；DCGM Exporter 镜像可以在没有 GPU 的机器上下载、保存，真正启动采集时才需要目标机的 GPU 和驱动。

本手册覆盖单台 NVIDIA 服务器、本机 Exporter、Docker 运行方式。真实推理服务由现场另行部署，本工程不部署模型权重或推理引擎。多机、外部 Exporter、海光、containerd 等场景见[离线部署总说明](offline-deployment.md)与[容器配置手册](docker-stack.md)。

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

| 物料/检查 | 在线无 GPU 机器 | 离线 NVIDIA 机器 |
| --- | --- | --- |
| 仓库源码、AISBench、toolbox | 获取并构建 | 解包、导入 |
| Prometheus、Grafana、Loki、Alloy、node_exporter、DCGM Exporter | 下载 amd64 镜像并导出 | 导入并启动 |
| 真实模型 tokenizer | 收集完整文件并验证无网络加载 | 解包并配置路径 |
| Docker、Python、NVIDIA 驱动、Container Toolkit | 按目标 OS 收集安装包（目标机已有可跳过） | 安装并检查 |
| 单元测试和无 GPU 模拟 | 可以执行 | 可选 |
| 真实 GPU 指标、推理吞吐和延迟 | 无法验证 | 必须现场验证 |
| 被测推理服务、模型权重 | 由服务部署方准备 | 必须提前部署 |

进场前记录：目标机发行版/版本、内核、CPU 架构、GPU 型号/数量、驱动版本、Docker/Container Toolkit 版本、推理 API 和模型名称、指标端口、日志路径。DCGM Exporter 标签需要与现场驱动兼容；本机能成功下载镜像不能证明兼容性。

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

## 3. 在线机：准备 NVIDIA 现场配置模板

执行位置：**在线机源码根目录** `/home/llm-bench-build/LLM-Inference-Benchmark`。开始本节前执行：

```bash
cd /home/llm-bench-build/LLM-Inference-Benchmark
test -f scripts/benchmark_stack.py
```


配置可以保留现场地址占位符直到离线机填写。明确指定 NVIDIA，避免在无 GPU 构建机上执行自动识别；先设置小规模验收负载，避免首次启动就施加大压力。

```bash
python3 - <<'PY'
import json
from pathlib import Path

cfg = json.loads(Path('configs/stacks/nvidia-single.json').read_text())
cfg.update(project='llm-bench-nvidia', platform='linux/amd64',
           simulation=False, offline=True, runtime=['docker'])
cfg['gpu']['vendor'] = 'nvidia'
cfg['gpu']['deployment'] = 'local'
cfg['storage'] = {
    'root': 'generated/nvidia-site',
    'logs': '/srv/inference-logs',
    'tokenizer': 'assets/tokenizer',
}
cfg['benchmark'].update(input_length=32, output_length=16,
                        request_count=8, concurrency=2, warmups=1, mode='perf')
Path('generated/offline-bundle/site/site.json').write_text(
    json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
PY
```

下面所有 `docker build` 命令均带 `--network=host`，使用 Linux 构建机网络执行 apt/pip；构建机本身须能访问软件源。详见 [构建网络说明](offline-deployment.md#32-构建两个项目镜像)。

模板中的 `offline: true` 用于最终离线运行，不影响下面的直接 `docker build`。不要在此时运行 `benchmark_stack.py build --config ...`：该入口会先检查占位符、本地 tokenizer，并生成现场路径；offline 模式也禁止它构建。下面的直接构建不依赖现场地址、路径或 GPU。

若现场要求不同 DCGM Exporter 版本，在下面拉取前修改 `site/site.json` 的 `images.nvidia_exporter`。若修改两个自建镜像的名称，第 4 步构建标签也必须同步修改。

## 4. 在线机：构建两个项目镜像

执行位置：**在线机源码根目录** `/home/llm-bench-build/LLM-Inference-Benchmark`。开始本节前执行：

```bash
cd /home/llm-bench-build/LLM-Inference-Benchmark
test -f scripts/benchmark_stack.py
```


以下提交、SHA-256 和镜像标签对应本仓库当前版本。更新仓库后，应与 `scripts/benchmark_stack.py`、`containers/aisbench/Dockerfile` 及 NVIDIA 模板重新核对，不要绕过校验。

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

在线网络需能访问 Docker Hub 及其下载域名、`codeload.github.com`、Debian 源、清华 PyPI（`pypi.tuna.tsinghua.edu.cn`）、PyTorch CPU 下载站和第 5 步的 `nvcr.io` 及其实际下载域名。企业代理需要同时配置 Docker daemon 拉取和构建步骤的代理/CA 信任；仅宿主机 curl 正常不能证明容器构建正常。遇到证书错误应安装可信 CA，保持 TLS、包签名及源码哈希校验，不使用跳过校验参数。

## 5. 在线机：下载其余六个镜像并导出

执行位置：**在线机源码根目录** `/home/llm-bench-build/LLM-Inference-Benchmark`。开始本节前执行：

```bash
cd /home/llm-bench-build/LLM-Inference-Benchmark
test -f scripts/benchmark_stack.py
```


DCGM Exporter 使用 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nvidia/dcgm-exporter:4.8.4`。若 site.json 是从旧版本模板复制的，请同步更新 `images.nvidia_exporter`；导出清单、离线导入标签和现场配置需一致。本次配置修改未执行镜像拉取验证。

此步骤只读取现场 JSON 中的镜像名，不加载现场配置、不检查 GPU、不启动生产容器。按单机 NVIDIA local 模式共导出 8 个镜像。

```bash
python3 - <<'PY'
import json
import subprocess
from pathlib import Path

bundle = Path('generated/offline-bundle')
cfg = json.loads((bundle/'site/site.json').read_text())
keys = ['toolbox', 'aisbench', 'prometheus', 'grafana', 'loki', 'alloy',
        'node-exporter', 'nvidia_exporter']
images = [cfg['images'][key] for key in keys]
metadata = []
for key, image in zip(keys, images):
    if key not in ('toolbox', 'aisbench'):
        subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', image], check=True)
    info = json.loads(subprocess.check_output(
        ['docker', 'image', 'inspect', image], text=True))[0]
    if (info['Os'], info['Architecture']) != ('linux', 'amd64'):
        raise RuntimeError('Wrong architecture: ' + image)
    metadata.append({'image': image, 'id': info['Id'],
                     'digests': info.get('RepoDigests', []), 'platform': 'linux/amd64'})
(bundle/'metadata/images.json').write_text(json.dumps(metadata, indent=2) + '\n')
subprocess.run(['docker', 'image', 'save', '-o',
                str(bundle/'images/images-linux-amd64.tar'), *images], check=True)
print('Exported', len(images), 'images')
PY
```

如果 NVIDIA 镜像下载失败，先解决仓库授权、网络或标签问题再打包；没有 GPU 不会阻止下载。不要遗漏 DCGM Exporter，也不要用 `docker export` 代替 `docker image save`。离线运行现成镜像不需要再携带 Python 基础镜像或额外安装客户端 pip 包。

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
  --entrypoint python llm-benchmark-aisbench:e330dc1-amd64 \
  -c 'from transformers import AutoTokenizer; t=AutoTokenizer.from_pretrained("/tokenizer", local_files_only=True, trust_remote_code=False); print(type(t).__name__, len(t.encode("offline tokenizer check")))'
```

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
cfg.update(project='llm-bench-online-smoke', platform='linux/amd64', offline=True,
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


如果离线机已经具备并验证了 Docker、Python、驱动和 Container Toolkit，可记录版本后跳过安装包收集。否则需要按**离线目标系统**准备完整依赖：

- `runtime/`：Docker Engine/CLI、containerd/runc、Python 及依赖安装包和安装说明。
- `drivers/`：匹配 GPU/目标内核的 NVIDIA 驱动、NVIDIA Container Toolkit 及依赖和安装说明。
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

## 9. 离线机：校验、导入和设备检查

以下命令在**离线 NVIDIA 服务器**执行。先选择新的交付目录 `/home/llm-bench-delivery`，把第 8 节生成的 `llm-bench-offline-amd64.tar.gz` 与 `llm-bench-offline-amd64.tar.gz.sha256` 复制到该目录，再运行下面命令。此处不是在线机仓库的 generated/，也不是 offline-bundle/ 内部。

```text
/home/llm-bench-delivery/
├── llm-bench-offline-amd64.tar.gz
└── llm-bench-offline-amd64.tar.gz.sha256
```

本节所有代码块均在 `/home/llm-bench-delivery` 执行。括号内 `(cd offline-bundle && ...)` 只临时切换子进程目录，执行后终端仍留在交付目录。

```bash
set -euo pipefail
cd /home/llm-bench-delivery
pwd  # /home/llm-bench-delivery
sha256sum -c llm-bench-offline-amd64.tar.gz.sha256
tar -xzf llm-bench-offline-amd64.tar.gz
(cd offline-bundle && sha256sum -c SHA256SUMS)
uname -m
python3 --version
docker info
nvidia-smi
```

若工具缺失，先按交付包 `runtime/`、`drivers/` 中与现场系统匹配的说明安装。NVIDIA Container Toolkit 安装后，如 Docker 尚未配置 NVIDIA runtime，由现场管理员执行：

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

重启 Docker 可能影响现有推理服务，应安排维护窗口；已正确配置的主机不必重复执行。此步骤适用于 systemd 管理的常规 Docker Engine，rootless Docker 需按对应官方部署方式配置。

然后导入镜像、核对 ID/架构并验证设备注入：

```bash
docker image load -i offline-bundle/images/images-linux-amd64.tar
python3 - <<'PY'
import json
import subprocess
from pathlib import Path
for expected in json.loads(Path('offline-bundle/metadata/images.json').read_text()):
    info = json.loads(subprocess.check_output(
        ['docker', 'image', 'inspect', expected['image']], text=True))[0]
    assert info['Id'] == expected['id'], expected['image']
    assert (info['Os'], info['Architecture']) == ('linux', 'amd64'), expected['image']
    print('OK', expected['image'])
PY
TOOLBOX_IMAGE=$(python3 -c 'import json; print(json.load(open("offline-bundle/site/site.json"))["images"]["toolbox"])')
docker run --rm --pull=never --platform linux/amd64 --network none \
  --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \
  --entrypoint nvidia-smi "$TOOLBOX_IMAGE"
```

最后一条显式使用已注册的 `nvidia` runtime，并通过 `NVIDIA_VISIBLE_DEVICES=all` 注入设备，与正式启动器中的本地 DCGM Exporter 参数一致；无需依赖 Docker 的 `--gpus` 设备请求机制。若 runtime 已注册且此命令通过，不必仅因 `--gpus all` 报错而重装 Toolkit 或重启 Docker。该命令通过 Toolkit 向现有 toolbox 容器注入宿主机 GPU 工具，不需要额外下载 CUDA 测试镜像。成功标准：宿主机与容器中均能显示 NVIDIA 设备。此检查不替代后续 DCGM Exporter 实际指标验证。

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

`git archive` 的源码归档不含 .git，解出的仓库不能通过 git pull 更新；若要使用后续提交，请重新制作源码归档和相关镜像，避免用旧交付包覆盖已更新的工程。

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

### 10.3 填写现场配置

后续第 11、12 节均在选定源码根目录执行。编辑 `generated/site/site.json`，逐项完成下表，不保留任何 `REPLACE_`。不要直接使用原始 `configs/stacks/nvidia-single.json` 替代第 3 节生成的现场模板；原始模板路径与这里的默认部署路径不同。

| 字段 | 现场填写方式 |
| --- | --- |
| `offline` / `simulation` / `platform` | 保持 `true` / `false` / `linux/amd64` |
| `gpu.vendor` / `gpu.deployment` | 保持 `nvidia` / `local`；不要用 auto |
| `gpu.nodes[0].name`、`gpus` | 本机标识、实际 GPU 数量；local 模式只配置一个节点 |
| `storage.root` | 默认 `generated/nvidia-site`；足够容量、当前用户可写的独立目录 |
| `storage.logs` | 推理服务实际持续写入的日志目录，如 `/srv/inference-logs` |
| `log_files` | 相对日志目录的真实活跃文件，如 `server.log`；不支持通配符 |
| `storage.tokenizer` | 使用第 10.1 节布局时填 `assets/tokenizer`；复用已有目录时填写实际路径，不能只创建空目录 |
| `inference.base_url` | 实际服务根地址，如 `http://10.20.0.10:30000/`，不含 `/v1/completions` |
| `inference.api_key_env` | 服务需 Bearer Token 时设置变量名（如 `INFERENCE_API_KEY`），执行第 11 节前在同一终端导出纯 Token；详见 [鉴权说明](inference-auth.md) |
| `inference.model` / `api` | `/v1/models` 返回的模型 ID；接口选 `completions` 或 `chat` |
| `topology.metrics_endpoints[0].target` | 实际提供 `/metrics` 的 `主机:端口`，可与推理 API 不同 |
| `topology.tp` / `pp` | 与真实部署一致的标签；不会替你启动推理进程 |
| `node_exporter_target` | 此 NVIDIA 主机可从容器访问的 IP:9100；host 模式不要写容器 localhost |
| `benchmark` | 先保留 8 请求/并发 2 的小负载；`generation_kwargs` 必须是服务支持的参数 |

相对路径按**仓库根目录**解析，而非 site.json 所在目录。不要只创建空日志文件冒充真实推理日志；Alloy 需要读日志，logrotate 会写入/轮转指定日志，避免与现场已有轮转任务同时管理同一文件。准备好数据目录的写权限、tokenizer 的读权限和容器访问权限。

推理服务需监听容器可达地址。离线仍需要 Docker bridge 到推理服务、Exporter 和内网 DNS 的通信，不要给整个栈设置 `--network none`。本机 host 模式 node_exporter 使用 9100 端口，需避免与已有服务冲突。

本工程当前应用指标面板主要面向 SGLang；其他引擎即使支持 OpenAI API，也需要核对其 `/metrics` 和面板查询，不能仅凭压测成功就认为所有图表适用。

## 11. 离线机：预检查、小规模压测与验收

执行位置：**离线机运行用源码根目录**。新开终端须先执行下面命令；若按第 10.2 节复用已有源码，cd 到你实际的源码根目录。

```bash
cd /home/llm-bench-runtime/LLM-Inference-Benchmark
test -f scripts/benchmark_stack.py
test -f generated/site/site.json
```


```bash
python3 scripts/benchmark_stack.py plan --config generated/site/site.json
python3 scripts/benchmark_stack.py check-images --config generated/site/site.json
set -o pipefail
python3 scripts/benchmark_stack.py up --config generated/site/site.json \
  2>&1 | tee generated/site/first-up.log
python3 scripts/benchmark_stack.py status --config generated/site/site.json
```

预期行为：`check-images` 通过 NVIDIA 单机的 8 个镜像检查；`up` 启动本机 DCGM Exporter、node_exporter、Loki、logrotate、Alloy、Prometheus、Grafana，等待采集目标与推理 API 就绪，再执行 AISBench。真实模式不会启动 mock fixture，也不会部署推理服务。offline 模式缺镜像时直接失败，不自动 build/pull。

按以下顺序验收：

1. `up` 退出码为 0；所有常驻容器运行，AISBench 正常退出。
2. Prometheus Targets 全部 UP；默认在目标机访问 `http://127.0.0.1:39090/targets`，检查 `nvidia-dcgm-exporter`、主机与推理指标目标。
3. 查询 `DCGM_FI_DEV_GPU_UTIL` 等真实指标，核对卡数/UUID/标签；空闲 GPU 利用率为 0 不代表采集失败。
4. Grafana 默认 `http://127.0.0.1:33000`，检查 NVIDIA 与推理面板、Prometheus/Loki 数据源。默认 admin/admin 首次登录后应修改；默认只绑定回环，远程查看使用现场允许的 SSH 隧道等方式。
5. 在 Grafana Explore 的 Loki 数据源查询 `{deployment="llm-bench-nvidia"}`，确认来自真实服务日志；如果改过 project，同步修改查询标签。
6. 查本次新增的 `generated/nvidia-site/data/results/<时间戳>/performances/*/synthetic.json`。首次小负载应为正式请求 8、成功 8、失败 0，预热不计入正式请求数；检查当前运行，不能拿旧成功报告作证据。

`verify_simulation.py` **只允许 simulation 栈**，不要用它验收真实 NVIDIA 配置。真实吞吐、TTFT、TPOT 与 SLO 指标解释参见 [AISBench 使用](aisbench.md)。

## 12. 调整负载、再次压测和停机

执行位置：**离线机运行用源码根目录**。新开终端须先执行下面命令；若按第 10.2 节复用已有源码，cd 到你实际的源码根目录。

```bash
cd /home/llm-bench-runtime/LLM-Inference-Benchmark
test -f scripts/benchmark_stack.py
test -f generated/site/site.json
```


第一次 8 请求是连通性验收，不足以得出容量结论。待任务结束后，先用原配置停止栈，再修改并发、输入输出长度、请求数和压力模式，然后重新启动：

```bash
# 在修改配置前执行；确认没有进行中的 benchmark/suite/search。
python3 scripts/benchmark_stack.py down --config generated/site/site.json
# 编辑 generated/site/site.json 的 benchmark，再执行：
python3 scripts/benchmark_stack.py up --config generated/site/site.json
```

`down` 只移除项目拥有的容器和网络，保留数据/日志/报告，不停止外部推理服务。已有状态时不能重复 `up`；不要删除 state.json 绕过检查，也不要在测试进程运行时改配置。

需要复用观测栈运行多组用例时，从清理后的状态选择以下流程：

```bash
python3 scripts/benchmark_stack.py up --config generated/site/site.json --no-benchmark
# 按顺序压测手册准备 generated/site/suite.json 后执行：
python3 scripts/run_aisbench_suite.py \
  --config generated/site/site.json --suite generated/site/suite.json
```

`--no-benchmark` 不会执行单次压测；不能随后再次 up 追加客户端。使用上述 suite 入口，或 down 后执行完整 up。用例格式见[顺序压测](aisbench-sequential.md)，业务容量搜索见[SLO 搜索](aisbench-slo-capacity.md)。

当前版本也提供 `llm-bench` CLI，可以管理上述脚本启动的栈。下列命令在观测栈已启动、没有正在执行的压测时使用；`bench run` 会发起新的请求，其结果保存在数据目录的 `data/results/suites/` 下：

```bash
export PATH="$PWD/bin:$PATH"
llm-bench status --config generated/site/site.json
llm-bench bench run --config generated/site/site.json --concurrency 2 --requests 8
# 现场出错时，在 down 之前采集诊断包，按输出路径取回：
llm-bench logs collect --config generated/site/site.json
```

CLI 的配置定位与结果路径见 [CLI 手册](cli.md)。诊断包可能包含现场地址、请求内容和日志，回传前按现场规定检查；采集范围及选项见[离线日志与故障回传](offline-diagnostics.md)。混合数据集用例还需交付 JSONL/ShareGPT 等本地文件并保留 `containers/aisbench/llm_bench_jsonl.py`，完整源码归档已包含该加载器。

验收结束后备份实际使用的 site/suite/search 配置、对应结果目录、版本记录及日志；记录目标 OS、GPU、驱动、推理服务/模型/tokenizer 版本与离线网络条件。

## 13. 常见故障定位

| 现象 | 优先检查/处理 |
| --- | --- |
| 在线无 GPU，提示无法识别厂商 | 不运行生产 up；site 显式 nvidia；构建采用第 4 步直接方式 |
| 在线 build 报占位符/tokenizer 错误 | 误用了现场配置入口；直接构建两个 Dockerfile，不需要现场路径 |
| 源码 SHA-256 不一致 | 停止构建，核对提交/下载内容；不修改期望哈希以强行通过 |
| 在线拉取或 pip/apt 失败 | 查看具体失败域名、代理、CA、仓库授权/标签；解决后重跑失败步骤 |
| 离线 Missing images / Wrong architecture | 按清单补齐 amd64 镜像，在在线侧重新 save；不能把 arm64 改标签冒充 |
| `could not select device driver` / `nvidia-smi` 失败 | 检查宿主机驱动、Toolkit 配置、Docker runtime；先通过第 9 步设备注入检查 |
| DCGM Exporter 启动失败 | `docker logs llm-bench-nvidia-gpu-exporter`；核对镜像/驱动兼容性与设备权限 |
| 采集或 API 就绪超时 | 确认容器内可达 IP、监听地址、端口、防火墙、DNS/代理；主机 curl 成功不代表容器成功 |
| `model-tokenizer.tar.gz: Cannot open: No such file or directory` | 交付包缺少 tokenizer 归档；第 6.1 节下载目录不等于归档，须完成打包并补传；源码已解包时只补齐 tokenizer，不重复创建安装目录 |
| tokenizer 离线加载失败 | 文件不全、外部软链接、自定义依赖或模型版本错误；回到第 6 步验证实际归档 |
| 挂载目录 Permission denied | 检查 UID/GID、目录权限、rootless 映射及 SELinux 策略；按服务最小权限处理，不递归放宽整个生产目录 |
| AISBench 非零退出或报告含失败请求 | `docker logs llm-bench-nvidia-aisbench`；核对模型 ID、API 类型、请求参数、上下文长度、服务错误和负载 |
| Grafana 有指标但 Loki 无日志 | 检查服务是否实际写文件、路径/文件名、Alloy 读权限及已有轮转机制 |
| 已有 state / suite.lock | 检查任务状态；完成后用原配置 down，不盲删锁或状态文件 |

可用 `docker logs --tail 200 <容器名>` 查看单组件错误；改过 project 时容器名前缀也相应变化。完整交付要求与离线模式行为见[离线部署总说明](offline-deployment.md)。

两个项目 Dockerfile 的 Debian 主仓库及安全更新仓库均使用清华 TUNA HTTPS 镜像：`https://mirrors.tuna.tsinghua.edu.cn/debian`、`https://mirrors.tuna.tsinghua.edu.cn/debian-security`。保留 bookworm、bookworm-updates、bookworm-security 及原有签名验证设置，兼容传统 sources.list 和 deb822 .sources 格式。构建机需能访问该域名并信任 HTTPS 证书；apt 索引下载失败时立即终止构建。Python 通用依赖统一使用清华 PyPI 索引 `https://pypi.tuna.tsinghua.edu.cn/simple`，包括 pip/setuptools/wheel、AISBench 及其构建依赖。PyTorch 固定 `torch==2.6.0+cpu`，通过 `--find-links https://download.pytorch.org/whl/cpu/torch/` 获取官方 CPU wheel；其余依赖仍由清华 PyPI 解析。构建时需同时允许清华 PyPI 及 PyTorch 官方下载域名。
