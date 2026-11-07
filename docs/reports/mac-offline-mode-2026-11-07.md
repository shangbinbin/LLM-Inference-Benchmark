# 正式离线模式 Mac 验收（2026-11-07）

本次验证正式 `offline: true` 行为，使用 Apple Silicon Mac / Docker Desktop、linux/arm64 镜像和本地模拟 tokenizer。Linux 生产配置仍限制为 linux/amd64，未验收真实 GPU/DCU 或裸 containerd。

## 复跑

先准备 `configs/stacks/mac-offline.json` 选用的七个 arm64 镜像，再在仓库根目录执行：

```bash
python3 docs/examples/verify_offline_mac.py
```

脚本使用正式的配置加载、镜像预检查、Stack.up、顺序用例和 SLO 搜索代码；仅在测试运行时给网络创建增加 `--internal`，以保留栈内通信并隔绝外部连接。测试会检查正式代码是否已经给每个 run/create 加上 `--pull=never`，**不再代替正式流程补这个参数**。如代码尝试 build/pull/源码下载，会被测试保护拦下并判为失败。

每个场景独立生成配置/状态/日志，缺镜像使用不存在的随机标签，不删除任何现有镜像。结束后清理本次容器和网络，保留运行证据；已有两套模拟栈未受影响。测试没有关闭 Mac 或 Docker daemon 全局网络，生产模式仍需现场网络策略控制公网访问。

## 缺镜像与正式 CLI

| 场景 | 结果 |
| --- | --- |
| 缺 toolbox | 预检查列出标签及 fixture/logrotate 用途，不尝试构建 |
| 缺 AISBench | 预检查列出标签，不下载源码；同配置的仅观测镜像检查通过 |
| 缺 node_exporter | 预检查列出公共镜像，不尝试运行或拉取 |
| 同时缺 toolbox、Grafana、AISBench | 一次列出三个缺失项，没有新建 state.json、容器或网络 |
| 显式 build / build_one | 直接拒绝，既不执行 Docker build，也不下载源码 |
| 所选镜像齐全 | check-images 成功，报告七个 linux/arm64 镜像 |

此外，未通过测试拦截器的正式命令行也已执行：`up --config <缺三项的配置>` 返回 1 并列全三项；`build` 返回 1；`check-images --config configs/stacks/mac-offline.json` 返回 0。

## 完整链路与客户端

| 负载 | 结果 |
| --- | --- |
| NVIDIA 模拟，单档压测 | 8 个正式请求成功，0 失败；`DCGM_FI_DEV_GPU_UTIL` 可查 |
| 海光模拟，单档压测 | 8 个正式请求成功，0 失败；`hcu_utilizationrate` 可查 |
| 顺序用例：string / tokenid | 两条用例各 4 个正式请求，均通过 |
| SLO 搜索：并发 1–2，各重复 1 次 | 两轮各 8 个正式请求，返回推荐并发 2；这是限定范围内的模拟搜索结果 |

两种厂商配置下，Prometheus 三个 targets 均 UP、Grafana health 正常，Loki 均能查询到单档压测对应的 9 条日志（含 1 次预热）。所有业务容器只接入测试内部网络；对 `1.1.1.1:443`、`8.8.8.8:443` 的探测均返回 Network is unreachable。

审计记录中的 **42 次 run/create 均已由正式代码带上 --pull=never**，覆盖观测组件、就绪探测、单档 AISBench、顺序用例与 SLO 搜索客户端/探测。没有 build、pull 或源码下载尝试。

## 外联设置

通过 NVIDIA 模拟栈 Grafana `/api/admin/settings` 读取生效值，确认统计、主程序更新、插件更新和新闻源均为 false；插件预安装及签名公钥下载的禁用开关为 true，在线插件管理为 false。Alloy 容器实际启动参数包含 `--disable-reporting`；Loki 保持 reporting_enabled=false。

本次 Grafana/Alloy/Loki 运行日志中，没有此前出现的更新检查、插件安装、Angular 规则更新、公钥下载失败或已知公网请求记录。日志检查覆盖本次运行窗口，结合固定版本设置验证，不代表对未来版本、自定义插件及长期所有网络流量的证明；生产环境仍需内网访问控制。

## 自动测试与证据

`python3 -m unittest discover -s tests -v`：34 项通过。新增覆盖缺失项聚合、架构错误/运行时故障区分、厂商与 external/仅观测选择、构建前禁止下载、离线配置及 nerdctl namespace 参数保持、在线默认行为和布尔类型校验。

运行 ID：`ac95ea40`。镜像 ID、各场景结果、正式 CLI 返回值、生效设置和命令审计摘要见 [JSON 快照](mac-offline-mode-2026-11-07.json)。本地原始证据保存在 `generated/offline-validation/ac95ea40/`，控制台日志为 `generated/offline-mode-validation-console.log`。

操作方法见 [离线部署手册](../offline-deployment.md)；正式模式接入前的对照结果见 [此前模拟报告](mac-offline-validation-2026-11-07.md)。
