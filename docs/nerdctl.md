# 使用 nerdctl / containerd 运行 llm-bench

启动器已实现 nerdctl 适配，沿用同一套镜像、配置、任务标签与 Dashboard。入口仍为 `llm-bench`，通过 site.json 的 runtime 选择引擎。这里部署的是 containerd 中由 nerdctl 管理的独立容器，不会创建或修改 Kubernetes Pod。

## 运行环境与 namespace

推荐 Linux amd64，已验证 nerdctl 1.7.7 / containerd 1.7.22 和 nerdctl 2.1.3 / containerd 2.1.3 两组软件链路；还需匹配的 OCI 后端、完整 CNI 插件和 iptables 等网络依赖。正式本机 GPU / host 指标采集使用 rootful 权限；先确认当前用户有权操作 containerd、CNI、设备和 bind mount 路径。服务启动不依赖 BuildKit；只有在线构建项目镜像时需要另行配置 BuildKit，当前 build 使用 host 网络，还需相应的 network.host entitlement。

```json
"runtime": ["nerdctl", "--namespace", "benchmark"]
```

需要指定 socket、snapshotter 或 cgroup manager 时，放在同一个 runtime 数组中，作为 nerdctl 全局参数：

```json
"runtime": [
  "nerdctl", "--address", "/run/containerd/containerd.sock",
  "--namespace", "benchmark", "--snapshotter", "overlayfs"
]
```

namespace 必须显式填写，支持 `--namespace NAME`、`--namespace=NAME`、`-n NAME`，不能填写两次。不要依赖不同终端的默认 namespace。`benchmark` 是示例独立 namespace；如需使用 `k8s.io` 中的现有镜像，可明确选择它，但本工程仍只管理自己创建的带项目标签容器，不接管 Kubernetes 容器。

可通过 `sudo ./bin/llm-bench ...` 使用节点已有的 rootful 环境；注意保持 Token 环境变量和路径访问权限。也可使用 `["sudo", "-n", "nerdctl", "--namespace", "benchmark"]`，前提是现场已授予相应权限，启动器不修改 sudo 配置。使用自定义包装脚本时，显式设置 `"runtime_kind":"nerdctl"`，并在 runtime 数组保留实际 namespace 参数；包装脚本须把参数传给同一 containerd，处理环境变量透传。

## 离线镜像导入

源码、配置和 tokenizer 放在执行 llm-bench 的节点；bind mount 源路径必须对 containerd 所在节点可见。Docker 和独立 containerd 的镜像存储不同，同一 containerd 的不同 namespace 也不自动共享镜像名称。

```bash
# 全部命令在离线源码根目录执行；归档路径换成实际位置。
nerdctl --namespace benchmark version
nerdctl --namespace benchmark info
nerdctl --namespace benchmark load -i /home/llm-bench-delivery/offline-bundle/images/images-linux-amd64.tar
```

同一份 amd64 Docker/OCI 镜像归档可以导入 nerdctl；**本次运行时适配无需重新构建 toolbox、AISBench 或监控镜像。** GPU Exporter 仍需选择与现场驱动兼容的版本。镜像检查、启动、诊断、状态和清理都使用配置中的同一 runtime/namespace。离线模式下所有 run/create 使用 `--pull=never`，缺镜像时在创建项目网络之前停止，不会隐式构建或下载。

## 先跑完整冒烟测试

提供 [nerdctl-offline-smoke.json](../configs/stacks/nerdctl-offline-smoke.json) 和 `nerdctl-offline-smoke` profile。该模板是 Linux amd64，无需 GPU；使用仓库 mock tokenizer 和模拟推理接口验证软件链路。要验证已经交付的模型 tokenizer，把复制后的 `storage.tokenizer` 改为 `assets/tokenizer` 或现场 tokenizer 的绝对路径。

