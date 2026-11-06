# HCU-Exporter v3 参考适配器

来源：光合开发者社区 HCU-Exporter 在线文档（2026-10-27 更新），资源中心 `hcu-exporter.yaml` 引用 v3.0.0。具体[官方资料及下载入口](../../../../docs/hygon-official-monitoring.md)。

- `adapter.json`：本项目 job `hygon-hcu-exporter`，HTTP `/metrics`；环境清单为每节点指定 `host:16080`。
- `metric-contract.json`：官方文档确认的指标和标签。`hardware_verified: false`；未确认单位保留 null，不做猜测性换算。
- `grafana/gpu.json`：本项目根据文档制作的基础面板，绑定 `benchmark-prometheus`，使用 `hcu_*`。不是下载中心的官方 JSON 原件，也不是对旧 `dcu_*` 面板做字符串替换。

原始 Exporter 部署清单保留在[官方档案](../../../../references/hygon-official/2026-11-06/upstream/exporter/hcu-exporter.yaml)，不会由生成工具自动部署。当前保留官方低成本采集思路，未添加高成本利用率指标或未知阈值告警；仅包含已明确指标名称的基础图表，缺失指标显示无数据。

`model-agnostic-hcu-v3/profile.json` 暂不绑定具体卡型号。使用 `configs/environments/hygon-hcu-v3-template/environment.json` 填写现场信息；参考状态不等于已经验证。原 `dcu-exporter-template/` 仍可用于未来其他版本的适配，不要与本适配器混用。
