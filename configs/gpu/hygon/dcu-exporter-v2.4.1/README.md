# DCU Exporter v2.4.1 默认适配

正式 llm-bench 的 hygon 默认 profile 使用本适配器。镜像为 `harbor.sourcefind.cn:5443/hcu/admin/base/dcu-exporter:v2.4.1`，来自仓库保存的光合社区镜像目录；指标和单位对照官方 DCU Dashboard，非 HCU v3 前缀替换。

- 就绪指标：dcu_memorycap_bytes；job：hygon-dcu-exporter。
- 型号/卡序号：name/minor_number，显式 nodes[].model 优先。
- 物理卡图表不累加重复 Pod 元数据副本，显存比例由 used/capacity 计算。
- metric-contract.json 记录名称、单位、来源与未验证项。
- 项目内部端口预设 16080 可通过 gpu.hygon_exporter_port 覆盖；该预设不等于已验证镜像默认值。
- 不默认给 DCU 注入 HCU v3 专属参数；实际设备初始化、挂载、端口和环境变量需按目标镜像确认。

官方原件在 references/hygon-official/2026-11-06/，保留原始内容；HCU v3 独立参考适配器仍在相邻目录。迁移和镜像导入见 [操作说明](../../../../docs/dcu-exporter-v2.4.1.md)。
