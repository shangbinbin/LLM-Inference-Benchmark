# Mac 离线模拟验证（2026-11-07）

已在 Apple Silicon Mac、Docker Desktop（Engine 29.6.2）完成五项验证：三个缺镜像失败路径，以及 NVIDIA/海光两种指标配置下的完整模拟链路。两次实际 AISBench 各完成 8 个正式请求，失败数均为 0。生产配置仍为 linux/amd64，本次使用 linux/arm64 镜像与模拟推理服务。

## 验证方法与边界

运行仓库根目录下的验证入口：

```bash
python3 docs/examples/verify_offline_mac.py
```

前提是 Docker Desktop 正常运行，且 `configs/stacks/mac-simulation.json` 所选的 toolbox、AISBench、Prometheus、Grafana、Loki、Alloy、node_exporter 七个 arm64 镜像已在本地。缺少前提镜像时预检查失败，不替用户下载。GPU 指标来自 fixture，不拉取或运行 DCGM/HCU 硬件 Exporter。

验证脚本调用当前 `Stack.up()`，在测试进程内拦截以下操作：

- `docker network create` 添加 `--internal`，保留栈内服务通信；所有业务容器仅接入这个网络，不发布宿主机端口。
- `docker run/create` 添加 `--pull=never`，防止 Docker daemon 自动补拉镜像。
- 拒绝启动器的 `docker build/pull` 和 AISBench 源码 `urlopen` 下载，并记录调用路径。
- 独立 project、存储目录和标签；结束后按标签检查并清理本次容器/网络，保留报告及日志。

Docker 内部网络本身不能禁止 daemon 拉取镜像，因此同时需要容器启动时的 pull 策略。[Docker 内部网络](https://docs.docker.com/reference/cli/docker/network/create/)、[Docker run 拉取策略](https://docs.docker.com/reference/cli/docker/container/run/)。

**这是具有测试保护的离线模拟，不是给生产启动器新增 offline 模式，也没有切断 Mac 主机或 Docker daemon 的全部公网访问。** 普通 `benchmark_stack.py up` 仍保留原有自动构建/拉取行为。该结果也不能代替 amd64 镜像、真实模型 tokenizer、设备驱动、真实 GPU、containerd 或整机隔离环境的验收；本次未测试镜像归档导入/导出和离线 SLO 搜索。

## 实测结果

| 场景 | 结果 |
| --- | --- |
| toolbox 指向不存在的独立标签 | 启动器进入 build 路径，被测试保护拦截；没有真正构建 |
| AISBench 指向不存在的标签，且没有源码缓存 | 启动器进入源码下载路径，被测试保护拦截；没有真正下载 |
| node_exporter 指向不存在的标签 | Docker 在 `--pull=never` 下报镜像不存在，退出码 125；没有拉取 |
| 镜像齐全，NVIDIA 模拟指标 | 所有观测组件就绪，AISBench 成功 8/8、失败 0；Prometheus 三个目标 UP，`DCGM_FI_DEV_GPU_UTIL` 可查 |
| 镜像齐全，海光模拟指标 | 所有观测组件就绪，AISBench 成功 8/8、失败 0；Prometheus 三个目标 UP，`hcu_utilizationrate` 可查 |

两种厂商配置均确认：

- 网络 `Internal=true`，所有栈内容器没有接入第二个网络。
- 在同网络探测容器内直接连接 `1.1.1.1:443`、`8.8.8.8:443`，均返回 `Network is unreachable`。
- Grafana health 的 database 状态为 ok；Loki 各查询到 9 条模拟请求日志（1 次预热和 8 次正式请求）。
- 正向启动过程没有尝试构建、源码下载或显式 pull；全部 run/create 使用 `--pull=never`。
- 测试容器与网络已清理；原有 `llm-bench-mac`、`llm-slo-mac` 两套模拟栈仍正常运行。

实际运行 ID 为 `e29883f5`。镜像 ID、各场景结果和审计摘要见 [JSON 快照](mac-offline-validation-2026-11-07.json)。完整命令记录、运行配置、AISBench 原始报告与容器日志保存在本机 `generated/offline-validation/e29883f5/`，控制台日志为 `generated/offline-validation-console.log`；大体积原始数据不提交仓库。

## 发现的默认外联行为

本次保留 Grafana 默认配置，以检查当前工程的实际表现。日志确认 Grafana 尝试向 grafana.com 请求版本更新、预安装插件、Angular 插件检测模式和插件签名公钥；在隔离网络下这些请求因 DNS/网络不可达失败。它们没有阻止本次核心监控与压测链路启动，但“启动成功”不能解释为“没有任何外联尝试”。

严格离线交付仍应按 [离线部署手册](../offline-deployment.md)准备镜像和本地 tokenizer，调整相关外联设置，并在目标网络策略下验收。单纯复制仓库或只携带 AISBench 源码不够。
