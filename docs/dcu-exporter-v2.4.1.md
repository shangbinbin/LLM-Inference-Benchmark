# 海光切换至光合社区 DCU Exporter v2.4.1

正式 llm-bench 的海光默认 Exporter 已从 HCU v3 切换为 **DCU Exporter v2.4.1**。vendor 仍填写 hygon，镜像键仍是 images.hygon_exporter；NVIDIA 配置保持原行为。HCU v3 的独立参考适配器和历史来源保留，不与当前 dcu_* 配置混用。

## 1. 官方镜像与依据

光合社区镜像栏目：<https://developer.sourcefind.cn/servicelist/detail?active=TagDownload&post_id=77f7eb50-b3c7-11f0-9a0f-acde48001122>

仓库保存的 [官方目录记录](../references/hygon-official/2026-11-06/sources.json)明确包含 v2.4.1（PushTime 2026-04-02 10:54:38，目录标记 dev），对应完整引用为：

```text
harbor.sourcefind.cn:5443/hcu/admin/base/dcu-exporter:v2.4.1
```

```bash
# 在线机按实际仓库权限准备，不在离线机执行 pull
docker pull --platform linux/amd64 \
  harbor.sourcefind.cn:5443/hcu/admin/base/dcu-exporter:v2.4.1
```

本次访问光合 developer/download 域名被环境代理以 403 拒绝，未实时刷新目录、未拉取该镜像或验证其 digest。上述引用与指标依据来自已保存的官方材料，不宣称本次验证了镜像可下载、实际监听端口、运行参数或海光驱动兼容性。私有镜像重标记时改为实际完整引用，并核对内容确为该版本。

## 2. 检测、标签与 Grafana 改动

| 项目 | 当前 DCU v2.4.1 适配 |
| --- | --- |
| 默认 profile / adapter | gpu/hygon/model-agnostic-dcu-v2.4.1 → dcu-exporter-v2.4.1 |
| Prometheus job | hygon-dcu-exporter |
| local / external 就绪检测 | /metrics 中出现 dcu_memorycap_bytes，不再检查 hcu_memorycap_bytes |
| GPU 任务/节点 | task / gpu_node，与 NVIDIA 相同的联动方式 |
| GPU 型号 | nodes[].model 显式值优先，否则原生 name；都没有时 unspecified |
| GPU 卡序号 | 原生 minor_number → gpu_index，可用 gpu.index_label 覆盖 |
| 显存比例 | 100 × dcu_usedmemory_bytes / dcu_memorycap_bytes，不使用 HCU 的 hcu_vram_percent |
| 温度 | dcu_temp（设备温度），不使用 HCU 的 hcu_temp_mem（显存温度） |
| 物理卡去重 | 按 task、gpu_node、gpu_model、gpu_index 取 max，避免把 Pod 元数据副本相加；不混入 vdcu_* |

其他面板包括 dcu_utilizationrate（%）、显存用量/容量（bytes）、dcu_power_usage（W）、dcu_pciebw_mb（MiB/s）、dcu_sclk（MHz）、dcu_ce_count/dcu_ue_count 原始计数及可见卡数。名称、单位依据 [官方 DCU Dashboard 原件](../references/hygon-official/2026-11-06/upstream/grafana/dcu-exporter-dashboard.json)；真实镜像逐项是否暴露仍需现场 /metrics 确认，缺失指标不伪造为零。

统一任务 Dashboard 仍包含 vLLM、SGLang、Logs Volume 和日志。两份预生成 NVIDIA/海光快照随代码同步，正式启动按厂商只挂载一份。新 adapter 的 [指标契约](../configs/gpu/hygon/dcu-exporter-v2.4.1/metric-contract.json)与[独立面板](../configs/gpu/hygon/dcu-exporter-v2.4.1/grafana/gpu.json)供核对和独立渲染入口使用。

## 3. 现有 site.json 如何迁移

**现场生成的 JSON 不会随仓库模板自动更新。** 先用旧配置 down 原项目栈，再同步当前完整 scripts/、configs/、containers/ 和 bin/ 源码，按下面方式修改。停止前可先 logs collect；不要改变 project/storage.root 后再尝试定位旧栈。

本机新建 Exporter 的字段示例（合并到现有完整配置，不替换整个 site）：

