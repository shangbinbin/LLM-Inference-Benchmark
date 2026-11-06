# 海光 HCU/DCU + Prometheus + Grafana 官方资料与接入

截至 2026-11-06，本项目采用官方资源中心的 **HCU-Exporter v3.0.0** 配置及 **2026-10-27 更新的 HCU-Exporter 在线文档**作为最新参考，暂不限定卡型号。此选择不是硬件兼容性或实机验证结论。

官方入口存在版本不同步：镜像栏目最高仍列 `dcu-exporter:v2.4.2`，下载中心已有 `hcu-exporter-v3.0.0.tar`；Grafana JSON 仍为旧 `dcu_*` 指标。详情及原件见[核对记录](../references/hygon-official/2026-11-06/README.md)。

## 官方文档与下载入口

- [当前 HCU-Exporter 官方文档](https://developer.sourcefind.cn/document/fea33d51-cc37-11f1-9b14-0242ac150003?id=47c190c3-cc5e-11f1-9b14-0242ac150003)：指标、标签、物理机/容器/Kubernetes 部署和监控集成。
- [官方文档中心](https://developer.sourcefind.cn/document)：导航中的 HCU 集群监控；旧 DCU 文档 UUID 本次返回 record not found，不再作为当前部署入口。
- [Kubernetes 插件资源中心](https://download.sourcefind.cn:65024/5/main/Kubernetes%E6%8F%92%E4%BB%B6)：安装包、YAML、PDF 及监控资源。
- [旧名 dcu-exporter 镜像 Tag 列表](https://developer.sourcefind.cn/servicelist/detail?active=TagDownload&post_id=77f7eb50-b3c7-11f0-9a0f-acde48001122)：旧版回退参考；本次最高 v2.4.2，标记 dev。
- [HCU Kubernetes 组件 PDF](https://download.sourcefind.cn:65024/file/5/Kubernetes%E6%8F%92%E4%BB%B6/HCU_Kubernetes%E7%BB%84%E4%BB%B6.pdf)：目录修改时间 2026-10-12；本次核实目录入口，未下载核读 PDF。

以下 URL 来自官方文件列表的 `DownloadPath`，配置文件已实际下载；二进制和镜像包仅核实目录记录，未执行下载或运行。若直链访问策略变化，可回到资源中心按文件名查找。

| 文件 | 官方目录修改时间 | 获取状态 |
| --- | --- | --- |
| [hcu-exporter](https://download.sourcefind.cn:65024/file/5/Kubernetes%E6%8F%92%E4%BB%B6/dcu-exporter/hcu-exporter) | 2026-09-02 16:48:24 | 仅记录入口，未下载 |
| [hcu-exporter-v3.0.0.tar](https://download.sourcefind.cn:65024/file/5/Kubernetes%E6%8F%92%E4%BB%B6/dcu-exporter/hcu-exporter-v3.0.0.tar) | 2026-09-02 16:57:26 | 仅记录入口，未下载 |
| [hcu-exporter.yaml](https://download.sourcefind.cn:65024/file/5/Kubernetes%E6%8F%92%E4%BB%B6/dcu-exporter/hcu-exporter.yaml) | 2026-09-02 16:53:01 | 已保存原件及 SHA-256 |
| [configmap.yaml](https://download.sourcefind.cn:65024/file/5/Kubernetes%E6%8F%92%E4%BB%B6/prometheus/configmap.yaml) | 2022-05-18 09:21:08 | 已保存原件及 SHA-256 |
| [prometheus.tar](https://download.sourcefind.cn:65024/file/5/Kubernetes%E6%8F%92%E4%BB%B6/prometheus/prometheus.tar) | 2022-05-18 10:06:33 | 仅记录入口，未下载 |
| [prometheus.yaml](https://download.sourcefind.cn:65024/file/5/Kubernetes%E6%8F%92%E4%BB%B6/prometheus/prometheus.yaml) | 2022-05-18 09:20:54 | 已保存原件及 SHA-256 |
| [rbac-setup.yaml](https://download.sourcefind.cn:65024/file/5/Kubernetes%E6%8F%92%E4%BB%B6/prometheus/rbac-setup.yaml) | 2022-05-18 09:21:01 | 已保存原件及 SHA-256 |
| [dcu-cluster-monitor.json](https://download.sourcefind.cn:65024/file/5/Kubernetes%E6%8F%92%E4%BB%B6/dcu-exporter/grafana/dcu-cluster-monitor.json) | 2026-02-12 17:37:58 | 已保存原件及 SHA-256 |
| [dcu-exporter-dashboard.json](https://download.sourcefind.cn:65024/file/5/Kubernetes%E6%8F%92%E4%BB%B6/dcu-exporter/grafana/dcu-exporter-dashboard.json) | 2026-02-12 17:37:58 | 已保存原件及 SHA-256 |
| [dcu-exporter-k8s-dashboard.json](https://download.sourcefind.cn:65024/file/5/Kubernetes%E6%8F%92%E4%BB%B6/dcu-exporter/grafana/dcu-exporter-k8s-dashboard.json) | 2026-02-12 17:37:58 | 已保存原件及 SHA-256 |

官方 YAML 指定的镜像地址为：

```bash
docker pull harbor.sourcefind.cn:5443/hcu/admin/base/hcu-exporter:v3.0.0
```

这是已核对 YAML 的镜像引用，未实测 registry 拉取或 digest。在线正文的 Docker 示例另写 `image.sourcefind.cn:5000/hcu/admin/base/hcu-exporter:{release_version}`；本项目不假定两个 registry 内容相同。离线包可在目标服务器 `docker load -i hcu-exporter-v3.0.0.tar` 后核对实际导入的标签，再调整清单。

Prometheus 和 Grafana 自身的当前发行版分别从 [Prometheus 官方下载](https://prometheus.io/download/) 和 [Grafana 官方下载](https://grafana.com/grafana/download) 获取。光合目录的 Prometheus 旧示例不是这两个项目的最新版本；本次没有找到光合提供的最新 Grafana 服务部署 YAML 或镜像包，不虚构一份“官方完整安装包”。

## 工程内的配置选择

| 内容 | 位置 | 状态 |
| --- | --- | --- |
| 官方原件、版本、下载校验 | [来源档案](../references/hygon-official/2026-11-06/README.md) | 已下载并核对结构，未部署 |
| HCU v3 指标采集和项目自制面板 | [hcu-exporter-v3](../configs/gpu/hygon/hcu-exporter-v3/README.md) | 基于官方文档，非官方面板原件 |
| 不限定卡型号的 profile | [profile.json](../configs/gpu/hygon/model-agnostic-hcu-v3/profile.json) | 型号为 unspecified，不代表全型号适配 |
| 待填现场地址的环境 | [environment.json](../configs/environments/hygon-hcu-v3-template/environment.json) | template，需要补齐地址、模型、卡数及日志路径 |

## 接入顺序

1. 在目标主机先安装匹配硬件的驱动。审阅[官方 Exporter YAML](../references/hygon-official/2026-11-06/upstream/exporter/hcu-exporter.yaml)中的 `hygon.com/hcu: "true"` 节点选择器、目录挂载和权限，再按目标集群部署。`/opt/hyhal` 必须来自驱动环境；旧节点若只有 `hygon.com/dcu` 标签，不能期望自动匹配。
2. 每个节点分别访问 `http://<node-ip>:16080/metrics`，确认实际输出 `hcu_*`，记录 Exporter、驱动版本和样本；只看到 `dcu_*` 时应选旧版适配方案，不能套用 HCU v3 面板。
3. 复制 HCU v3 环境模板，填写每个节点独立的 `gpu_target`（默认 16080）。将 `status` 从 template 改为 reference，补齐所有 `REPLACE_` 字段后再正常生成。节点数量和卡数不从型号推断。
4. 使用生成结果部署 Prometheus、Grafana 及需要的日志组件；挂载与启动方式沿用[观测链路指南](observability.md)。此生成器不执行 Exporter 安装或 Kubernetes apply。选定组件版本后进行对应版本的语法验证。
5. 在 Grafana 查看 HCU v3 基础面板：UP、计算单元瞬时利用率、显存使用量/总量/百分比、显存温度和原始功耗值。未确认的功耗、利用率单位不强行标为 W 或 %；详见指标契约。

仅预览：

```bash
python3 scripts/render_observability.py \
  --environment configs/environments/hygon-hcu-v3-template/environment.json \
  --output generated/hygon-hcu-v3-preview --allow-template
```

对已填写的环境执行相同命令时，去掉 `--allow-template` 并使用新输出目录。生成器将 GPU job 与本项目统一的 SGLang、日志链路组合；不把来源档案中的旧 Prometheus 镜像和旧 Dashboard 混入结果。

## 压测时核对

官方 YAML 的 `PULSE=10` 表示采集更新周期为 10 秒；本项目 Prometheus 默认 5 秒抓取并不意味着每 5 秒都有新硬件样本。需要更细粒度时同步评估采集成本，再调整 PULSE 和抓取周期。高成本利用率/采样窗口指标不在本次基础面板中。

保持 `METRICS_DEFINE` 和 `LABEL_DEFINE` 为空，否则指标名或标签重命名会导致面板查询失效。`node` 抓取标签以环境清单为准，Exporter 同名标签可能变成 `exported_node`；设备查询使用 `device_id` 和 `minor_number`。

验证每个节点 UP、设备数量、显存单位、设备 ID 和实际读数；缺失指标应显示无数据，不能用零值掩盖。若同一设备按多个 Pod 输出多条序列，不应直接按节点求和；当前基础面板保留原生序列，未提供跨 Pod 去重的计费/总量统计。新配置仍需在真实 HCU/DCU 环境验证。
