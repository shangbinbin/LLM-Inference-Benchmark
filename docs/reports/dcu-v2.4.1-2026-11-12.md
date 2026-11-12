# DCU Exporter v2.4.1 适配验证（2026-11-12）

当前正式 hygon 默认镜像改为 `harbor.sourcefind.cn:5443/hcu/admin/base/dcu-exporter:v2.4.1`。该引用来自仓库保存的光合社区官方镜像目录；指标名称和单位来自保存的官方 DCU Dashboard，卡型号标签 name、卡序号 minor_number 由官方面板确认。本次访问 developer.sourcefind.cn 与 download.sourcefind.cn 被环境代理以 403 拒绝，未刷新官网或拉取该设备镜像；没有真实海光硬件。

## 软件验证

| 验证 | 结果 |
| --- | --- |
| 标准单元测试 | 143 项通过，含 NVIDIA 原有路径回归、Docker/nerdctl 参数生成、DCU 镜像/标签/端口/就绪检查、快照一致性 |
| 默认离线镜像计划 | 9 个镜像，海光引用为官方 dcu-exporter:v2.4.1，NVIDIA 镜像保持原值 |
| 完整 CPU 模拟链路 | 使用缓存 toolbox/AISBench/Prometheus/Grafana/Loki/Alloy/node_exporter，未构建或拉取镜像；AISBench 8/8 成功、0 失败 |
| DCU fixture | 从当前源码只读挂载 mock_server.py，缓存 toolbox 可产生 dcu_*，无 hcu_memorycap_bytes |
| Prometheus 标签 | task、gpu_node、gpu_model、gpu_index 非空，保留 name/minor_number/device_id 原生标签 |
| DCU 面板 PromQL | 11 条查询均返回数据，1 GiB / 8 GiB 的显存比例为 12.5% |
| 物理卡副本回归 | promtool 对同一张卡的两份 Pod 元数据样本计算得到卡数 1、显存比例 12.5%，不重复累计 |
| Grafana API | 预置任务总览包含 dcu_*，不包含 hcu_*，数据源正确 |
| Grafana 浏览器 | Chromium/Playwright 加载并滚动到 DCU 区域，单卡图例、时钟/PCIe/错误计数/卡数与 Logs Volume 可见，无数据源查询 HTTP 错误 |
| 配置手册脚本 | NVIDIA/海光 × Docker/nerdctl 四种组合及三测试例 plan 均通过 |
| 文档与语法 | shell/Python 代码块、本地链接及 JSON 解析校验通过 |
| 清理 | 临时栈容器和网络均正常 down，持久测试结果保留在 generated/ |

本地容器测试对内部服务名增加 NO_PROXY，不修改全局 Docker 代理；query、Loki 和 Grafana 都在测试栈内部通信。模拟图表和指标不代表真实设备性能。

## 仍需现场确认

未验证真实 v2.4.1 镜像拉取/digest、驱动初始化、实际端口和环境开关，以及该镜像逐项实际暴露的指标。16080 仅是项目沿用的预设，新增 gpu.hygon_exporter_port 用于匹配实际容器端口；不会自动修改二进制监听。HCU v3 的开关不再自动注入，按已确认支持的参数填写 gpu.hygon_exporter_env；已有 Exporter 可优先 external 连接实际 target。

HCU v3 的独立参考目录和历史报告没有重写。当前 NVIDIA 指标与面板维持原行为；本次真实容器集成使用 Docker，nerdctl 以命令/配置回归验证，不宣称完成生产节点 GPU/容器运行时验收。迁移与镜像来源见 [DCU 使用说明](../dcu-exporter-v2.4.1.md)。
