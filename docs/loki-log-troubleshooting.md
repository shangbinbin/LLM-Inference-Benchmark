# Loki 标签为空、日志与 Logs Volume 无数据

适用于 Grafana Explore 的 Benchmark Loki 显示 No options found，任务 Dashboard 的日志/Logs Volume 同时为空。先检查文件采集与入库，再检查查询范围；Explore 的 Drilldown 迁移提示不表示 Loki 故障。

## 1. 本次现场配置中的不一致

本次现场 site.json 的实际采集路径由两项拼接：

| 字段 | 现场当前值 |
| --- | --- |
| storage.logs | /srv/inference-logs |
| log_sources[0].path | server.log |
| Alloy 容器内路径 | /logs/server.log |
| 实际要读的宿主机文件 | **/srv/inference-logs/server.log** |

此前提供的真实日志却位于：

```text
/nfs-data/b80b3433c7374662bd67be120e30f566/a78f816093a2b0414d6ea1530dfae279/server_log.log
```

若真实服务仍写到该位置，必须同时修改 storage.logs 和 log_sources[].path；不能只修改 Grafana URL、任务下拉或其中一个字段。Alloy 初始化正常、/-/ready 返回成功，只说明组件启动，不保证 local.file_match 匹配到文件。目标文件不存在时，旧启动器可能创建空日志目录并继续启动，最终 Loki 没有流和标签。

这是本次代码修复的检查缺口；路径仍需由操作者填写真实值，工程不会自动接管或猜测其他目录中的日志。本文路径来自此前现场信息；如果已经迁移，替换成服务当前实际写入的文件。

## 2. 在离线机修正现场 site.json

所有命令在源码根目录执行：

```bash
cd /home/llm-bench-runtime/LLM-Inference-Benchmark
```

下面脚本先确认此前真实文件存在，再只修改日志字段。它保留 project=llm-bench-gpu、task=gpu-inference、storage.root=generated/gpu-site，以及接口、Token 变量名、GPU、端口和负载：

```bash
python3 - <<'PY'
import json
from pathlib import Path

logdir = Path('/nfs-data/b80b3433c7374662bd67be120e30f566/a78f816093a2b0414d6ea1530dfae279')
filename = 'server_log.log'
if not (logdir / filename).is_file():
    raise SystemExit('未找到真实日志，请改为服务实际路径；不要创建空文件冒充：' + str(logdir / filename))
path = Path('generated/site/site.json')
cfg = json.loads(path.read_text())
cfg['storage']['logs'] = str(logdir)
cfg['log_sources'] = [{'path':filename, 'role':'master', 'replica':'replica-0'}]
path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
print('已更新真实日志路径：', logdir / filename)
PY
```

本次只改日志字段，原栈仍运行于旧 manifest；project/storage.root 保持原值，以下 down 能定位并清理原栈。**修改 JSON 不会热更新 Alloy 的配置或挂载。** 确认没有正在发压的用例后，逐条执行：

```bash
# 需要回传诊断时先保留旧栈证据
./bin/llm-bench logs collect --config generated/site/site.json
./bin/llm-bench down --config generated/site/site.json

# 需要鉴权时，在当前 Bash 终端重新导出实际 Token
read -r -s -p 'Inference Token: ' INFERENCE_API_KEY
export INFERENCE_API_KEY

./bin/llm-bench plan --config generated/site/site.json
./bin/llm-bench images check --config generated/site/site.json
./bin/llm-bench up --config generated/site/site.json --no-benchmark
./bin/llm-bench bench run --config generated/site/site.json --requests 8 --concurrency 2
./bin/llm-bench logs check --config generated/site/site.json
```

如果根本没有启动过真实栈，跳过 logs collect/down。只有真实服务会将请求日志写入该文件；AISBench 自身的 client.log 是另一份客户端日志。确认服务会记录请求，并等待 Alloy 发现文件、批量推送和 Loki 查询可见；不向生产日志注入人工测试行。

本次软件修复无需重建镜像或重启 Docker/containerd，更新当前完整源码（包括新增 scripts/log_health.py）并重建项目容器即可；Alloy、Loki、toolbox 沿用原镜像。旧源码也可先按以上路径修正并 down/up；新增 logs check 需更新源码后才有。

