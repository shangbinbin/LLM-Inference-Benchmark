# 参考资料

这里保存搭建压测全链路时参考的已有配置和测试场景，按来源及用途分开管理。

| 目录 | 来源与用途 | 阅读入口 |
| --- | --- | --- |
| `sglang-inference-platform/` | 从同名项目复制的监控、日志、Dashboard 和镜像构建参考 | [组件索引与来源版本](sglang-inference-platform/README.md) |
| `benchmark-scenarios/` | 其他项目的推理压测场景、测试项和要求整理 | [场景索引](benchmark-scenarios/README.md) |

新增参考材料时说明来源、版本或整理日期、适用环境，以及尚未核实的内容。正式采用后，将适配配置放入 [configs](../configs/README.md)，将本项目方案写入 `docs/`。

来源项目的组件目录保持在同一层级，便于对照上游，并保留 Prometheus、Grafana、GPU/IB 监控说明之间的相对引用。各组件内部的配置、Pod 示例、Dockerfile 和说明放在一起。
