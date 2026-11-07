# 基于 SLO 自动搜索最大并发

离线运行时，在启动栈的 JSON 中设置 `"offline": true`，后续使用同一配置。客户端和就绪探测自动禁止拉取，缺少 AISBench/toolbox 会在发压前列出清单；完整准备步骤见 [离线部署手册](offline-deployment.md)。

`scripts/slo_search.py` 在每轮 AISBench 完成后读取真实报告，检查 SLO，自动选择下一档并发。它复用已启动的观测栈与同一个被测服务，支持搜索范围、重复次数、轮数/时间预算和单次压测超时。AISBench 仍是唯一发压客户端；搜索控制由本工程实现，不是上游 AISBench 新增的 CLI 参数。

## 快速开始：Mac 实测流程

Docker Desktop 已运行，在仓库根目录执行：

```bash
# 首次或修改模拟端点后重建 toolbox；AISBench 镜像沿用已有版本
docker build --platform linux/arm64 -t llm-benchmark-toolbox:slo-arm64 \
  -f containers/toolbox/Dockerfile .

# 独立于原 mac-simulation 栈的名称、数据目录与端口
python3 scripts/benchmark_stack.py up \
  --config configs/stacks/mac-slo-search.json --no-benchmark

python3 scripts/slo_search.py \
  --config configs/stacks/mac-slo-search.json \
  --search configs/search/mac-slo.json
```

`up --no-benchmark` 不构建 AISBench 镜像。首次没有该镜像时，先执行 `python3 scripts/benchmark_stack.py build --config configs/stacks/mac-slo-search.json`。搜索器会检查镜像存在及架构，不在搜索预算中下载/构建镜像。

模拟服务在同时处理请求数超过 2 时，增加 400 ms 响应延迟。搜索范围 1–6，每个候选实际运行两次 AISBench，TTFT P99 阈值为 200 ms。预期最大达标并发为 2；结果必须来自实际压测报告，模拟服务不会给搜索器提供答案。Mac 调度若异常拥塞，也可能触发停止/不达标，不能硬编码期望结果或跳过校验。

本次完整搜索及停止条件实测见 [Mac SLO 验收记录](reports/mac-slo-search-2026-11-07.md)。

查看 Grafana：`http://localhost:33002`。完成后可执行：

```bash
python3 scripts/benchmark_stack.py down --config configs/stacks/mac-slo-search.json
```

保留 data/results/slo-search 的原始结果。模拟服务只验证控制流程，不代表真实 GPU 的容量或曲线。

## Linux x86 生产运行

1. 从 `configs/stacks/nvidia-single.json` 复制完整 site.json，填写服务 URL、model、真实 tokenizer、指标/日志地址与存储目录。海光按既有 GPU 适配配置切换。
2. 保持 `platform: linux/amd64` 与 `*-amd64` 镜像标签。生产镜像应在 x86 Linux 构建并完成常规单次 AISBench 验证。
3. benchmark 使用 `mode: perf`、`request_rate: 0`、`retry: 1`；移除 concurrency_levels/requests_per_level。输入输出长度、样本数、tokenizer、生成参数、预热数在整个搜索中保持不变，只有 concurrency 被搜索器覆盖。
4. request_count 至少达到搜索 max_concurrency 和 min_requests；output_length 至少为 2。准备固定输出长度负载，并确认后端支持相应生成参数。
5. 复制并修改 `configs/search/slo-capacity.json` 中的业务阈值及预算，然后执行：

```bash
python3 scripts/benchmark_stack.py up --config /absolute/path/site.json --no-benchmark
python3 scripts/slo_search.py --config /absolute/path/site.json --search /absolute/path/search.json
```

如果栈已以相同配置启动且为 ready/complete，直接执行搜索即可。不要并行运行其他客户端；搜索与本工程 suite 共用 `storage.root/suite.lock`，down 在锁存在时会拒绝清理组件。直接从外部启动的压测客户端无法由此锁隔离，需要实验环境保证独占测试窗口。

