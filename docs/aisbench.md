# AISBench 合成数据与推理服务压测

命令入口与安装步骤见 [CLI 快速上手](cli.md#快速上手)。

客户端使用 [AISBench/benchmark](https://github.com/AISBench/benchmark)，本项目固定源码提交 [`e330dc134c33f47671926144d9c143b554bc71cc`](https://github.com/AISBench/benchmark/tree/e330dc134c33f47671926144d9c143b554bc71cc)（2026-11-05）。容器内为 Python 3.11 和 CPU PyTorch，不占用被测 GPU。容器记录完整依赖清单 `/opt/aisbench/dependencies.txt`；主机只需 Python 标准库和容器运行时。

AISBench 内置后端名称仍包含 `vllm_api_*`，这是它的 OpenAI 兼容协议实现类，不是 `vllm bench serve` 命令，也不会启动 vLLM 服务。项目默认为流式 `/v1/completions`，可用 `inference.api: chat` 选择 `/v1/chat/completions`，对 SGLang 等兼容服务需确认请求字段与 usage 响应。

## 指定输入/输出长度

在栈 JSON 的 `benchmark` 中填写：

```json
{
  "dataset_type": "string",
  "input_length": 1024,
  "output_length": 256,
  "request_count": 1000,
  "concurrency": 16,
  "request_rate": 0,
  "warmups": 1,
  "mode": "perf",
  "generation_kwargs": {"temperature": 0, "ignore_eos": true}
}
```

启动器生成标准 AISBench Python 配置：选择 `SyntheticDataset`，设置 RequestCount；string 输入/输出分布采用 uniform，并将 MinValue、MaxValue 同时设为指定长度。每条请求携带其输出长度，模型配置同时设置 max_out_len。执行 `plan` 即可在 `storage.root/config/<hash>/aisbench.py` 查看实际配置。

### string：重复字符串负载

固定提交的 [SyntheticDataset 源码](https://github.com/AISBench/benchmark/blob/e330dc134c33f47671926144d9c143b554bc71cc/ais_bench/benchmark/datasets/synthetic.py) 将输入构造成空格分隔的重复 `A`，不是任意模型 tokenizer 下严格相同数量的输入 token，也不是每请求不同的随机文本。长度分布随机，但相同长度的文本相同。Mac 示例以此完成网络/流式/结果验证。

string 生成过程本身不依赖 tokenizer，但本固定版本的性能汇总阶段会强制加载 tokenizer，所以两种模式都必须填写 `storage.tokenizer`。Mac 使用仓库内的小型测试词表；生产必须替换为被测模型真实 tokenizer，不能用模拟词表解释真实模型结果。

因此 input_length=1024 不保证服务 usage.prompt_tokens=1024；chat 模板和特殊 token 还可能增加长度。重复前缀会显著影响 prefix cache 命中率，不能把这种输入的吞吐当作真实业务吞吐。需要无共享前缀的基线时，选择 tokenid 并核对解码后的输入，或用实际 tokenizer 验证过的自定义数据；服务端是否关闭 prefix cache 由被测服务管理员决定。

### tokenid：基于被测模型 tokenizer 生成随机 token

1. 将被测模型 tokenizer 文件放在本地主机目录，包括 tokenizer_config.json、tokenizer.json/词表等所需文件，无需模型权重。
2. 配置 `storage.tokenizer` 为该目录的绝对路径，容器只读挂载到 `/tokenizer`。
3. 设置 `dataset_type: tokenid`、`input_length`、`prefix_length`（默认 0）和 `output_length`。
4. 生成器设置 TokenIdConfig.RequestSize 和 PrefixLen，并把模型 path 指向 `/tokenizer`。AISBench 排除特殊 token 后随机采样，再解码成字符串发给接口。
5. 核对结果中的 InputTokens/OutputTokens。decode→encode 不一定保持原 token 数；PrefixLen 表示采样时共享 token 数，不保证实际缓存命中率。当前默认关闭 trust_remote_code，不会自动执行模型仓库代码。

```json
{
  "dataset_type": "tokenid",
  "input_length": 1024,
  "prefix_length": 0,
  "output_length": 256,
  "request_count": 1000,
  "concurrency": 16,
  "request_rate": 0,
  "warmups": 1,
  "mode": "perf",
  "generation_kwargs": {"temperature": 0, "ignore_eos": true}
}
```

tokenid 配置的输出上限来自 max_out_len。`ignore_eos` 属于后端扩展参数，AISBench 会转发而不会替服务生成固定长度。后端不支持时应移除/替换该参数，明确输出只是上限；即便支持，也需确认没有 stop 条件、上下文窗口不足或请求失败导致提前停止。不要仅凭配置值声称获得严格固定输入/输出 token。

### 其他分布与可复现性

AISBench string 原生支持 uniform、gaussian、zipf，可在生成的 Python 配置副本中将 Input/Output 的 Method、Params 改为需要的分布，然后使用下文直接容器命令。gaussian 的 Var 在源码中是方差（采样标准差为 sqrt(Var)），并被 MinValue/MaxValue 截断；zipf 也被上下界裁剪。高级字段的准确含义以固定提交的[合成配置](https://github.com/AISBench/benchmark/tree/e330dc134c33f47671926144d9c143b554bc71cc/ais_bench/benchmark/configs/datasets/synthetic)为准。

本栈 JSON 简化入口只实现固定长度；不会把尚未实现的高级分布字段静默传入。原生合成数据在加载时生成；若要严格复现实验，应固定客户端/服务/tokenizer 版本并保存实际请求数据及 seed/调度设置，不能仅依赖相同长度和请求数。上下文窗口至少覆盖真实输入（含模板）与最大输出。

## 启动压测

推荐使用完整栈入口，自动等待观测链路和服务就绪：

```bash
llm-bench up --config /path/to/site.json
```

如果观测链路已经由其他系统管理，可直接用同一 AISBench 镜像与 Python 配置（路径按实际生成目录修改）：

```bash
docker run --rm --network <project>-net \
  --mount type=bind,src=/absolute/path/aisbench.py,dst=/config/aisbench.py,readonly \
  --mount type=bind,src=/absolute/path/results,dst=/results \
  llm-benchmark-aisbench:e330dc1-amd64 \
  /config/aisbench.py --mode perf --debug --num-warmups 1
```

两种模式都需增加 `--mount type=bind,src=/absolute/path/tokenizer,dst=/tokenizer,readonly`（放在镜像名之前）。Linux 使用 amd64 镜像；Apple Silicon 模拟改用 `e330dc1-arm64`。直接运行绕过了启动器的就绪检查，需自行确认服务和采集链路。生成的 Python 文件不修改安装包内部默认配置；同一安装可用于多套实验。

## 发压模式及参数

| 方式 | 项目配置 / AISBench 用法 | 如何理解 |
| --- | --- | --- |
| 固定样本性能测试 | `mode: perf`，`request_count: N` | 完成指定样本数，含性能计算与汇总 |
| 并发上限测试 | `concurrency: C`、`request_rate: 0` | batch_size 是客户端在途请求上限，不是服务端 batch/token budget；完成后继续补请求 |
| 定速请求 | `request_rate: R` | QPS 调度目标，仍受客户端并发、延迟与资源限制；实际发出速率可能低于目标 |
| 持续压力测试 | `mode: pressure`、`pressure_seconds: 60` | 映射为 `--mode perf --pressure --pressure-time 60`；可循环使用数据，完成数不等于原始 RequestCount |
| 仅重新统计 | 直接 `--mode perf_viz --reuse <run-id>` | 读取已有结果；需保留原始配置、性能明细和输出目录 |
| 组合实验 | AISBench Python 配置的 models/datasets、model_dataset_combinations | 可组合不同并发、速率、输入/输出长度；本栈 JSON 当前一次一个 workload |
| 精度测试 | AISBench all/infer/eval/viz | 不是本项目合成性能入口；随机合成数据不应计算精度 |

`warmups` 不计入正式请求数；在检查模拟服务请求计数时应考虑预热。本固定版本源码使用 `range(retry)`，因此 retry 表示总尝试次数：默认 `retry=1` 才是一次请求、不自动重试；`0` 会完全不发请求，启动器会拒绝。可配置 benchmark.retry，但需要记录重试对失败率的影响。CPU、客户端网卡、连接数和容器资源也可能成为瓶颈。

单档调用使用 `--debug`，方便把日志留在容器 stdout；原生多档使用普通 runner 以保留冷却行为。可通过 `docker logs <project>-aisbench` 查看启动信息。正式日志、报告和配置转储位于 `storage.root/data/results/` 的 AISBench 时间戳目录。多测试例步骤见 [批量顺序执行指南](aisbench-sequential.md)。

## 结果解读

业务 SLO 阈值设定、自动筛选达标并发和边界复测见 [SLO 容量搜索手册](aisbench-slo-capacity.md)。

根据每轮结果动态调整并发、自动寻找最大达标档位，使用 [SLO 自适应搜索](aisbench-auto-slo.md)。

先看成功/失败请求数和实际输入输出长度，再看吞吐、平均值和尾延迟。不要将 Mac fixture 的延迟/吞吐用于 GPU 选型。

| 指标 | 解释及注意事项 |
| --- | --- |
| TTFT | 请求发出到首个有效输出的耗时；排队、Prefill 和网络都会影响 |
| ITL | 相邻流式输出的间隔；一次 SSE chunk 可能含多个 token，应结合 AISBench 的采样与服务返回格式解读 |
| TPOT | 输出阶段按输出 token 数摊薄的耗时；与逐间隔分布 ITL 不等价，单 token 输出需检查有效样本数 |
| E2EL | 请求端到端完成时间，包含 TTFT 和后续生成 |
| Request throughput | 完成请求/测量时长；与设置的 request_rate 不一定相等 |
| Output token throughput | 输出 token/测量时长；区分单请求统计与全局吞吐，不把每请求均值简单求和 |
| InputTokens / OutputTokens | 以真实响应 usage 或工具对应版本统计方式为准，核对与配置目标的偏差 |
| P90/P99 | 尾延迟；样本少时不稳定，需结合 N 与失败率 |

结果通常包含 summary 文本/CSV、performance 下的逐请求 CSV、汇总 JSON、详细采样文件及 HTML 图表；具体路径以当前运行打印结果为准。`perf_viz` 不应重新向服务发请求。对比实验必须固定客户端版本、tokenizer、服务配置、缓存状态、输入输出分布、预热与 SLO 边界。

将时间戳与 Grafana 上的设备利用率/显存/功耗、SGLang 排队、node_exporter CPU/网络指标以及 Loki 错误日志对齐。高 TTFT 但低设备利用率不自动等于算力不足；应检查客户端限流、排队/路由、数据准备和网络。PD 场景分别观察角色指标，避免重复汇总服务端请求计数。

官方依据：[合成数据指南](https://ais-bench-benchmark.readthedocs.io/en/latest/advanced_tutorials/synthetic_dataset.html)、[性能测试与组合配置](https://github.com/AISBench/benchmark/blob/e330dc134c33f47671926144d9c143b554bc71cc/docs/source_zh_cn/base_tutorials/scenes_intro/performance_benchmark.md)、[CLI 参数实现](https://github.com/AISBench/benchmark/blob/e330dc134c33f47671926144d9c143b554bc71cc/ais_bench/benchmark/cli/argument_parser.py)。动态 latest 文档可能变化，工程执行行为以固定提交为准。
