# 离线环境收集 llm-bench 诊断包

推理服务异常、监控断点或压测结果异常时，在运行 llm-bench 的主机上执行。优先在 `down` 前采集，以保留运行时中的组件日志。只需本地 Python、配置好的 Docker 或 nerdctl，以及站点日志和数据目录的读取权限。

```bash
llm-bench logs collect --config /srv/bench/site.json
```

终端会打印 `Diagnostic bundle` 和文件绝对路径。默认文件为 `storage.root/diagnostics/llm-bench-<UTC时间戳>.tar.gz`。采集期间可以继续压测。命令只读取已配置的运行时、本地文件和本机发布的 Prometheus 接口，使用主机标准库打包，不需要拉镜像或安装依赖。

## 定位故障和回传

1. 正式 `up` 的镜像预检、组件启动或就绪探测失败时，CLI 会在本地自动留包。`images check` 的运行时检查和 `bench run/suite/slo` 的异常或非零返回也会触发留包。SLO 搜索返回“无可行并发”也可能产生包，具体结论看搜索报告。自动采集失败时，CLI 仍报告原命令的错误。
2. 先看包内 `manifest.json` 的 `error`、`created_at` 和 `collection_notes`。手动采集时 `error` 为空，之前的故障可按时间查 CLI 事件和用例摘要。用 `cli/events.jsonl` 对齐启动顺序与就绪探测，用 `containers/<组件>/state.json` 的 `ExitCode`、`OOMKilled`、`Error` 及同目录的日志区分崩溃、配置和依赖故障。
3. 如果故障只影响某个用例，打开 `results/` 下的 suite 或 SLO 汇总，按用例名、并发和轮次找到 `client.log`、`trial.json` 或失败请求摘要，再对照 `inference/` 下同时间段的服务日志。
4. 回传前可在本地查看文件清单和清理过的内容。原始服务或客户端日志的自由文本可能包含业务内容、内网地址或额外凭据。检查后将压缩包作为附件提交，并说明故障发生时间（含时区）、执行的命令和受影响的用例。工具只在本地生成文件。

```bash
tar -tzf /path/to/bundle.tar.gz
tar -xOzf /path/to/bundle.tar.gz manifest.json
```

## 手动选取日志

| 参数 | 用法 |
| --- | --- |
| `--config FILE` / `--data-dir DIR` | 二选一。当前目录只有一个活动栈时可以省略。新版本启动过的栈在 `down` 后可用 `--data-dir` 找回上次启动配置。旧栈优先使用完整的原始 `site.json`。 |
| `--output FILE` | 指定新压缩包的路径，保留已有同名文件。默认文件在该栈的数据目录内。 |
| `--tail N` | 每个容器、客户端及推理日志的最近 N 行，默认 500，可选 1–10000。 |
| `--run ID` | 只选指定运行的压测产物。ID 相对 `storage.root/data/results`，如 `suites/<UTC时间戳>`、`slo-search/<UTC时间戳>` 或 AISBench 单次运行目录。组件和推理日志仍采集当前尾部。 |
| `--extra-log FILE` | 可以重复指定，补入宿主机上可读的外部 GPU Exporter、远端 worker 或启动终端日志。文件按普通文本读取。 |

```bash
# RUN 替换为这次搜索打印的结果目录名
RUN=20261108T043539596936Z
llm-bench logs collect --data-dir /srv/llm-benchmark \
  --run "slo-search/$RUN" --tail 2000 \
  --extra-log /mnt/shared/rank1/exporter.log \
  --output /srv/transfer/incident.tar.gz
```

如果参数、site JSON、tokenizer 或启动清单在运行时上下文建立之前就校验失败，可先保留终端输出。在 Bash 中使用以下命令保留原退出状态，然后用可读取的完整站点配置手动打包。

```bash
set -o pipefail
llm-bench up --config /srv/bench/site.json 2>&1 | tee /tmp/llm-bench-start.log
llm-bench logs collect --config /srv/bench/site.json --extra-log /tmp/llm-bench-start.log
```

## 采集范围

| 包内位置 | 可用于定位的问题 |
| --- | --- |
| `manifest.json`、`runtime/version.txt`、`stack/state.json` | 采集时间、触发错误、主机及容器架构、镜像、GPU 厂商、服务端点、部署拓扑、运行时版本和栈状态。 |
| `cli/` | 启动及压测操作的起止、组件阶段、短时就绪探测的输出和退出码。事件日志约 2 MiB 轮转，保留一份前卷。 |
| `containers/` | 按项目标签发现并核对归属的运行中或已退出容器。包括 Prometheus、Grafana、Loki、Alloy、logrotate、node_exporter、本机 GPU/DCU Exporter，以及采集时仍存在的 AISBench 客户端。每个容器保留带时间戳的 stdout/stderr 和精简状态。 |
| `monitoring/targets.json` | 项目 Prometheus 映射到 127.0.0.1 时，采集实时 target、健康状态、上次抓取时间和错误。未发布端口时可看 CLI 先前的就绪记录。 |
| `inference/`、`external/` | 按 `storage.logs` 和 `log_files` 读取当前活动日志，可包含 prefill/decode 等子目录。外部节点上未共享的日志需先复制到采集机，再用 `--extra-log` 指定。 |
| `results/` | 默认分别选最新一次普通 AISBench、顺序 suite 和 SLO 搜索。收集已有的客户端日志、原生多并发档 runner 的 logs/infer/*.out、运行摘要、策略、trial 记录及性能汇总。逐请求明细尾部的失败记录仅提取 ID、轮次及文本错误，写入 `*_failures.json`。 |

归档按文件名白名单收集压测产物。业务数据集、逐请求原始输入与回答文件、SQLite/时序数据库、模型、tokenizer、完整容器环境变量和挂载清单不在默认采集项内。常见密码、令牌、Authorization 和 URL 登录信息会被遮盖。日志默认只读活动文件；轮转前的历史日志可解压为文本后用 `--extra-log` 补入。

单个本地文件读取上限为末尾 256 KiB，普通日志再按 `--tail` 裁剪；超限的结构化 JSON 摘要会跳过并在清单中注明。每类运行最多收集 240 个候选结果文件，优先汇总及近期文件。归档内容预算为 24 MiB（另有清单）。服务停止、引擎不可用或文件缺失时，仍尽量生成部分包，并在 `collection_notes` 记录缺项。`down` 会删除容器，之后可回传已留存的自动包，或补采仍在磁盘上的日志及报告。诊断包保留在本地，按现场保留期限清理。
