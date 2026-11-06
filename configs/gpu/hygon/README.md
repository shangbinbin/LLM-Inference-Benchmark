# 海光 DCU 适配入口

已根据 2026-11-06 检索到的官方资料增加 [HCU-Exporter v3 参考适配器](hcu-exporter-v3/README.md)。默认推荐从 `configs/environments/hygon-hcu-v3-template/environment.json` 开始，暂不限定卡型号。官方文档、具体下载链接和版本差异见[接入指南](../../../docs/hygon-official-monitoring.md)。配置已按文档核对，但尚未实机验证。

下面的原始空模板保留给其他 Exporter/版本使用，独立于 HCU v3 适配器。

## 后续接入流程

1. 将 `model-template/` 复制为实际型号目录，将 `dcu-exporter-template/` 复制为实际 Exporter/版本目录。型号 profile 的 `adapter` 指向对应适配器；只有接口、标签、单位相同的型号才共享适配器。
2. 在适配器旁保存说明、脱敏 `/metrics` 样本及 Exporter 部署配置；填写 `metric-contract.json` 中的版本、设备标识标签、指标名、单位与换算。该文件是人工适配记录，不会自动生成 recording rules 或转换单位。
3. 修改 `adapter.json` 的 job、scheme、metrics_path。当前生成器只读取这三个抓取字段；若 Exporter 需要认证、TLS 或 relabel，需同时扩展生成器，不能只在 JSON 里增加未被读取的字段。
4. 使用实际样本完善 `grafana/gpu.json`，使查询与 job、设备标签及单位一致；不支持的指标明确注明，不用零值代替缺失数据。需要厂商告警时新增规则并扩展生成器加载逻辑。
5. 复制 `configs/environments/hygon-template/` 为真实环境，填写节点数、每节点卡数、指标入口、模型与并行参数、日志路径；网络按实际部署选择，不默认启用 IB。
6. 补齐占位符、确认 profile 和 adapter 的厂商一致后，修改三处 `status` 并生成配置，按[使用指南](../../../docs/observability.md)验证。记录硬件、驱动、运行时、Exporter、推理引擎版本及测试日期。

`grafana/gpu.json` 当前只展示 Prometheus 自带的 `up`，用于确认 Exporter 连通性；没有伪造海光利用率、显存、温度或功耗指标。`up=1` 不代表设备指标齐全。GPU 性能指标验证完成前，保留模板状态。

仅查看结构：

```bash
python3 scripts/render_observability.py \
  --environment configs/environments/hygon-template/environment.json \
  --output generated/hygon-preview --allow-template
```

不加 `--allow-template` 会拒绝生成。预览含 `REPLACE_` 占位符，不能直接部署。