```bash
mkdir -p generated/site
cp configs/stacks/nerdctl-offline-smoke.json generated/site/nerdctl-smoke.json
# 编辑上面的 JSON：runtime namespace、镜像名称、tokenizer 和空闲端口。
./bin/llm-bench images check --config generated/site/nerdctl-smoke.json
./bin/llm-bench plan --config generated/site/nerdctl-smoke.json
./bin/llm-bench up --config generated/site/nerdctl-smoke.json
./bin/llm-bench status --config generated/site/nerdctl-smoke.json
python3 scripts/verify_simulation.py --config generated/site/nerdctl-smoke.json
```

`up` 创建 fixture、node_exporter、Loki、logrotate、Alloy、Prometheus、Grafana，随后执行 8 次请求、并发 2 的 AISBench 压测。成功后观测组件继续运行。默认 Grafana `http://节点IP:33004`、Prometheus `http://节点IP:39094`、Loki `http://节点IP:33104`，按 ports 配置修改；日志与结果保留在 storage.root 和 storage.logs 下。模拟指标不代表真实硬件性能。

查看客户端日志、再次发压、打包诊断与停止：

```bash
nerdctl --namespace benchmark logs --tail 100 llm-nerdctl-smoke-aisbench
./bin/llm-bench bench run --config generated/site/nerdctl-smoke.json
./bin/llm-bench logs collect --config generated/site/nerdctl-smoke.json
./bin/llm-bench down --config generated/site/nerdctl-smoke.json
```

`bench run` 和顺序压测的进度/输出写入对应运行目录的 client.log，运行结束后汇总结果；命令行为沿用 [CLI 指南](cli.md)。`down` 删除本项目状态中登记且 ownership label 匹配的容器和网络，保留持久化日志、监控数据、tokenizer 与压测结果；不会清空 namespace 或删除镜像。

## 对接真实推理服务

单机 NVIDIA 以 [GPU41 Qwen3-0.6B 完整示例](gpu41-qwen3-random-1k.md)生成现场文件：

```bash
python3 scripts/create_site_config.py --runtime nerdctl --namespace benchmark
```

输出 `generated/site/site.json`；已存在时拒绝覆盖，直接编辑已有文件或用 --output 另选路径。示例为随机 1k/1k、1000 次请求、并发 16，需核对接口、模型、日志、tokenizer、节点及端口。选择 Docker 可改用生成参数 `--runtime docker`；两者业务字段相同。通用模板 [nerdctl-nvidia-single.json](../configs/stacks/nerdctl-nvidia-single.json) 和 `nerdctl-single` profile 仍可使用，参数与 GPU41 现场示例不同。已有栈先 down，再更改 runtime 并 up。

需要核对：`project`、`task`、`storage.root/logs/tokenizer`、`gpu.nodes`、`inference.base_url/model/api/api_key_env`、端口和 benchmark。GPU41 使用 host_metrics=container；只有选择 host 模式才需填写可达的 `node_exporter_target`。标签、日志角色与 GPU 节点/型号/卡序号配置见 [任务观测说明](task-observability.md)。默认的 task 仍可省略并使用 project。

```bash
# 需要鉴权时，在同一终端导出 inference.api_key_env 指定的变量。
./bin/llm-bench images check --config generated/site/site.json
./bin/llm-bench up --config generated/site/site.json --no-benchmark
./bin/llm-bench bench run --config generated/site/site.json
```

NVIDIA 本机 Exporter 的参数按引擎区分：

| 引擎 | GPU 注入方式 | 节点要求 |
| --- | --- | --- |
| Docker | `--runtime=nvidia` + NVIDIA 环境变量 | 已注册 Docker nvidia runtime |
| nerdctl | `--gpus all` + NVIDIA 环境变量 | 驱动正常、`nvidia-container-cli` 可执行及依赖库可用 |

