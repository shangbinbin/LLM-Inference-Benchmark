# InfiniBand 网络适配

与 GPU 厂商无关，只有环境清单选择此适配器时才生成 job、告警和面板。

来源基于 node_exporter 0.17.0 的 `infiniband` collector。保留来源的采集白名单和指标口径；不同版本需用实际 `/metrics?collect[]=infiniband` 样本核对。不能将此配置当成 RoCE 或任意网卡的通用配置。

已删除来源中的“每节点必须有 10 个 IB device”告警；这是现场设备基线，不能通用于其他环境。剩余告警主要覆盖抓取状态、collector 健康、指标缺失和链路事件。当前只加载规则，未提供 Alertmanager 通知配置。

DNS 发现范围应限定为本次推理节点；若现有 headless Service 覆盖整个集群，需改为专用 Service 或显式 `targets`，避免把其他节点算入本次观察范围。
