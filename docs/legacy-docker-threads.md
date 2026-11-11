# 旧 Docker 环境的 Python 线程兼容设置

部分旧 Docker/containerd/runc/libseccomp 组合的默认 seccomp 策略与新镜像内的 glibc 不兼容，可能导致Python 或 Go/cgo 线程创建失败。现场 Docker 20.10.7 出现了 `RuntimeError: can't start new thread`，同一镜像的最小线程测试仅在加 `--security-opt seccomp=unconfined` 后通过，说明默认 seccomp 策略参与导致故障。具体是否为 clone3 需系统调用跟踪进一步确认，不能仅凭报错断定。

减少 OpenBLAS 线程数可以避免计算库大规模线程初始化，却不能解决 fixture 每次处理请求仍需创建线程的问题。升级到受支持的容器运行时及配套 libseccomp 是长期方案；生产服务器升级应安排维护窗口。

## 配置范围

工程支持顶层 JSON 布尔值 `legacy_seccomp_unconfined`，未填写时按 offline 取默认值：offline=true 时自动补为 true，在线模式默认 false；显式 false 始终保持运行时默认安全策略。启用时添加 `--security-opt seccomp=unconfined`：

```json
"legacy_seccomp_unconfined": true
```

开启后，统一在本工程启动器发出的所有 `run` / `create` 命令中添加该参数，覆盖：

- fixture 模拟推理服务和单次、顺序、SLO 搜索的 AISBench 客户端。
- Alloy、Prometheus、Grafana、Loki、node_exporter、DCGM/HCU Exporter、logrotate 等常驻服务。
- toolbox 就绪探测和 Prometheus targets 检查等临时容器。

只影响使用当前 stack 配置创建的项目容器，不改变 Docker daemon 配置、宿主机已有推理服务或其他工程的容器；不额外添加 privileged。`exec`、`start`、`restart` 不接受新的安全参数，已有容器须删除后重新创建才生效。offline 模式仍禁止拉取和构建。`plan` 输出中的 `container_security_options` 显示统一追加的参数。

该设置降低指定容器的隔离程度，仅用于已经确认默认 seccomp 导致线程失败、且暂时不能升级运行时的受信任项目工作负载（包含监控和临时探测）。本工程按现场需求对离线配置默认启用，因此新生成的离线配置和未填写该字段的已有离线配置都会在新建容器时取消过滤；在线配置仍默认关闭。若现场不需要此兼容性，应显式设 false。手工 docker run 不读取 stack JSON，需要单独指定参数。

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

使用当前配置正常 down，把该字段显式改为 false（离线配置删除字段会再次默认开启），再重新 up。先确认不带兼容参数的最小 Python 线程测试可以成功。不要仅使用 docker restart 代替重新创建，restart 会保留原容器的安全选项。

## Alloy 启动崩溃与后续 DNS 错误

若 Alloy 状态是 exited，先看 `docker logs <project>-alloy 2>&1 | sed -n '1,60p'`。Go/cgo 的 pthread_create 权限错误可能使它在初始化阶段退出，随后探测 `http://alloy:12345/-/ready` 报无法解析名称；此时先修复 Alloy 启动，给探测容器关闭 seccomp 不会使已退出的 Alloy 恢复。

当前开关覆盖本工程创建的所有容器，包括 Alloy。已有旧版配置无需换字段，保持 true，更新源码后先 down 再 up，重新创建容器。可用 `docker inspect <project>-alloy --format '{{json .HostConfig.SecurityOpt}}'` 确认含 `seccomp=unconfined`，再检查日志和就绪状态。本次仅改启动器参数，不要求重建 Alloy 或 toolbox 镜像，其他项目和宿主机的安全策略不变。
