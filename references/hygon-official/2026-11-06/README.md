# 官方资料核对记录：2026-11-06

本次直接读取光合开发者社区的公开文档目录、正文、镜像标签及资源文件列表，并下载配置原件；没有依赖第三方转载确定版本。日期均按官方页面返回值记录，不能将页面访问统计的更新时间当作软件发布日期。

| 对象 | 本次找到的最新内容 | 判断依据 |
| --- | --- | --- |
| 在线监控文档 | HCU-Exporter；2026-10-27 15:53:41 +08:00 更新 | [官方正文](https://developer.sourcefind.cn/document/fea33d51-cc37-11f1-9b14-0242ac150003?id=47c190c3-cc5e-11f1-9b14-0242ac150003) |
| 当前部署 YAML / 离线镜像 | `hcu-exporter.yaml` / `hcu-exporter-v3.0.0.tar` | [官方资源目录](https://download.sourcefind.cn:65024/5/main/Kubernetes%E6%8F%92%E4%BB%B6/dcu-exporter)；YAML 明确引用 `hcu-exporter:v3.0.0` |
| 旧名镜像栏目 | `dcu-exporter:v2.4.2`，PushTime 2026-06-25，dev | [Tag 下载](https://developer.sourcefind.cn/servicelist/detail?active=TagDownload&post_id=77f7eb50-b3c7-11f0-9a0f-acde48001122)；不是最新 HCU v3 的发布证明 |
| 官方 Grafana 配置 | 3 份 JSON，目录标记修改时间 2026-02-12 | [Grafana 目录](https://download.sourcefind.cn:65024/5/main/Kubernetes%E6%8F%92%E4%BB%B6/dcu-exporter/grafana)；实际查询仍为 `dcu_*` / `vdcu_*` |
| 官方 Prometheus 配置 | ConfigMap、Deployment/Service、RBAC；目录标记为 2022-05-18 | [Prometheus 目录](https://download.sourcefind.cn:65024/5/main/Kubernetes%E6%8F%92%E4%BB%B6/prometheus)；Deployment 实际镜像为 `prom/prometheus:v2.0.0` |

“最新”仅指本次在上述公开入口找到的内容；未发现与 HCU v3 同步发布的整套 Prometheus + Grafana 部署包。文档、Exporter、面板和 Prometheus 文件的版本不一致，不能无修改地拼装部署。

## 已保存的官方原件

- [Exporter DaemonSet / Service / RBAC](upstream/exporter/hcu-exporter.yaml)
- [Prometheus ConfigMap](upstream/prom/configmap.yaml)、[Deployment / Service](upstream/prom/prometheus.yaml)、[RBAC](upstream/prom/rbac-setup.yaml)
- [单机 Dashboard](upstream/grafana/dcu-exporter-dashboard.json)、[Kubernetes Dashboard](upstream/grafana/dcu-exporter-k8s-dashboard.json)、[集群 Dashboard](upstream/grafana/dcu-cluster-monitor.json)
- [下载清单和校验值](downloads.json)、[官方文档与镜像版本元数据](sources.json)

`upstream/` 文件按下载字节原样保存，未修正旧配置。Exporter YAML 自带 Hygon 版权及 Apache-2.0 SPDX 标记，附[许可证文本](APACHE-2.0.txt)；其他文件保留原内容，未替来源添加许可声明。修改后的本项目配置放在 `configs/`，不覆盖原件。

## 核对出的使用差异

1. 最新 Exporter 指标前缀为 `hcu_` / `vhcu_`，旧 Dashboard 为 `dcu_` / `vdcu_`。不能仅替换前缀来证明所有指标存在、单位一致。
2. 官方 JSON 中含导入时使用的 `${DS_PROMETHEUS}` / `${DS_PROMETHEUS-DCGM}`，自动 provisioning 前须显式绑定数据源；本项目新面板使用固定 `benchmark-prometheus` UID。
3. 最新 YAML 的 `METRICS_LEVEL=low`、`PULSE=10`；高成本利用率指标另有采样窗口，不能由抓取周期推断指标更新频率。
4. Exporter YAML 使用 `hostNetwork`、privileged、宿主机目录挂载及较宽的 pod RBAC；需在目标集群审阅。Prometheus 原件使用旧镜像、旧 retention 参数和 `emptyDir`，只用于追溯。
5. 原件只放置 annotation 并不会配置任意 Prometheus 实例的自动发现；需明确采用配套发现规则，或本项目生成器的逐节点静态 targets，不能通过一个负载均衡 Service 抓取所有节点。
