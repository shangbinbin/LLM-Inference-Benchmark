# Mac 容器链路实测（2026-11-07）

时间按北京时间记录，容器内 AISBench 文件名使用 UTC（2026-11-06 16:xx）。环境为 Apple Silicon macOS、Docker Desktop Engine 29.6.2、Linux arm64 容器。Linux GPU 生产配置明确为 **linux/amd64（x86）**，并使用独立 amd64 客户端/辅助镜像标签。

## 实际运行范围

Prometheus 3.5.0、Grafana 12.0.2、Loki 3.5.0、Alloy 1.8.3、node_exporter 1.9.1、logrotate 和 AISBench 均实际运行在容器中。AISBench 源码固定 `e330dc134c33f47671926144d9c143b554bc71cc`，Python 3.11、CPU Torch 2.6.0；核心 tokenizer 依赖固定为 Transformers 5.22.2 / tokenizers 0.23.3。

只有推理服务响应和 GPU/DCU 指标来自仓库测试 fixture。没有调用或部署真实推理模型，没有模拟 AISBench 客户端本身。模拟数值不代表任何显卡性能。

## 验收结果

| 项目 | 结果 |
| --- | --- |
| 配置文件一键启动 | 按依赖顺序启动 7 个常驻组件，再运行 AISBench 客户端 |
| Prometheus | 推理指标、设备指标、node_exporter 三个目标全部 UP |
| NVIDIA 模拟 | 存在 DCGM 指标，不存在 HCU 指标；加载 NVIDIA GPU 面板 |
| 海光模拟 | 存在 HCU 指标，不存在 DCGM 指标；加载海光 HCU 面板 |
| Grafana | 健康接口成功，Prometheus/Loki 数据源与应用/GPU 面板正常 provisioning |
| Loki | 可查询实际 AISBench 请求触发的 JSON 日志 |
| logrotate | 强制轮转生成 gzip 文件，活动文件继续写入 |
| 持久化 | 重启 Alloy/Prometheus/Loki/Grafana 后，旧测试日志仍可查询，新日志继续入库，设备指标仍可查询 |
| AISBench 单档 | 8/8 正式请求成功、0 失败，输入总量 256、输出总量 128（每请求 32/16）；另有 1 次预热 |
| AISBench 原生顺序扫描 | 并发 1 / 2，正式请求 4 / 8，全部成功；生成 combos_manifest、批量 CSV 和 HTML |
| AISBench 自定义顺序清单 | string、tokenid、持续压力三例依次 PASS；记录独立起止时间与结果，无重叠 |
| 架构及失败防护 | 单元测试覆盖错用 arm64 生产镜像、非法扫描参数、就绪失败禁止发压及拒绝删除无 ownership 的容器 |
| x86 镜像清单 | 实际查询 Python 基础镜像、Prometheus、Grafana、Loki、Alloy、node_exporter、NVIDIA DCGM Exporter 的 registry manifest，均包含 linux/amd64 |

实际定位并修复了 Alloy v1.8 不支持嵌入 file_match 块、Loki WAL/compactor 目录权限、AISBench retry=0 不发请求、合成性能汇总必须加载 tokenizer 等问题。不能用只渲染配置或 Docker 容器已创建来代替这些端到端验证。

## 复现

```bash
python3 scripts/benchmark_stack.py build --config configs/stacks/mac-simulation.json
python3 scripts/benchmark_stack.py up --config configs/stacks/mac-simulation.json
python3 scripts/verify_simulation.py --config configs/stacks/mac-simulation.json --exercise-restart
python3 scripts/run_aisbench_suite.py --config configs/stacks/mac-simulation.json --suite configs/suites/mac-smoke.json
python3 -m unittest discover -s tests -v
```

已有栈时先使用 down；持久化文件不会删除。海光切换验证使用同一模拟配置，改 project、storage 路径和 ports，并设置 gpu.vendor=hygon；原生扫描设置 concurrency_levels=[1,2]、requests_per_level=[4,8]。详细步骤见 [顺序压测指南](../aisbench-sequential.md)。

本机原始证据在 `generated/mac-smoke/verification.json`、`generated/hygon-sweep-smoke/verification.json`、各自 data/results 和 suite-results.json；精选机器可读结果见 [验收快照](mac-container-smoke-2026-11-07.json)。大体积运行数据不提交 Git。

## 尚需目标环境验证

- Linux x86 的真实 NVIDIA/Hygon Exporter、驱动/设备权限、实际指标值和 tokenizer。
- Linux amd64 镜像在目标服务器上的运行；Mac arm64 实测不能代替 x86 实机验收。
- 裸 containerd/nerdctl、高版本 Kubernetes 的调度、设备插件和存储接入。
- 多机/多 P 多 D 的真实服务拓扑、角色统计口径、跨节点日志和可选 IB 网络。

这些项目已有配置/接口或操作说明，但未将模拟结果标记为硬件部署验证。
