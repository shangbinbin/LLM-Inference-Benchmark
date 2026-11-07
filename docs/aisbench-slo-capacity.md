# AISBench：自动筛选满足 SLO 的推理服务容量

命令入口与安装步骤见 [CLI 快速上手](cli.md#快速上手)。

需要根据每轮结果自动调整并发时，使用新增的 [SLO 自适应搜索功能](aisbench-auto-slo.md)和 `llm-bench bench slo`。本页保留固定候选扫描、离线报告筛选和 SLO 业务口径说明；两种入口均不调整被测推理服务。

## 1. 先明确搜索目标与版本能力

SLO（服务等级目标）是业务给定的约束，例如 P99 TTFT ≤ 1000 ms、P99 TPOT ≤ 50 ms。压测可以找出满足这些约束的并发/吞吐范围，不能自动决定业务应该接受多大的延迟。

工程固定 AISBench 提交 `e330dc134c33f47671926144d9c143b554bc71cc`。核对该提交的 CLI、runner、性能文档后：

| 能力 | 当前可用方式 |
| --- | --- |
| 顺序扫描多个并发档 | AISBench 原生 `--batch-size` / `--num-prompts` 配对列表 |
| 不同长度、并发、QPS 的测试例顺序执行 | 本工程 `llm-bench bench suite` |
| 自动按业务时延阈值筛选已测档位 | 本手册配套的只读示例 `docs/examples/select_aisbench_slo.py` |
| 根据上一轮 SLO 结果自动调整并发 | 本工程 `llm-bench bench slo`，支持二分和更高候选验证；上游 AISBench CLI 没有该闭环 |
| 自动修改服务参数、重启服务并寻优 | 不在当前工程执行范围；与客户端容量扫描是不同任务 |

不要把 AISBench 的 `--search` 当作 SLO 搜索：它只查找模型/数据集/summarizer 的配置路径。本手册中的阈值字段也是本工程示例策略文件的字段，不是 AISBench 原生 CLI 参数。

本页示例采用**有限候选集扫描并自动筛选**。结果名称为“已测候选中的最大达标并发/最佳达标吞吐”，不宣称找到连续负载空间的全局最优点。新增自适应入口的验证范围与停止条件见其独立手册。

## 2. 定义一份可重复执行的 SLO

以下均为演示值，执行前改为业务标准：

| 约束 | 示例 | 判定口径 |
| --- | --- | --- |
| P99 TTFT | ≤ 1000 ms | 流式请求首个有效输出时延 |
| P99 TPOT | ≤ 50 ms | 输出阶段每 token 摊薄时延；需输出超过 1 token |
| P99 E2EL | ≤ 30000 ms | 请求完成的端到端时延 |
| 成功率 | 100% | 此示例严格要求零失败，不允许用成功请求的低延迟掩盖失败 |
| 每轮正式请求 | ≥ 1000 | 不含预热；只是示例最低样本量，不是统计置信保证 |
| 重复测试 | 3 次 | 同一并发的每次测试均达标才纳入候选 |

本手册判定的是**各指标独立的 P99 均达标**，不等于“99% 的请求同时满足 TTFT、TPOT、E2EL”。如果业务要求联合达标率/Goodput，需要基于逐请求数据及对应 SQLite 时间点计算联合条件，并把失败请求计为不达标；不能从三个 P99 或全局吞吐反推联合达标率。

不能平均多轮 P99。示例分别检查每轮 P99，并报告最差一轮的 P99；这也不等于合并原始请求后的总体 P99。报告使用 AISBench 已格式化的毫秒值，边界附近还需考虑舍入误差并用原始明细复核。

## 3. 准备服务和观测链路

1. 从 `configs/stacks/nvidia-single.json` 复制完整 site.json，填写服务根 URL、model、指标端点、日志目录、真实 tokenizer 和数据目录。海光选择 gpu.vendor=hygon 或在本机使用 auto。
2. Linux GPU 服务器固定 **linux/amd64（x86）**，使用 `*-amd64` 自建镜像标签；Mac 模拟使用独立 arm64 标签。
3. 固定模型/量化、服务版本、GPU 数量、TP/PP/PD 拓扑、缓存策略、输入输出长度、tokenizer、流式协议和预热方式。不同长度应分别执行一轮容量搜索，不能直接混为同一组候选。
4. 确认上下文窗口能容纳输入与输出，真实输出长度与请求目标一致。tokenid 解码再编码仍可能改变 token 数，见 [合成数据说明](aisbench.md)。
5. 只启动观测组件，避免先发送默认 workload：

```bash
llm-bench up --config /absolute/path/site.json --no-benchmark
```

已有同一配置的观测栈且状态 ready/complete 时直接复用。不能同时运行其他压测。SGLang 服务应启用指标；PD 场景把 inference.base_url 指向对外路由入口，分别采集 prefill/decode 角色，不把不同角色的重复请求计数直接相加。

## 4. 自动生成候选测试清单

从仓库根目录运行下面代码，生成策略和 suite，文件位于 Git 忽略的 generated/。这里固定输入 1024、输出 256、每轮 1000 条请求、三轮重复，扫描并发 1/8/16/32。全部使用无限定 QPS 的并发上限模式（request_rate=0）。

```python
import json
from pathlib import Path

folder = Path('generated/slo-capacity')
folder.mkdir(parents=True, exist_ok=True)
policy = {
    'concurrency_candidates': [1, 8, 16, 32],
    'repeats': 3,
    'min_requests': 1000,
    'ttft_p99_ms': 1000,
    'tpot_p99_ms': 50,
    'e2el_p99_ms': 30000,
}
cases = []
for repeat in range(1, policy['repeats'] + 1):
    for concurrency in policy['concurrency_candidates']:
        cases.append({
            'name': f'c{concurrency:03d}-r{repeat}',
            'benchmark': {
                'dataset_type': 'tokenid', 'prefix_length': 0,
                'input_length': 1024, 'output_length': 256,
                'request_count': 1000, 'concurrency': concurrency,
                'request_rate': 0, 'warmups': 16, 'retry': 1, 'mode': 'perf',
                'generation_kwargs': {'temperature': 0, 'ignore_eos': True},
            },
        })
suite = {'cooldown_seconds': 15, 'stop_on_failure': True, 'cases': cases}
for name, value in [('policy.json', policy), ('suite.json', suite)]:
    (folder / name).write_text(json.dumps(value, indent=2) + '\n')
```

将代码保存为 `generated/create-slo-suite.py` 后执行 `python3 generated/create-slo-suite.py`；也可直接粘贴到 Python 解释器。若后端不支持 ignore_eos，替换为该后端支持的参数并核对实际输出长度，不要为了让压测跑通而忽略短输出。

顺序为一轮完整候选扫描后再进入下一轮，有助于识别随时间产生的漂移；不能消除温度、缓存和其他负载的影响。cooldown_seconds=15 只是示例，应等队列排空后再开始下一例。预热每例都会执行，不计入 request_count。

## 5. 执行 AISBench 扫描

```bash
llm-bench bench suite \
  --config /absolute/path/site.json \
  --cases generated/slo-capacity/suite.json
```

每例顺序运行并保留独立配置、client.log、性能 JSON/CSV/明细。脚本最后打印本次 `Suite report: .../suite-results.json`，记录这个**精确路径**；后续筛选不要使用不受控的 `latest` 或混入上次实验数据。

此时 suite 中的 passed 仅检查程序退出、报告完整性和请求成功数，尚未检查时延 SLO。时延超标不会中止扫描；程序/请求失败则按 stop_on_failure=true 停止。发生失败时先排查容量耗尽、服务错误或链路问题，不能把没有执行到的高档位记作达标。默认扫描最多正式发送 4 × 3 × 1000 = 12000 条请求，预热另计；要在测试窗口内安排好负载上限。

## 6. 自动筛选报告

无需重建容器或再次发压，主机 Python 标准库即可执行：

```bash
python3 docs/examples/select_aisbench_slo.py \
  --suite generated/slo-capacity/suite.json \
  --policy generated/slo-capacity/policy.json \
  --results /absolute/path/to/this-run/suite-results.json
```

示例按实际版本的报告读取方式：

- `suite-results.json`：核对预期用例、实际参数、退出码、报告路径；所有轮次除 concurrency 外的 workload 必须相同。
- `performances/aisbench-target/synthetic.json`：核对正式请求数、零失败，以及全局 **Output Token Throughput**。
- 同目录 `synthetic.csv`：只读取 Stage=total 的 TTFT、TPOT、E2EL 的 P99，单位要求 ms，并确认有效样本 N 等于正式请求数。

筛选器还要求观测到的 Max Concurrency 达到设定并发，且 OutputTokens 的 Min/Max 均等于设定输出长度、输出长度至少为 2。达不到并发时先检查客户端资源/请求规模；提前 EOS、短输出或变长输出测试不适用于这个固定长度示例。输入长度仍应按真实 tokenizer 的统计另行核对，不能假定采样 tokenid 的长度等于服务实际输入 token 数。

它不会把请求级 CSV 的 OutputTokenThroughput 平均值当作全局吞吐，不会用 Average 代替 P99。缺文件、缺指标、样本不足、请求失败、单位不符、参数不一致均为 INVALID，不能按零时延通过。

结果保存到本次 suite-results.json 同目录的 `slo-selection.json`：

| 字段/状态 | 含义 |
| --- | --- |
| trials[].status=PASS | 单轮样本完整且三个 P99 均不高于阈值 |
| SLO_FAIL | 数据有效，但至少一项时延超标 |
| INVALID | 不能据此判断容量；先修复或补测 |
| complete | 所有预期用例均获得有效判定 |
| candidates[].all_repeats_passed | 该并发的全部重复轮次均通过 |
| max_observed_passing_concurrency | 已测候选中最大的全轮次达标并发 |
| best_observed_passing_throughput | 达标候选中，最差一轮全局输出吞吐最高的档位；同值优先较低并发 |
| search_ceiling_reached | 最大候选仍通过，搜索上限尚未覆盖失败边界 |

存在 INVALID 时不生成最终推荐，即使某个低档已经通过；先补齐完整实验再筛选。完整实验全部超标时，推荐为 null。退出码 0 表示存在有效达标候选，1 表示不完整或没有达标候选，2 表示策略/输入格式错误。示例不支持压力模式、非零 request_rate、容忍失败率或自动扩大搜索范围。

**举例（非实测）：** 1、8、16 三档全部重复通过，32 档有一轮 P99 TTFT 超标，则最大已测达标并发为 16。若并发 8 的保守吞吐反而更高，吞吐目标下的推荐可能是 8。并发上限和最佳吞吐不是同一个优化目标。

## 7. 细化边界与稳定性复测

如果 16 通过、32 超标，可以将候选改为 16/20/24/28/32，使用相同 workload 生成**新** suite 再运行、筛选；如果最高候选仍通过，在业务允许的上限内扩大范围。如果最低候选就超标，先检查单请求基线、网络和长度，不应只增加并发。

二分只在时延随负载大致单调且噪声受控时才有意义。Prefix cache、批处理效率、P/D 路由、热降频和其他业务可能造成非单调结果；本手册不把一次通过/失败当作严格数学边界。现在可用 [slo_search.py](aisbench-auto-slo.md)执行闭环，其默认 verified_max 策略还会检查高于二分结果的未测候选；本页离线筛选示例自身仍不发起新测量。

对最终候选及其邻近较低档进行更长时间的持续压力复测，见 [顺序执行手册](aisbench-sequential.md)。不要将压力模式输出直接传给上述 perf 示例筛选器。确定上线容量时还需评估业务流量波动和资源余量，不应直接把短测峰值作为生产限流值。

## 8. 如果要搜索 QPS，而不是并发

固定输入输出、并发上限和其他变量，为 suite 的各例分别设置 request_rate=1/2/4/8 等正数。一定要核对实际请求发出速率与完成吞吐：batch_size 过小或客户端资源不足会使目标 QPS 无法实现，从而产生看似达标的错误结论。不要同时改变 QPS 和并发后只将变化归因于一个因素。

本手册附带筛选器只处理 request_rate=0 的并发搜索，会明确拒绝 QPS 测试。QPS 搜索需增加“实际负载达到目标”的判定和对应结果分组。不同长度/P/D 配比/缓存命中率同样应独立搜索；现有顺序清单支持发起这些测试，但不等于已经实现了多维自动寻优。

## 9. Grafana 与 Loki 联合判读

- TTFT 超标：对照请求队列、Prefill、路由延迟和客户端 CPU/网络；不要直接归因为 GPU 算力不足。
- TPOT 超标：对照 Decode 负载、显存、设备利用率以及 PD 传输；核对输出确实超过一个 token。
- E2EL 超标：同时核对实际输出长度、TTFT 和后续生成阶段。
- 失败或异常短输出：查询同一测试起止时间的 Loki 错误日志，核对服务超时、OOM、限流和 stop/EOS。
- NVIDIA/海光指标按栈厂商切换，SLO 仍以相同客户端指标口径判断；Mac fixture 只能验证流程，不能产生真实显卡容量结论。

## 10. 服务参数自动寻优的另一个工具范围

官方 MindStudio `msmodeling optix` 提供服务参数实测寻优，并可使用 AISBench 作为测评后端。它涉及服务框架参数和服务生命周期，不能等同于 AISBench CLI 原生 SLO 搜索，也不能直接假定覆盖本工程的 SGLang、海光或多 P 多 D 环境。本手册只提供资料入口，不安装或调用该工具。

依据与核对入口：

- [AISBench 固定提交 CLI（--search 的含义）](https://github.com/AISBench/benchmark/blob/e330dc134c33f47671926144d9c143b554bc71cc/ais_bench/benchmark/cli/argument_parser.py)
- [AISBench 原生多档扫描](https://github.com/AISBench/benchmark/blob/e330dc134c33f47671926144d9c143b554bc71cc/docs/source_zh_cn/advanced_tutorials/multi-concurrency_performance.md)
- [性能指标计算、单位与报告格式](https://github.com/AISBench/benchmark/blob/e330dc134c33f47671926144d9c143b554bc71cc/ais_bench/benchmark/calculators/base_perf_metric_calculator.py)
- [MindStudio 服务化实测寻优官方手册](https://mindstudio-docs-master.readthedocs.io/zh-cn/latest/msmodeling/docs/zh/user_guide/msmodeling_optix_user_guide/)

本文按固定提交源码及本工程现有报告核对，不将动态 latest 文档中可能新增的参数自动套用到当前镜像。