两者都保留 `SYS_ADMIN`、compute/utility 和默认 basic collectors。普通观测/压测容器不申请 GPU，external Exporter 和模拟模式不注入 GPU。不要把 Docker 的 runtime 别名 `nvidia` 直接当作 containerd shim 名称；Kubernetes 中已配置 NVIDIA RuntimeClass 也不等于 nerdctl 的 `--gpus` 已可用。参考 [nerdctl GPU 文档](https://github.com/containerd/nerdctl/blob/v2.1.3/docs/gpu.md)。

海光配置 `gpu.vendor=hygon`，保留原有 HCU Exporter 设备、运行库和挂载要求；不添加 NVIDIA 参数。真实 NVIDIA/海光硬件仍需在现场验证。

## 适配行为与常见排障

- **组件名称解析**：nerdctl 2.1.3 没有 `--network-alias`，启动器改用短 hostname（loki、prometheus 等）。nerdctl 的 hosts store 根据同一 CNI 网络中的 hostname 更新各容器 `/etc/hosts`，保留现有内部 URL；不需要新 DNS 镜像。host 网络的 node_exporter 使用节点地址。
- **1.7.7 网络作用域**：CNI 网络名可跨 containerd namespace 可见，project 名称应在节点上唯一；不能仅靠换 namespace 复用同名网络。启动器仍拒绝接管已存在网络。
- **检查格式**：容器、网络和镜像 inspect 明确指定 `--mode=dockercompat`，镜像检查同时指定平台，避免多架构默认选择错误。nerdctl 的 native 输出不能直接替代此格式。1.7.7 的部分 Config 字段可能为空；查看启动参数使用 `--format 'Path={{.Path}} Args={{json .Args}}'`，PID 使用 `{{.State.Pid}}`。
- **容器日志**：nerdctl run/create 显式使用 `--log-driver=json-file`，使 `nerdctl logs` 和离线诊断可用。日志文件位置由 nerdctl data-root/namespace 管理，推理服务的原始日志仍由 storage.logs 和 log_sources 指定。
- **1.7.7 删除返回码**：`rm -f` 可能记录删除失败却返回 0；已改为先 stop、再普通 rm，并通过 inspect 确认不存在，down 和 SLO 临时容器共用此逻辑。使用旧 `8099c15` 时需同步本次源码修复，无需重建镜像。
- **故障保留**：containerd/socket/权限错误不会当作容器不存在；清理失败时保留状态用于重试。state.json 记录 runtime 和 namespace，禁止在清理时切到另一个 namespace 后误删状态。
- **CNI 报错**：检查 bridge、host-local、loopback、portmap、firewall、tuning 插件、iptables、IP 转发及节点已有网络规则；启动器不自动安装依赖、清空规则或修改 containerd 系统配置。
- **离线兼容开关**：`offline=true` 仍默认启用 `legacy_seccomp_unconfined`，作用于该项目全部新容器。安全范围与显式关闭方法见 [兼容说明](legacy-docker-threads.md)。

## 从 Docker 迁移或离线更新

先使用原配置 down 原栈，再同步当前 `scripts/`、`bin/`、`configs/`、`containers/` 等完整源码，导入镜像到选定 containerd namespace，最后修改现场 runtime 并重新 up。不要只复制 benchmark_stack.py，新增 `scripts/container_runtime.py` 也是必要依赖。

不用重建已有镜像，不用重启已有 Docker 或 containerd。切换运行时不能用 restart 原容器代替；数据目录可保留，但新运行身份需有读写权限。原 namespace 和原 Docker 中已有 Kubernetes/第三方容器由其管理者维护。

集成验证使用真实 nerdctl 1.7.7/containerd 1.7.22、nerdctl 2.1.3/containerd 2.1.3、缓存镜像及 CPU fixture；云环境因 cgroup 限制使用 runsc OCI 后端并为测试 CNI 加入 loopback 插件。它验证软件链路，不等同于生产节点 runc/cgroup/GPU 验收，详见 [1.7.7 验证记录](reports/nerdctl-1.7.7-2026-11-12.md)及 [2.1.3 验证记录](reports/nerdctl-2026-11-12.md)。
