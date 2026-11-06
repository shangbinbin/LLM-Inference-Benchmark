# Mac SLO 自适应搜索验收（2026-11-07）

北京时间 2026-11-07，Apple Silicon Mac + Docker Desktop，容器为 linux/arm64。使用实际 AISBench 镜像 `e330dc1-arm64`，源码固定 `e330dc134c33f47671926144d9c143b554bc71cc`。Linux 生产配置仍固定 linux/amd64，本次没有进行真实 NVIDIA/海光硬件容量验收。

## 完整搜索

命令：

```bash
python3 scripts/benchmark_stack.py up --config configs/stacks/mac-slo-search.json --no-benchmark
python3 scripts/slo_search.py --config configs/stacks/mac-slo-search.json --search configs/search/mac-slo.json
```

独立模拟栈包括真实 Prometheus、Grafana、Loki、Alloy、logrotate、node_exporter，以及测试推理端点。端点在活动请求超过 2 时延迟响应 400 ms；AISBench 测量实际 HTTP/SSE 时延并生成报告，搜索器没有读取模拟 capacity。

策略：并发 1–6，步长 1，每个候选重复 2 次，每次 12 个正式请求、1 次预热，输入/输出 32/16。SLO 为 P99 TTFT ≤ 200 ms、TPOT ≤ 50 ms、E2EL ≤ 1000 ms。

| 测试顺序 | 并发 | 两次 P99 TTFT（ms） | 候选结果 |
| --- | --- | --- | --- |
| 1 | 1 | 12.4 / 11.4 | PASS |
| 2 | 6 | 413.2 / 413.8 | SLO_FAIL |
| 3 | 3 | 414.9 / 415.1 | SLO_FAIL |
| 4 | 2 | 11.4 / 14.0 | PASS |
| 5 | 5 | 413.7 / 413.7 | SLO_FAIL |
| 6 | 4 | 413.8 / 412.7 | SLO_FAIL |

共实际执行 12 次 AISBench，144 个正式请求，所有请求成功；搜索耗时约 118.5 秒（含初始化、预热、结果汇总和冷却）。最终 status=complete、stop_reason=boundary_found、recommended_concurrency=2、maximum_verified=true。

这里验证的是模拟曲线上的自动决策和流程，不是显卡吞吐/容量。完整时延、阶段、报告相对路径与重复结果见 [机器可读快照](mac-slo-search-2026-11-07.json)。原始配置、客户端日志、AISBench 报告保存在 `generated/mac-slo/data/results/slo-search/`，不提交大体积运行数据。

## 停止与互斥实测

| 场景 | 观察到的结果 |
| --- | --- |
| max_rounds=1、repeats=1 | round_budget；记录 best_observed=1，但 recommended=null |
| trial_timeout_seconds=0.1 | trial_timeout；客户端未完成，不给推荐 |
| max_duration_seconds=0.001 | time_budget；初始化阶段停止，不发起有效压测 |
| 范围 1–2、各重复 1 次 | upper_bound_passed，推荐 2，仅对配置范围成立 |
| 范围 1–2、TTFT 阈值 0.001 ms | no_feasible_concurrency，推荐 null |
| 在 AISBench 容器运行时启动第二个搜索 | 第二个搜索被锁拒绝，原锁内容保持不变 |
| 对运行中的搜索发送 SIGTERM | interrupted，推荐 null |

上述运行结束后均检查 `suite.lock` 已释放，`llm-slo-mac-search-client` 与 `llm-slo-mac-search-probe` 已不存在。观测栈继续运行，证据文件保留。

## 自动测试

`python3 -m unittest discover -s tests -v` 共 28 项通过。新增覆盖包括二分与高档验证、重复测试全部通过规则、预算、超时、中断、无可行点、单位/样本/输出长度校验、错属容器拒绝清理及锁互斥。其中对 6 个候选点的全部 64 种通过/失败组合逐一验证，默认 verified_max 均找到网格内最大通过点（包括非单调组合）。

操作与边界见 [SLO 自适应搜索手册](../aisbench-auto-slo.md)。