## 3. 新增日志链路检查命令

```bash
./bin/llm-bench logs check --config generated/site/site.json
# 查询更早的日志，单位分钟，允许 1..10080
./bin/llm-bench logs check --config generated/site/site.json --lookback-minutes 1440
```

Docker 和 nerdctl 共用此命令，按当前栈保存的 runtime/namespace 执行。功能如下：

| 检查 | 说明 |
| --- | --- |
| host_files | 列出 storage.logs + log_sources 的实际路径、文件状态与字节数，不返回日志原文 |
| 容器内 /logs | 使用现有 toolbox、与 Alloy 相同的 root UID 和只读挂载检查文件；可发现绝对软链接目标未挂载、目录权限等问题 |
| Alloy / Loki readiness | 在项目内部网络探测两个固定服务名，不需要公开端口 |
| Loki 标签和任务日志 | 默认查询最近 60 分钟的标签与 task 对应日志是否存在，不展示日志内容 |
| config_source | 有活动栈时使用 saved running manifest；没有活动栈时只检查输入 site 的本地文件 |
| site_log_settings_differ | 有活动栈时提示现场文件与原快照的日志设置是否不同；为 true 时修改尚未作用于运行容器 |

status=error 表示路径、挂载或链路检查失败，退出码为 1。status=pending 表示尚未验证到该任务在指定时间窗口内有日志，例如尚未 up、文件为空、未出现新请求或日志已超出窗口；退出码为 0，但不宣称链路入库验收成功。status=ok 表示检查通过且窗口内有该任务日志。

命令只读取文件状态和接口，不写日志、不清空 Loki、不删除 Alloy positions，不传入推理 Token，也不需要重新构建 toolbox。非 root CLI 无法读到的文件仍可在活动栈中按 Alloy 的 root UID 验证；容器可读性是最终依据。

## 4. 启动前的新增保护

真实 simulation=false 默认要求配置的日志目录已经存在，具体活跃文件存在、为普通文件且在 Alloy 相同 UID 的只读挂载中可读；缺失或不可读时在创建常驻组件/网络前停止，并提示修改 storage.logs + log_sources[].path。plan 不会再为真实服务自动创建空日志目录，避免把路径错误伪装成已准备完成。

已有的空文件允许启动，因为服务可能在第一条请求时才写内容。对明确知道服务会延迟创建日志文件的场景，可在 site 顶层显式设置：

```json
{
  "allow_missing_logs": true
}
```

这只是合并进完整 site 的字段片段。目录仍必须存在，不放行目录类型错误或实际文件读取失败；仍通过 Alloy 的文件发现处理后续文件。不要为了掩盖本次路径不一致而开启它。simulation=true 的 fixture 会自行创建日志，保持原来的延迟创建行为，不要求预先伪造日志。

## 5. Grafana 查询与后续判断

本次现场 task 为 **gpu-inference**，请在 Grafana Explore → Benchmark Loki 切换 Code 输入：

```logql
{task="gpu-inference"}
```

进一步按实例筛选：

```logql
{task="gpu-inference", instance_role="master", replica="replica-0"}
```

时间范围选择刚完成真实请求的时段。Explore 可用 Live 持续查看，Dashboard 自动刷新日志和 Logs Volume。日志变量没有选项时先用 task 的最小查询，不能把本次任务误选成 GPU41 另一配置示例的 qwen3-0.6b。

如果 logs check 为 ok 而页面仍空，检查 Loki 数据源是否为本栈、task/角色/副本/文件筛选、日志搜索框以及浏览器与节点时间范围。不要重置持久数据来刷新下拉；Alloy 从已保存 positions 续读，没有新行时不会重复读取旧行，旧记录应在原时间段查询。

GPU 某些图表的 No data 是独立检查项：核对对应 Exporter 指标、设备标签和所选时间范围；日志路径修复不会凭空补出未暴露的 GPU/推理指标。

修复验证范围和结果见 [验证记录](reports/loki-log-path-2026-11-12.md)。
