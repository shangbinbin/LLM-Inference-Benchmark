# nerdctl 1.7.7 兼容验证（2026-11-12）

本次使用真实 nerdctl 1.7.7，从提交 `8099c15` 开始验证 llm-bench。启动、发压和观测通过，但发现旧清理逻辑会误判 nerdctl 1.7.7 的强制删除结果；已补充清理修复及回归验证。没有用 Docker 命令伪造 nerdctl 检查结果。完整生命周期应使用包含本次修复的源码，不能仅凭原提交的启动成功就认定清理也成功。

## 测试环境

| 组件 | 版本 / 设置 |
| --- | --- |
| nerdctl | 1.7.7，commit 5882c720f4e7f358fb26b759e514b3ae9dd8ea83 |
| containerd | 1.7.22（nerdctl full 1.7.7 发行包自带） |
| CNI | 1.5.1（同一发行包自带） |
| 平台 | Linux amd64 |
| namespace | bench-177，与既有项目隔离 |
| OCI 后端 | runsc release-20260413.0，systrap / ignore-cgroups，仅用于云端验证 |
| 业务镜像 | 已缓存的 toolbox、AISBench、Prometheus、Grafana、Loki、Alloy、node_exporter，共七个 |

官方 full 发行包 SHA256 已核对：`a731eac93e8e9dda1a0d76dc1606438deb0668ea7d6bd5c5af436353ed9f65c5`。镜像从 Docker 归档导入真实 containerd，未拉取或重建业务镜像。测试启动独立 containerd，不重启或修改节点原有运行时。

云环境无法创建 runc 所需子 cgroup，沿用此前 2.1.3 验证使用的 runsc；仅在测试 CNI 网络增加 loopback 插件，使沙箱内 Loki 的本地 gRPC 可用。包装器只负责将命令转发到独立测试容器中的真实 nerdctl，以及上述沙箱适配。生产配置不包含这些测试改动。

## 1.7.7 的实际差异

- 已验证 `inspect --mode=dockercompat`、镜像 `--platform`、run/create 的 `--pull=never` / `--log-driver=json-file`、hostname 和端口映射等项目所需参数可用。
- 部分 inspect 字段不完整。例如本次 fixture 的 Config.Entrypoint / Config.Cmd 为 null，而 State.Pid 有效。查看实际启动命令可使用 `.Path` 和 `.Args`；不应将空字段解释为容器未运行。
- 在同一 containerd 下，1.7.7 的 CNI 网络名可跨 namespace 可见；image namespace 仍隔离。应让不同项目使用节点上唯一的 project 名称。启动器已有网络存在即拒绝启动、ownership label 和运行时/namespace 清理保护，不会因发现同名网络而自动接管。

## 清理问题及修复

nerdctl 1.7.7 的 [Remove 实现](https://github.com/containerd/nerdctl/blob/v1.7.7/pkg/cmd/container/remove.go)在 force 模式遇到错误时记录日志并返回 nil。本次 runsc 测试触发 task 删除失败，`rm -f` 仍返回 0，原 down 删除了状态但实际还残留 7 个停止容器，后续复用端口也受旧 CNI 转发规则影响。

修复为先停止运行中的容器，再使用不带 force 的 rm，最后 inspect 确认容器确实不存在。失败时保留状态/锁，继续遵守项目 ownership 校验。同一逻辑用于 down 和 SLO 临时 probe/client 清理。增加成功退出码却仍有容器、普通删除报错、停止/删除调用顺序和 SLO 清理回归测试。

为复验，在独立测试环境恢复了事先保存的 NVIDIA 模拟项目状态并删除残留容器，随后移除仅属于该旧测试项目的 DNAT 跳转规则，再验证海光模拟链路。未清空节点或其他项目的防火墙规则；这些旧测试残留的处置没有写入生产启动器。

## 通过的检查

| 检查 | 结果 |
| --- | --- |
| 版本与镜像 | 真实 1.7.7 / 1.7.22，七个已有 amd64 镜像从归档导入，不构建、不拉取 |
| 单元及回归测试 | 114 项通过，包含清理误判的故障注入 |
| NVIDIA / 海光模拟 up | 两套均完成 8 次正式请求，0 失败 |
| 多数据集 | JSONL 2 请求、ShareGPT 3 请求、random tokenid 4 请求全部通过 |
| SLO | 两种模拟厂商各完成一次 8 请求的并发 1 测量；海光一轮使用修复后的临时容器清理逻辑，正常释放锁 |
| Prometheus / Loki | Targets UP、厂商对应指标及 task/gpu_node/gpu_model/gpu_index、推理日志均通过验证 |
| Grafana 浏览器 | NVIDIA 和海光总览及 Logs Volume/日志查询无页面或数据源错误 |
| 重启与轮转 | NVIDIA 模拟强制轮转和组件重启后，新旧日志、指标和持久化结果均可查询 |
| 诊断 | 1.7.7 的 ps/inspect/logs 可生成诊断包；首次包 25 个文件，无 collection_notes |
| 修复后清理 | 海光模拟项目逐项 stop/rm/inspect 后，无项目容器及 CNI HOSTPORT DNAT 跳转残留，network/state 已移除；数据及 foreign 容器保留 |
| 空 namespace 反例 | 正确列出七个缺失镜像，不会误用其他 namespace 的缓存镜像 |

SLO 的上界故意设为 1，只验证执行和清理，不代表服务实际容量。测试原始证据、日志、截图和结果保存在 `generated/nerdctl-177-validation/`，不提交原始运行日志或大型镜像归档。

## 复验方式

复制 `configs/stacks/nerdctl-offline-smoke.json`，保持 `runtime` 为 `["nerdctl", "--namespace", "benchmark"]`，使用同一 namespace 导入镜像。然后按 [nerdctl 指南](../nerdctl.md)执行 images check、up、verify_simulation、bench suite、logs collect 和 down。

## 覆盖边界

本次验证运行时和软件链路，推理接口与 GPU 指标使用 CPU fixture。它不证明现场 runc/cgroup、真实 NVIDIA/海光驱动或 `dcu-exporter:v2.4.1` 指标契约正确。DCU Exporter 的实际指标名称、型号和卡序号标签仍应读取现场 `/metrics` 后映射，不能直接将旧版 dcu 指标视为 HCU v3 的 hcu 指标。
