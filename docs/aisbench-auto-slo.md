# 基于 SLO 自动搜索最大并发

命令入口与安装步骤见 [CLI 快速上手](cli.md#快速上手)。

离线运行时，在启动栈的 JSON 中设置 `"offline": true`，后续使用同一配置。客户端和就绪探测自动禁止拉取，缺少 AISBench/toolbox 会在发压前列出清单；完整准备步骤见 [离线部署手册](offline-deployment.md)。

`llm-bench bench slo` 在每轮 AISBench 完成后读取真实报告，检查 SLO，自动选择下一档并发。它复用已启动的观测栈与同一个被测服务，支持搜索范围、重复次数、轮数/时间预算和单次压测超时。AISBench 仍是唯一发压客户端；搜索控制由本工程实现，不是上游 AISBench 新增的 CLI 参数。

## 快速开始：Mac 实测流程

Docker Desktop 已运行，在仓库根目录执行：

```bash
# 首次或修改模拟端点后重建 toolbox；AISBench 镜像沿用已有版本
docker build --network=host --platform linux/arm64 -t llm-benchmark-toolbox:slo-arm64 \
  -f containers/toolbox/Dockerfile .

# 独立于原 mac-simulation 栈的名称、数据目录与端口
llm-bench up \
  --config configs/stacks/mac-slo-search.json --no-benchmark

llm-bench bench slo \
  --config configs/stacks/mac-slo-search.json \
  --policy configs/search/mac-slo.json
```

`up --no-benchmark` 不构建 AISBench 镜像。首次没有该镜像时，先执行 `llm-bench images build --config configs/stacks/mac-slo-search.json`。搜索器会检查镜像存在及架构，不在搜索预算中下载/构建镜像。

模拟服务在同时处理请求数超过 2 时，增加 400 ms 响应延迟。搜索范围 1–6，每个候选实际运行两次 AISBench，TTFT P99 阈值为 200 ms。预期最大达标并发为 2；结果必须来自实际压测报告，模拟服务不会给搜索器提供答案。Mac 调度若异常拥塞，也可能触发停止/不达标，不能硬编码期望结果或跳过校验。

本次完整搜索及停止条件实测见 [Mac SLO 验收记录](reports/mac-slo-search-2026-11-07.md)。

查看 Grafana：`http://localhost:33002`。完成后可执行：

```bash
llm-bench down --config configs/stacks/mac-slo-search.json
```

保留 data/results/slo-search 的原始结果。模拟服务只验证控制流程，不代表真实 GPU 的容量或曲线。

## Linux x86 生产运行

1. 从 `configs/stacks/nvidia-single.json` 复制完整 site.json，填写服务 URL、model、真实 tokenizer、指标/日志地址与存储目录。海光按既有 GPU 适配配置切换。
2. 保持 `platform: linux/amd64` 与 `*-amd64` 镜像标签。生产镜像应在 x86 Linux 构建并完成常规单次 AISBench 验证。
3. benchmark 使用 `mode: perf`、`request_rate: 0`、`retry: 1`；移除 concurrency_levels/requests_per_level。输入输出长度、样本数、tokenizer、生成参数、预热数在整个搜索中保持不变，只有 concurrency 被搜索器覆盖。
4. request_count 至少达到搜索 max_concurrency 和 min_requests；output_length 至少为 2。准备固定输出长度负载，并确认后端支持相应生成参数。
5. 复制并修改 `configs/search/slo-capacity.json` 中的业务阈值及预算，然后执行：

```bash
llm-bench up --config /absolute/path/site.json --no-benchmark
llm-bench bench slo --config /absolute/path/site.json --policy /absolute/path/search.json
```

如果栈已以相同配置启动且为 ready/complete，直接执行搜索即可。不要并行运行其他客户端；搜索与本工程 suite 共用 `storage.root/suite.lock`，down 在锁存在时会拒绝清理组件。直接从外部启动的压测客户端无法由此锁隔离，需要实验环境保证独占测试窗口。

## 用同一份 cases.json 逐数据集搜索

`up --cases`、`bench suite --cases` 和 `bench slo --cases` 读取同一种用例格式。SLO 搜索按数组顺序为每个用例重新建立并发网格。业务 JSONL、ShareGPT 多轮对话、固定长度合成数据可共用一份清单。[完整 cases.json](../configs/suites/mixed-datasets.json) · [语料格式与只读挂载](aisbench-sequential.md#在同一份-casesjson-混用三种数据集)

**Mac 快速验证**。从仓库根目录执行。已有 `mac-slo-search` 栈可直接执行第二条命令。示例策略用很小的样本池验证功能，生产测试请按业务样本量设置 `min_requests` 和重复次数。[Mac 混合搜索策略](../configs/search/mac-mixed-datasets.json)

```bash
llm-bench up --config configs/stacks/mac-slo-search.json --no-benchmark
llm-bench bench slo --config configs/stacks/mac-slo-search.json \
  --cases configs/suites/mixed-datasets.json --policy configs/search/mac-mixed-datasets.json
llm-bench down --config configs/stacks/mac-slo-search.json
```

**Linux x86**。先按上一节准备并启动站点，然后对已有栈运行：

```bash
llm-bench bench slo --config /srv/bench/site.json \
  --cases /srv/bench/cases.json --policy /srv/bench/slo-policy.json
```

### 负载、阈值和预算

- **清单决定负载**。每轮使用同一用例的语料、样本范围、输出上限、预热和生成参数。相对语料路径以清单所在目录为基准。搜索器逐轮设置 `concurrency`，因此清单中普通 suite 使用的并发值可以保留。
- **策略决定判定口径**。各组使用同一份 `--policy` 中的阈值、统计口径、范围、步长和重复次数。生产用 `verified_max` 时，只有网格中所有更高候选都被排除，才会报告已验证的最大并发。`max_rounds` 和 `max_duration_seconds` 对每组单独计数。策略的冷却在 trial 之间生效，清单的 `cooldown_seconds` 在两组搜索之间生效。
- **每组的样本必须足够**。JSONL 每行计一次请求，ShareGPT 每轮计一次请求，合成数据按 `request_count` 生成。每组的正式请求数均须覆盖 `min_requests` 和并发上限。ShareGPT 同一对话的轮次串行，所选的独立对话数也须达到并发上限。例如 2 段对话共 3 轮可搜索并发 1–2。CLI 在第一轮前检查全部用例，指出不合规的用例名。
- **输出长度口径**。合成用例严格核对固定输出长度。本地文件用例允许提前 EOS，输出须落在 1 至 `output_length` 之间。策略包含 TPOT 或 ITL 时，每个正式响应至少需要 2 个输出 token。各指标在 AISBench 报告的样本数必须完整，实测峰值并发必须达到候选值。未满足这些条件的轮次记为 `INVALID`。
- **继续或停止**。测完网格仍无可行点时，该组记录 `no_feasible_concurrency`，继续搜索下一组。无效 trial、超时或预算耗尽会停止该组；默认 `stop_on_failure: true` 同时停止后续用例，设为 `false` 可继续下一组。中断总是停止整批。整批使用同一把运行锁。

### 阅读汇总

启动时终端打印 `SLO suite report` 的绝对路径。目录结构如下。

```text
storage.root/data/results/slo-search/<UTC时间戳>/
├── suite-results.json
├── stack.json
└── cases/<用例名>/
    ├── policy.json
    ├── stack.json
    ├── search-results.json
    └── round-001-c1-r1/
        ├── trial.json
        └── results/
```

`suite-results.json` 按输入顺序列出每组的数据来源、`recommended_concurrency`、`maximum_verified`、停止原因和独立报告路径。推荐值所在档位的多次测量中，每项指标最差的统计值记录在 `worst_recommended_latencies_ms`。例如策略选 P99 时，它记录各次 P99 的最大值。未完成的组保留已观察结果，推荐值为 `null`；未启动的组标为 `skipped`。每个 trial 的 `trial.json` 还记录选中指标的毫秒值、实测峰值并发、输出 token 范围和明细证据路径。

汇总的 `status: complete` 表示所有组均完成搜索，可能包含没有可行点的组。退出码 0 表示每组均有推荐值；存在无可行点、无效测量或预算停止时返回非零。多组搜索被 Ctrl-C 或 SIGTERM 中断时返回 130。

### Mac 模拟验收结果（2026-11-08）

本次用 Apple Silicon Mac、离线 arm64 镜像和仓库的混合用例实跑。为复现清晰的边界，模拟服务设置 `simulation_latency.capacity=1`、`overload_delay_seconds=0.4`。搜索范围为 1–2，每档重复 2 次，按 P99 TTFT ≤ 200 ms 且 P99 TPOT ≤ 50 ms 判定。下表时延均取该档两次 P99 中的较大值。

| 用例 | 正式请求总数 | 并发 1 TTFT / TPOT | 并发 2 TTFT / TPOT | 已验证最大并发 |
| --- | ---: | ---: | ---: | ---: |
| 业务 JSONL | 8 | 8.9 / 6.1 ms | 407.9 / 6.4 ms | 1 |
| ShareGPT | 12 | 11.0 / 6.2 ms | 413.3 / 6.3 ms | 1 |
| 64 入 / 16 出合成数据 | 16 | 11.5 / 6.5 ms | 414.5 / 6.4 ms | 1 |

12 次 trial 的逐请求时间戳都达到了目标峰值。模拟服务的过载延迟使三组都在并发 2 超过 TTFT 阈值，生产 GPU 的结论需要使用现场服务及业务语料重新测量。

## AISBench 上游能力核查（2026-11-08）

GitHub 最新正式 Release 为 **v3.1-20261013-master**（10 月 16 日发布）。在该版本的 CLI 与公开文档中未找到根据每轮 TTFT/TPOT 自动选择下一档并发的入口。`ais_bench --search` 用于查找模型、数据集等任务配置的路径。[官方 Release](https://github.com/AISBench/benchmark/releases/tag/v3.1-20261013-master) · [该版本命令行实现](https://github.com/AISBench/benchmark/blob/5a63d936e40dd04addfcffb2401f84cd0e53bb46/ais_bench/benchmark/cli/argument_parser.py)

当日默认分支 `master` 的 HEAD 是 **e330dc1**（11 月 5 日），与本工程镜像固定提交一致。它支持将预先列出的多档并发和请求数配对展开，执行后汇总 CSV/HTML。在该提交的 CLI、调度代码与性能指南中也未找到按 SLO 反馈搜索最大并发的原生入口。本工程由 `llm-bench bench slo` 选择下一档并核验结果，AISBench 负责每个候选的发压和测量。[官方多档并发指南](https://github.com/AISBench/benchmark/blob/e330dc134c33f47671926144d9c143b554bc71cc/docs/source_zh_cn/advanced_tutorials/multi-concurrency_performance.md) · [档位展开实现](https://github.com/AISBench/benchmark/blob/e330dc134c33f47671926144d9c143b554bc71cc/ais_bench/benchmark/cli/sweep.py)

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
2. AISBench 退出成功，存在本例唯一的 synthetic.json、jsonl.json 或 sharegpt.json，以及对应 CSV、逐请求 details JSONL 和时间戳数据库。
3. 正式请求总数与固定 workload 相符，达到 min_requests，零失败，成功数等于总数。
4. 汇总 Max Concurrency 只校验配置是否匹配（它来自 batch_size，不是实测值）。逐条读取 details 的 time_points，以首末时间戳构成左闭右开区间，计算客户端实测峰值重叠数，必须等于本轮目标；同一时刻结束和开始的请求不算重叠。请求明细必须完整、成功且唯一（ShareGPT 使用对话 ID 与从 0 开始的轮次组成键），时间戳必须有限、非负、有序且区间非零；缺失、损坏或重复引用的数据判为 INVALID，不退回使用配置值。
5. OutputTokens 的 Min/Max 须符合该数据类型的输出长度口径。
6. 所选指标 Stage=total、样本 N 完整，单位为 ms，值有限且非负；按 statistic 读取，不用平均值替代 P99。
7. 任一时延超过阈值为 SLO_FAIL，等于阈值算通过。数据/请求失败为 INVALID，立即停止，不能把它当作有效的 SLO 失败边界。

并发校验衡量的是 AISBench 客户端从发起请求到最后一个已记录响应时间点的峰值重叠，不是服务端执行并发，也不保证持续保持目标负载。不能用平均 Concurrency 必须等于目标来替代，因为升压、收尾阶段会降低平均值。当前固定 AISBench 版本的 SQLite 时间戳以 NPY v1 一维 float64 存储；搜索器只读解析该格式，无需在宿主机安装 NumPy，遇到不支持的格式会停止。保留原始 details 与 db_data 才能复核测量。

每组搜索要求零请求失败、perf 模式和 request_rate=0。QPS 搜索、容忍失败预算、压力模式、自定义合成长度分布和服务参数寻优不在该入口中。实际输入 token 数仍须按模型 tokenizer 核对。

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

search-results.json 记录 rounds[].phase（下界、上界、二分、高档验证）、并发、各次重复的状态/阈值违例/实测时延、起止时间、全局输出吞吐、配置并发 configured_concurrency、实测峰值 measured_peak_concurrency、明细证据相对路径 concurrency_evidence、报告相对路径及停止原因。报告路径相对于该 trial 的目录。用这些起止时间与 Grafana/Loki 对齐分析。

`maximum_verified=true` 表示**配置网格内、更高候选已被本次测量排除**；不会把 Mac 结果解释为显卡性能，也不会把该值直接等同于生产限流配置。选定档位后应做长期压力、邻近档位和真实业务分布复测。

镜像构建使用 `--network=host`，构建机须能访问软件源；Docker Desktop 需支持并启用 host networking。详见 [构建网络说明](offline-deployment.md#32-构建两个项目镜像)。