## 搜索策略文件

```json
{
  "min_concurrency": 1,
  "max_concurrency": 64,
  "step": 1,
  "repeats": 3,
  "strategy": "verified_max",
  "statistic": "P99",
  "thresholds_ms": {"TTFT": 1000, "TPOT": 50, "E2EL": 30000},
  "min_requests": 1000,
  "max_rounds": 64,
  "max_duration_seconds": 7200,
  "trial_timeout_seconds": 300,
  "cooldown_seconds": 15
}
```

以上阈值为示例，不能代替业务 SLO。

| 字段 | 含义 |
| --- | --- |
| min_concurrency/max_concurrency | 含两端的整数搜索范围 |
| step | 候选间隔；必须使 `(max-min) % step == 0`，最大值只在此网格内成立 |
| repeats | 每个候选运行的完整重复次数，**全部通过**才判为达标 |
| strategy | verified_max（默认）或 binary |
| statistic | 所有阈值共用的统计口径：P99、P90、P75、Median、Average、Max；默认 P99 |
| thresholds_ms | TTFT/TPOT/E2EL/ITL 中任意非空子集；数值均为毫秒，各项同时满足才通过 |
| min_requests | 每次正式请求的最低数量，不含预热 |
| max_rounds | 最多评估多少个不同候选；一个 round 包含 repeats 个 trial |
| max_duration_seconds | 搜索总墙钟预算，包含初始化、预热、压测、汇总、检查和冷却 |
| trial_timeout_seconds | 单次 AISBench 容器执行上限，包含客户端初始化/预热/汇总；剩余总预算更小时以剩余预算为准 |
| cooldown_seconds | 相邻 trial 之间的等待时间，包含同一候选的重复测试之间 |

不支持的字段会报错，避免拼错阈值却继续运行。每个候选都会完成配置的重复次数，即使前一次时延已超标，也保留后续重复用于观察抖动；程序失败、报告无效或预算耗尽则立即停止，不继续发起负载。

## 算法与“最大”的含义

先测试下界；下界通过时测试上界。若上界失败，对通过/失败区间进行整数二分，每次依据最新 AISBench 报告缩小区间。已完整评估过的候选会缓存结果，不重复创建相同 round。

两种策略的差异：

- **verified_max**：二分后，从搜索上界向下检查所有高于当前最佳值、但尚未测过的候选，遇到达标点后结束。即使最小并发失败，也会检查更高候选，处理非单调曲线。只有所有更高网格点均被实测排除，才设置 maximum_verified=true。
- **binary**：二分边界确定后直接结束，假定“更高并发不会从不达标恢复为达标”。结果明确标记 conditional_on_monotonicity=true、maximum_verified=false。适合已经验证近似单调、希望减少测试轮数的场景。

例如范围 1–6、阈值在并发 2 和 3 之间时，典型 verified_max 轨迹为 `1 → 6 → 3 → 2 → 5 → 4`。最后两轮用于排除二分未测过的高档；binary 只运行前四轮。

上界通过则无需测试更低档即可确认它是配置范围内的最大值，但不能证明范围之外都失败。verified_max 最坏可能扫描整个网格，预算要覆盖 `(max-min)/step+1` 个候选及其 repeats。默认生产样例最坏 64 × 3 次 AISBench，正式请求最多 192000 条，预热另计；若总时间预算先耗尽，将返回未完成状态。

重复通过只代表本轮测量证据，不保证未来所有流量和时间段都满足 SLO。不要平均多轮 P99；每轮分别判断。多个指标的 P99 都通过，也不等于相同比例的请求联合满足全部条件。有关业务口径、Goodput、QPS 搜索及长期复测，见 [SLO 容量判读](aisbench-slo-capacity.md)。

## 每次报告如何判断

每个 trial 使用独立目录和 AISBench 容器，仅读取本次结果。检查：

