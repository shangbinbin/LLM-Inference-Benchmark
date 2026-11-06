# AISBench 多测试例顺序执行

**支持。** 本项目固定的 AISBench 提交已包含 2026-11-04 新增的多并发档扫描：一次命令按列表展开各档，默认串行，普通 runner 在相邻任务间冷却 5 秒，并输出批量 CSV/HTML。不同输入输出长度、数据类型和压力模式的任意用例清单，可以使用本项目的顺序调度脚本，复用已启动的观测链路。

## 选择入口

| 需求 | 使用方式 |
| --- | --- |
| 同一个输入输出配置，依次扫描并发 1/8/16/32 | 原生多档扫描，栈 JSON 的 concurrency_levels / requests_per_level |
| 依次执行不同长度、不同 QPS、string/tokenid、perf/pressure | `run_aisbench_suite.py` + suite JSON |
| 已熟悉 AISBench，组合多个模型和数据集 | 原生 Python models/datasets + model_dataset_combinations；显式 max_num_workers=1 |

“任务串行”与“请求并发”是两个维度：同时只有一个测试例运行，但该测试例可以有 32 个在途请求。不要通过提高 max_num_workers 加速针对同一被测服务的性能对比，否则不同用例会互相抢占服务资源。

## 方式一：原生并发档扫描，一键启动

1. 复制 `configs/stacks/nvidia-single.json`，填写 x86 Linux 的服务地址、真实 tokenizer、日志/数据路径。
2. 在 benchmark 中增加两个配对数组：

```json
{
  "dataset_type": "tokenid",
  "input_length": 1024,
  "output_length": 256,
  "prefix_length": 0,
  "request_count": 512,
  "concurrency": 1,
  "concurrency_levels": [1, 8, 16, 32],
  "requests_per_level": [64, 128, 256, 512],
  "request_rate": 0,
  "warmups": 8,
  "retry": 1,
  "mode": "perf",
  "generation_kwargs": {"temperature": 0, "ignore_eos": true}
}
```

3. 启动：

```bash
python3 scripts/benchmark_stack.py up --config /absolute/path/site-sweep.json
```

实际按 `(并发1,64条) → (并发8,128条) → (并发16,256条) → (并发32,512条)` 执行；不是两个数组的笛卡尔积。此时 `concurrency` 被各档覆盖。request_count 是每档合成样本池大小，必须不小于最大的 requests_per_level；每档预热独立，不计入正式条数。列表长度一致、并发档唯一，当前入口只允许 perf 模式，组合不超过 200。

启动器转换为以下 AISBench 参数（容器内执行示意）：

```bash
ais_bench /config/aisbench.py --mode perf --max-num-workers 1 \
  --num-warmups 8 --batch-size 1 8 16 32 --num-prompts 64 128 256 512
```

位置配置文件必须放在列表参数前面，避免被 nargs 列表解析。启动器在多档模式下**不传 `--debug`**：本固定版本 debug runner 虽然顺序执行，却不执行普通 runner 的 5 秒冷却。单档模式仍用 debug 把日志显示在容器 stdout。

### 报告与失败处理

`storage.root/data/results/<时间戳>/` 内包含：

- `combos_manifest.json`：档位和参数快照，确认执行顺序、并发和请求数。
- `batch_perf_summary.csv`：各档对比汇总。
- `batch_perf_report.html`：可离线打开的可视化对比报告。
- `performances/aisbench-target-bs<N>/synthetic.json`、`.csv`、`synthetic_details.jsonl`：逐档指标和逐请求详情。
- `logs/infer/`：非 debug 模式的逐任务日志。

原生扫描某一档失败后会继续后续档；退出码不能代替逐档成功率检查。本项目启动器会在扫描结束后检查报告完整性、失败请求和正式请求总数，不符合即返回失败。核对每档 Total/Success/Failed Requests，排除失败档再比较吞吐和 P99。要“任意一例失败立即停止”，使用下面的 suite 入口。5 秒只是工具默认冷却，不保证大型服务的排队/缓存状态已恢复；应结合服务监控调整实验流程。

## 方式二：预设异构用例清单，严格按数组顺序运行

本仓库提供 `scripts/run_aisbench_suite.py`。它按 cases 数组顺序启动独立 AISBench 容器，等待上一例退出并检查报告后再启动下一例；观测容器保持运行。每例保存配置快照、客户端日志、独立报告和起止时间，便于对照 Grafana/Loki。

### Mac 可直接运行的示例

```bash
# 若此前已有栈，无需再次 up；确保其单次压测已结束
python3 scripts/benchmark_stack.py up --config configs/stacks/mac-simulation.json --no-benchmark
python3 scripts/run_aisbench_suite.py \
  --config configs/stacks/mac-simulation.json \
  --suite configs/suites/mac-smoke.json
```