```json
{
  "images": {
    "hygon_exporter": "harbor.sourcefind.cn:5443/hcu/admin/base/dcu-exporter:v2.4.1"
  },
  "gpu": {
    "vendor": "hygon",
    "deployment": "local",
    "nodes": [{"name": "dcu-node", "gpus": 8}],
    "hygon_exporter_port": 16080,
    "hygon_exporter_env": {}
  }
}
```

**16080 是项目沿用的可配置预设，不是本次已验证的 v2.4.1 镜像默认端口。** 本机模式必须将 gpu.hygon_exporter_port 改为容器实际监听端口；该字段同时控制 Prometheus 内部 target、就绪 URL 和 ports.gpu-exporter 到容器的映射。只改此字段不会自动改变二进制监听；若要设置二进制端口，按该镜像实际支持的选项配置进程。gpu.hygon_exporter_env 是可选字符串键值对象，可传递已经确认支持的环境变量，默认不猜测或注入 HCU v3 的 CONNECT_K8S/METRICS_LEVEL 等开关。

项目继续沿用 privileged 及 /dev、/opt/hyhal、/etc/hostname、/etc/vdev 挂载；这些路径及驱动是否满足 v2.4.1 需要现场验证。实际已部署的 DCU Exporter 可优先复用，避免重新初始化设备：

```json
{
  "gpu": {
    "vendor": "hygon",
    "deployment": "external",
    "nodes": [{"name": "dcu-node", "gpus": 8, "target": "192.0.2.10:8000"}]
  }
}
```

以上 192.0.2.10:8000 是待替换的示例。external 不创建本机设备采集容器，直接访问实际 target 的 /metrics，并检查 DCU 指标。它不读取 hygon_exporter_port，也不会给原 Exporter 改端口；用户原有 Kubernetes/nerdctl 容器继续由原部署方管理。

运行时仍可选 `["docker"]` 或 `["nerdctl","--namespace","benchmark"]`。nerdctl 镜像导入须与配置同 namespace；已有 k8s.io 镜像不会自动在 benchmark 可见。容器公开端口默认仍为 39400，Grafana 为 33000，与 GPU 厂商选择无关。

## 4. 准备镜像与重新启动

在线镜像交付脚本默认仍是 NVIDIA DCGM + 海光 DCU 两类，默认共 9 个镜像。先更新最终交付 site 的 images.hygon_exporter，再查看并执行：

```bash
python3 scripts/prepare_offline_images.py \
  --site generated/offline-bundle/site/site.json --plan
python3 scripts/prepare_offline_images.py \
  --site generated/offline-bundle/site/site.json
```

仅海光交付可加 --exporters hygon。离线导入新的归档；已有 HCU v3 镜像不能通过改标签冒充 DCU v2.4.1，不需要删除旧镜像。已有实际 DCU 镜像可以直接使用，但需在所选运行时/namespace 中核对标签和架构。

以下在离线源码根目录执行；现场服务、tokenizer、真实日志和 Token 仍按原业务配置保留：

```bash
# 停止原栈应在修改 project/root 之前完成；若尚未运行则跳过
./bin/llm-bench down --config generated/site/site.json
# 此时更新源码、导入镜像并编辑 site.json；需要鉴权时在当前终端导出 Token
./bin/llm-bench plan --config generated/site/site.json
./bin/llm-bench images check --config generated/site/site.json
./bin/llm-bench up --config generated/site/site.json --no-benchmark
./bin/llm-bench bench run --config generated/site/site.json --requests 8 --concurrency 2
./bin/llm-bench logs check --config generated/site/site.json
```

NVIDIA 的 DCGM 镜像、采集和面板未改变。AISBench 无需因本次指标适配重建；真实生产 toolbox 探测复用原 Python 镜像。模拟 fixture 只读挂载当前 containers/toolbox/mock_server.py，使已缓存 toolbox 也能输出新的 dcu_* 测试指标；软件模拟数值不代表真实设备数据。

Grafana 中选择现场 task，再选 GPU 节点、型号、卡序号。若只有 HCU 旧面板/旧 job，确认已同步配置快照、完整源码并重新 up；若 /metrics 仍只有 hcu_*，确认实际运行镜像和指标版本。修改 site 后直接 bench 不会热切换原 manifest。

软件测试证据和未覆盖项见 [验证记录](reports/dcu-v2.4.1-2026-11-12.md)。