1. 所需观测容器仍运行且属于本项目，Prometheus targets 全部 UP。
2. AISBench 退出成功，存在唯一 synthetic.json 及对应 CSV。
3. 正式请求总数与固定 workload 相符，达到 min_requests，零失败，成功数等于总数。
4. 实测 Max Concurrency 达到本轮目标；客户端未施加目标负载时不会推荐该档。
5. OutputTokens 的 Min/Max 等于固定输出目标，防止提前 EOS/stop 造成虚假的低时延。
6. 所选指标 Stage=total、样本 N 完整，单位为 ms，值有限且非负；按 statistic 读取，不用平均值替代 P99。
7. 任一时延超过阈值为 SLO_FAIL，等于阈值算通过。数据/请求失败为 INVALID，立即停止，不能把它当作有效的 SLO 失败边界。

当前实现严格要求零请求失败、固定输出长度、单一 workload、perf 模式和 request_rate=0。QPS 搜索、容忍失败预算、压力模式、自定义分布的变长输出和服务参数寻优不在该入口中。实际输入 token 数仍须按模型 tokenizer 核对。

## 停止、退出码与清理

| stop_reason | 含义 |
| --- | --- |
| boundary_found | 搜索策略完成，并找到达标候选 |
| upper_bound_passed | 配置上界通过，推荐仅限本次范围 |
| no_feasible_concurrency | 策略完成但没有达标候选；binary 的结论仍依赖单调假设 |
| round_budget/time_budget | 达到轮数/总时间上限；没有完成最大值搜索 |
| trial_timeout | 单次客户端执行超时，停止本次搜索 |
| invalid_trial | 请求失败、报告缺失/无效、采集未就绪等，停止搜索 |
| interrupted | Ctrl-C/SIGTERM 中断 |
| execution_error | 初始化、架构、状态或清理阶段异常；查 error 字段 |

未完成时保留 best_observed_concurrency，但 recommended_concurrency=null、maximum_verified=false，不能把“已见最好值”写成“搜索最大值”。重复次数尚未完成的候选也不会算入 best_observed。

正常完成且有推荐返回 0；没有可行点、预算/测量阶段停止返回 1；初始化/输入/执行环境异常返回 2。初始化阶段中断可返回 130。以 JSON 的 status/stop_reason 为详细判据。

每次客户端结束、超时或可捕获中断后，按项目标签和 trial 独有 token 清理该客户端/探测容器，保留观测栈及数据。总预算用于停止发压，清理动作有独立的有界超时，可能在预算后继续数十秒。若容器引擎不可用、进程被 SIGKILL 或断电，无法保证即时清理：会尽量保留锁，先检查 `*-search-client` / `*-search-probe` 的实际状态，再处理残留；不要直接删锁后重启搜索。

中断后的搜索不自动续跑。重新执行会生成新目录，避免混合服务状态已变化的旧测量。服务在客户端退出后是否取消已收到的推理请求取决于后端；重试前应确认队列排空。

## 结果文件

启动时打印 `Search report: .../search-results.json`。所有输出在：

```text
storage.root/data/results/slo-search/<UTC时间戳>/
├── stack.json                    # 固定服务/workload/镜像/架构快照
├── policy.json                   # 生效搜索策略（含默认值）
├── search-results.json           # 原子更新的搜索进度、停止原因与最终结论
└── round-001-c1-r1/              # 每个候选的每次重复均独立保存
    ├── stack.json
    ├── aisbench.py
    ├── client.log
    ├── trial.json
    └── results/<AISBench时间戳>/performances/aisbench-target/...
```

search-results.json 记录 rounds[].phase（下界、上界、二分、高档验证）、并发、各次重复的状态/阈值违例/实测时延、起止时间、全局输出吞吐、报告相对路径及停止原因。报告路径相对于该 trial 的目录。用这些起止时间与 Grafana/Loki 对齐分析。

`maximum_verified=true` 表示**配置网格内、更高候选已被本次测量排除**；不会把 Mac 结果解释为显卡性能，也不会把该值直接等同于生产限流配置。选定档位后应做长期压力、邻近档位和真实业务分布复测。