示例依次运行 string 32/16、tokenid 64/8、3 秒持续压力三个用例。所有客户端和观测组件为实际容器，仅推理响应/GPU 指标由 fixture 提供。

### Linux x86 生产用例

先准备完整 site.json（platform 为 linux/amd64、镜像标签为 amd64），再创建 suite.json：

```json
{
  "cooldown_seconds": 15,
  "stop_on_failure": true,
  "cases": [
    {
      "name": "short-c8",
      "benchmark": {"dataset_type": "tokenid", "input_length": 1024, "output_length": 256, "concurrency": 8, "request_count": 200, "mode": "perf"}
    },
    {
      "name": "long-c16-qps4",
      "benchmark": {"dataset_type": "tokenid", "input_length": 8192, "output_length": 512, "concurrency": 16, "request_count": 200, "request_rate": 4, "mode": "perf"}
    },
    {
      "name": "sustained-c32",
      "benchmark": {"dataset_type": "tokenid", "input_length": 2048, "output_length": 256, "concurrency": 32, "request_count": 200, "request_rate": 0, "mode": "pressure", "pressure_seconds": 300}
    }
  ]
}
```

```bash
python3 scripts/benchmark_stack.py up --config /absolute/path/site.json --no-benchmark
python3 scripts/run_aisbench_suite.py \
  --config /absolute/path/site.json --suite /absolute/path/suite.json
python3 scripts/benchmark_stack.py down --config /absolute/path/site.json
```

覆盖规则与运行语义：

- 每例从同一 base 配置独立复制 benchmark，再覆盖自己的字段，**不继承上一例**；未写的 warmups/retry/generation_kwargs 等来自 base。
- name 必须唯一，仅小写字母、数字、短横线；传入不支持的覆盖字段会在发压前报错。
- suite 一例一个 workload，不嵌套 concurrency_levels；为需要的各档分别写一例。
- inference、GPU、存储和架构沿用 base，不能在 suite 中变更服务；多服务实验使用独立配置/观测栈。
- cooldown_seconds 是上一例报告完成到下一例启动前的等待秒数；请求阶段不会重叠。
- 默认 stop_on_failure=true；客户端非零退出、报告缺失、失败请求大于零、perf 成功条数不符合目标，均判失败并停止。false 会记录失败并继续，最终命令仍返回失败。
- 持续压力按时长结束，不要求完成条数等于样本池 request_count，但要求有成功报告且失败请求为零。
- 同一 storage.root 的 suite.lock 防止同时启动两份 suite；不要在执行中更改配置或 down。异常断电留下 lock 时，先检查对应 `*-suite-client` 确实不在运行，再删除锁文件。
- 不自动清空服务缓存，不重启推理服务，不自动恢复失败用例。复跑时新建仅包含待重跑用例的清单，输出会进入新的 suite 目录。

输出位于 `storage.root/data/results/suites/<UTC时间戳>/<name>/`，包含 `stack.json`、`aisbench.py`、`client.log`、`results/`。根目录 `suite-results.json` 记录实际参数、顺序、起止时间、退出码、passed 和报告路径。这里的 passed 仅表示功能/请求完整性，尚未增加业务 TTFT/吞吐 SLO 门槛。

## 原生自定义模型/数据集组合

AISBench Python 配置可声明 models、datasets 及 model_dataset_combinations 精确配对。不写配对时会生成模型 × 数据集笛卡尔积；各模型和数据集必须有唯一 abbr，否则可能被合并/去重。设置 `--max-num-workers 1` 可限制一次一个任务，但分区器可能合并同模型任务，不应把任意复杂配对列表直接当成严格的外层测试步骤清单。需要明确先后顺序、独立冷却和失败即停时，优先使用 suite。

## 核对依据

- [官方多并发档指南（固定提交）](https://github.com/AISBench/benchmark/blob/e330dc134c33f47671926144d9c143b554bc71cc/docs/source_zh_cn/advanced_tutorials/multi-concurrency_performance.md)
- [官方组合配置及性能指南](https://github.com/AISBench/benchmark/blob/e330dc134c33f47671926144d9c143b554bc71cc/docs/source_zh_cn/base_tutorials/scenes_intro/performance_benchmark.md)
- [LocalRunner 串行与冷却实现](https://github.com/AISBench/benchmark/blob/e330dc134c33f47671926144d9c143b554bc71cc/ais_bench/benchmark/runners/local.py)
- [参数展开及校验](https://github.com/AISBench/benchmark/blob/e330dc134c33f47671926144d9c143b554bc71cc/ais_bench/benchmark/cli/sweep.py)
