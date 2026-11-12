# Loki 无标签：日志路径检查与修复验证（2026-11-12）

现场提供的 site.json 使用 storage.logs=/srv/inference-logs、log_sources[0].path=server.log，而此前给出的服务日志是 NFS 模型目录中的 server_log.log。现场 Alloy 日志仅显示初始化和 HTTP ready；缺少文件匹配不能靠 readiness 判断。未访问现场节点、NFS 或推理服务，实际服务文件位置仍由现场确认。

使用缓存的 Alloy v1.8.3、Loki v3.5.0 和原 toolbox 镜像，在本机独立项目复现同类错误。未构建或拉取镜像，未修改原有容器；测试只读取自建 fixture 文件，不向生产日志注入内容。

| 检查 | 结果 |
| --- | --- |
| 137 项单元测试 | 全部通过，含 Docker/nerdctl 命令适配、源文件缺失、挂载失败、延迟创建、空文件、软链接和快照漂移 |
| 文件只有 server_log.log，配置误写 server.log | Alloy /-/ready 成功，Loki 标签列表为空，复现截图的后端状态 |
| 新 up 检查 | 缺失路径在创建网络/常驻组件前被拒绝，提示实际拼接路径和应修改的字段 |
| 将同一采集配置改为 server_log.log | 文件入库，task、instance_role、replica、source_file 等标签可见 |
| Dashboard Logs Volume 的实际 LogQL | Loki 查询返回非零计数 |
| logs check 活动栈 | 检查只读挂载、Alloy/Loki 和任务日志，返回 status=ok |
| Loki 空 labels 响应 | 兼容 status=success 但无 data 字段，标记 pending，不误报解析失败 |
| 输出隐私范围 | 文件状态、字节数和标签；不返回日志原文或推理 Token |
| 清理 | 临时测试容器及网络已删除，原有项目资源保留 |

本次真实容器集成验证使用 Docker；nerdctl 新探测参数由单元测试验证，未在现场 nerdctl/DCU/NVIDIA 硬件上执行。CPU fixture 与 Loki 入库验证不构成真实模型或硬件性能验收。

现场修正、重新创建项目容器和诊断命令见 [操作手册](../loki-log-troubleshooting.md)。
