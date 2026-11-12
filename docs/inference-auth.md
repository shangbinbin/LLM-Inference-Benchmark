# 推理服务 Bearer Token 鉴权

单次压测、顺序用例和自动 SLO 搜索均支持推理服务 Bearer Token，适用于 completions、chat 及多轮 chat 请求。配置中只记录环境变量名，实际 Token 在启动压测的进程环境中提供。

## 配置与运行

在现场 stack JSON 的 inference 中加入 api_key_env：

```json
"inference": {
  "base_url": "http://inference-host:30000/",
  "model": "your-model",
  "api": "completions",
  "api_key_env": "INFERENCE_API_KEY"
}
```

api_key_env 是变量名，不是 Token。无需鉴权时省略该字段；不支持把明文 api_key 写入 JSON。变量名应使用字母、数字、下划线，首字符不能为数字。

在执行启动/压测命令的同一个 Bash 终端中输入 Token，避免将值直接写入 shell 历史：

```bash
read -r -s -p 'Inference token: ' INFERENCE_API_KEY
printf '\n'
export INFERENCE_API_KEY

python3 scripts/benchmark_stack.py up --config /absolute/path/site.json --no-benchmark
# 选择一种压测方式，均继承上述环境变量：
python3 scripts/run_aisbench_suite.py --config /absolute/path/site.json --suite /absolute/path/suite.json
# 或：python3 scripts/slo_search.py --config /absolute/path/site.json --search /absolute/path/search.json
# 单次压测：首次启动时使用 up --config /absolute/path/site.json（不加 --no-benchmark）

unset INFERENCE_API_KEY
```

填写纯 Token，不要添加 `Bearer ` 前缀。客户端自动发送 `Authorization: Bearer <token>`。变量缺失、空值、含空白或误加 Bearer 前缀时直接报错，不降级成匿名请求。新终端、sudo 或作业调度器启动的进程也必须获得该变量。统一 CLI `scripts/llm_bench.py` 同样使用 stack 中的此配置。

配置可以在没有 Token 时渲染/查看计划；实际启动及压测时必须提供 Token。已有栈的现场配置与 manifest 必须保持一致：增加 api_key_env 后先用原配置 down，再用新配置重新 up；每次独立运行 suite/search 都重新提供环境变量，Token 值变化不必改 JSON。

## 探测、指标和凭据存储

推理服务 `/v1/models` 就绪探测使用同一个 Token，401/403 会导致探测失败；认证探测拒绝重定向，请填写最终服务根地址。顺序用例及每次 SLO trial 都会重新检查推理服务。监控组件和 GPU Exporter 的探测不会收到推理 Token。若推理服务 `/metrics` 也要求鉴权，需要单独配置 Prometheus 抓取鉴权；此字段不控制指标采集。

Docker 命令使用 `-e INFERENCE_API_KEY` 转发值，命令日志只显示变量名。生成配置、manifest、suite/trial JSON 不保存实际 Token；AISBench 扩展在构建 API 客户端时才读取环境变量，避免其 MMEngine 配置快照写出 Token。运行的客户端及推理探测容器环境仍含 Token，Docker 管理员可以查看，运行账户须具备相应授权。优先使用 HTTPS 保护网络传输，不把 Token 写入 base_url。

## 离线环境升级

本功能修改了 toolbox 探测程序，已导出的旧 toolbox 镜像不会自动更新。请在在线机重新构建、导出，再在离线机导入（保持 stack 中配置的标签，核对新的 image ID）：

```bash
docker build --network=host --platform linux/amd64 \
  -t llm-benchmark-toolbox:local-amd64 -f containers/toolbox/Dockerfile .
```

同时交付当前仓库源码，包含 `containers/aisbench/llm_bench_auth.py`。该扩展由启动器只读挂载到客户端容器，不要求为鉴权单独重建 AISBench 镜像。按 [离线部署文档](offline-deployment.md) 导出所选镜像、导入并重建栈；offline=true 下仍禁止在线构建和自动拉取。本次代码修改没有拉取或构建镜像。

## 统一基础 URL：自动拼接推理与指标接口

切换模型服务时，填写服务根地址（包含网关前缀）。末尾斜杠可省略，启动器会补齐；不要填写完整的 v1/chat/completions 接口地址。

```json
"inference": {
  "base_url": "http://10.142.118.41:38000/publishaddress/inference/qwen306b/",
  "model": "Qwen3-0.6B",
  "api": "chat",
  "api_key_env": "INFERENCE_API_KEY"
},
"topology": {"kind": "single", "tp": 1, "pp": 1}
```

默认自动生成：

| 用途 | 基础 URL 后追加 | 鉴权 |
| --- | --- | --- |
| chat 压测 | v1/chat/completions | inference.api_key_env |
| completions 压测（api=completions） | v1/completions | inference.api_key_env |
| 推理就绪探测 | v1/models | inference.api_key_env |
| Prometheus 应用指标 | metrics | 默认继承 inference.api_key_env |

例如上述指标地址为 http://10.142.118.41:38000/publishaddress/inference/qwen306b/metrics。默认从 base_url 提取协议和主机:端口；基础地址改为另一模型服务后，所有派生路径自动跟随。无路径前缀的普通服务仍使用 /metrics。模型 ID、tokenizer 和服务实际日志路径也应随被测模型修改，不能仅改变 URL 而继续使用其他模型的 tokenizer。

**使用自动派生时，删除旧配置中显式的 topology.metrics_endpoints、metrics_path、metrics_scheme、metrics_api_key_env，避免保留旧覆盖值。** 尤其从 nvidia-single 模板复制时，其中默认包含 metrics_endpoints 占位符，需删除或填写有效的独立指标端点。

不同指标服务器或不同路径仍可显式覆盖：

```json
"topology": {
  "kind": "single",
  "tp": 1,
  "pp": 1,
  "metrics_endpoints": [{"target": "metrics.internal:9099", "role": "master"}],
  "metrics_path": "/custom/metrics",
  "metrics_scheme": "http",
  "metrics_api_key_env": null
}
```

各字段独立覆盖默认值。metrics_endpoints.target 仅写主机:端口，不拼路径；metrics_api_key_env=null 表示应用指标无需鉴权，也可填写另一个变量名。未设置时使用推理鉴权变量；GPU/node Exporter 不继承推理 Token。

启动时将指标 Token 写入 storage.root/.credentials/metrics.token（文件权限 0600、目录 0700），仅只读挂载到 Prometheus。生成的 Prometheus 配置只引用 credentials_file，不写 Token 值；plan 不读取 Token，也不生成凭据文件。启动需要对应环境变量存在且有效；正常 down 在移除容器后删除该文件，其他压测产物仍保留。启动失败时文件可能留存于受限目录，需保护现场磁盘，不能将整个 storage.root 原样分享。更换 Token 后需正常 down/up 重新生成凭据。

配置生成、Docker/nerdctl 选择及完整启动步骤见 [GPU41 操作指南](gpu41-qwen3-random-1k.md)。站点示例见 [GPU41 Qwen3-0.6B 随机 1k/1k、1000 请求、并发 16 配置](../configs/sites/gpu41-qwen3-0.6b-random-1k.json)。示例采用统一 base_url，不重复填写指标路径或地址。它使用 GPU41 本机单卡 NVIDIA Exporter、容器模式 node_exporter；网关须支持流式 chat、v1/models 和 metrics。日志/tokenizer 使用现场 NFS 模型目录，logrotate 管理 server_log.log，需有权限且不与其他轮转机制冲突。实际 Token 长度以报告为准，ignore_eos 需后端支持。
