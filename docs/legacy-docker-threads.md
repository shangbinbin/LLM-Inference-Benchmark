# 旧 Docker 环境的 Python 线程兼容设置

部分旧 Docker/containerd/runc/libseccomp 组合的默认 seccomp 策略与新镜像内的 glibc 不兼容，可能导致普通 Python 线程创建失败。现场 Docker 20.10.7 出现了 `RuntimeError: can't start new thread`，同一镜像的最小线程测试仅在加 `--security-opt seccomp=unconfined` 后通过，说明默认 seccomp 策略参与导致故障。具体是否为 clone3 需系统调用跟踪进一步确认，不能仅凭报错断定。

减少 OpenBLAS 线程数可以避免计算库大规模线程初始化，却不能解决 fixture 每次处理请求仍需创建线程的问题。升级到受支持的容器运行时及配套 libseccomp 是长期方案；生产服务器升级应安排维护窗口。

## 配置范围

工程支持顶层 JSON 布尔值 `legacy_seccomp_unconfined`，省略或设为 false 时保持运行时默认安全策略。只有明确设为 true 才添加 `--security-opt seccomp=unconfined`：

```json
"legacy_seccomp_unconfined": true
```

开启后，取消以下容器的 seccomp 系统调用过滤：

- simulation 模式的 fixture 模拟推理服务。
- 所有模式的 AISBench 客户端：单次压测、顺序用例和 SLO 搜索，包括业务 JSONL/ShareGPT 用例。

Prometheus、Grafana、Loki、Alloy、node_exporter、DCGM/HCU Exporter、logrotate 和 toolbox 就绪探测不受该开关影响。它不添加 privileged、不改变 Docker daemon 配置、不修改宿主机已有推理服务，也不会自动重试或自动关闭安全策略。offline 模式仍禁止拉取和构建。

该设置降低指定容器的隔离程度，仅用于已经确认默认 seccomp 导致线程失败、且暂时不能升级运行时的受信任压测工作负载。不应默认应用于所有环境。手工 docker run 不读取 stack JSON，需要单独指定参数。

## 离线机现有模拟栈的启用步骤

在离线机源码根目录执行。先更新当前源码；只改变创建容器的参数，无需为此重新构建或重新导入镜像。已有容器不会因修改 JSON 自动生效，需先使用原配置停止项目栈，再改配置重新创建。

```bash
cd /home/llm-bench-runtime/LLM-Inference-Benchmark
# 如实际源码位置不同，请修改上述路径。
CONFIG=generated/offline-smoke.json

# 确认没有正在运行的压测，再停止本项目栈；不停止外部推理服务。
python3 scripts/benchmark_stack.py down --config "$CONFIG"
```

上一步成功后修改配置：

```bash
python3 - "$CONFIG" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
cfg = json.loads(path.read_text())
cfg['legacy_seccomp_unconfined'] = True
path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
PY

python3 scripts/benchmark_stack.py plan --config "$CONFIG"
python3 scripts/benchmark_stack.py check-images --config "$CONFIG"
python3 scripts/benchmark_stack.py up --config "$CONFIG"
python3 scripts/verify_simulation.py --config "$CONFIG"
```

逐条执行，前一条成功后再继续。若新开终端，需重新设置 CONFIG。若使用自己的模拟配置文件名，请修改 CONFIG。真实推理服务使用 `generated/site/site.json` 中的相同顶层字段，且保持 simulation=false；真实模式不运行 verify_simulation.py。Bearer Token 环境变量仍需正常提供。

修改配置会改变生成的配置目录/manifest；suite/search 继续使用与重新启动后的栈完全相同的配置。兼容参数也会自动传给它们创建的 AISBench 容器，不要在套件文件中单独修改。

## 升级运行时后恢复默认策略

使用当前配置正常 down，把该字段删除或改为 false，再重新 up。先确认不带兼容参数的最小 Python 线程测试可以成功。不要仅使用 docker restart 代替重新创建，restart 会保留原容器的安全选项。
