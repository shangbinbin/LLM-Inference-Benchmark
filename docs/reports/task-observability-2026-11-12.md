# 任务观测与统一 Dashboard 验证记录（2026-11-12）

验证对象为正式 `benchmark_stack.py` 生成的任务标签、Prometheus/Alloy/logrotate 配置及统一 Dashboard。使用本地已缓存的 amd64 镜像，未构建或拉取镜像；未连接用户的离线推理服务。两套隔离的测试项目分别使用 NVIDIA 和海光模拟指标，推理请求由 CPU fixture 响应。

## 检查结果

| 检查 | 结果 |
| --- | --- |
| `python3 -m unittest discover -s tests -q` | 100 项通过 |
| NVIDIA / 海光完整启动与 AISBench | 两套均 8/8 正式请求成功，0 失败 |
| Prometheus GPU 标签 | 两厂商均可按 task、gpu_node、gpu_model、gpu_index 查询；原生设备标签保留 |
| 推理实例标签 | 同任务 prefill/decode 两个端点独立标记 instance_role/replica；指标命名空间识别 vLLM/SGLang |
| Dashboard 查询 | NVIDIA 44 条、海光 40 条 PromQL 均执行成功；两版日志与 Volume 的 LogQL 均通过 |
| Loki 任务隔离 | 写入其他任务的日志作为反例，指定任务/角色/副本的查询未返回该日志 |
| Grafana 浏览器验证 | Chromium 加载两版 Dashboard 无页面/API 错误；筛选 decode/decode-0/decode.log 后，日志和 Volume 仅显示选定副本 |
| 特殊字符回归 | 浏览器选择带点号的日志文件、GPU 节点及带点号/加号的型号后仍可查询匹配数据；避免 regex 转义导致查询失败或误匹配 |
| 厂商切换 | NVIDIA Dashboard 不含 hcu 查询；海光 Dashboard 不含 DCGM 查询；auto 检测分支另有单元验证 |
| 模拟验收工具 | 两套 `verify_simulation.py` 均通过；海光追加轮转、采集/存储组件重启及新旧日志续读检查通过 |

浏览器使用 Grafana 12.0.2，查询分别由 Prometheus 3.5.0、Loki 3.5.0 执行，文件采集使用 Alloy 1.8.3。模拟日志在当前云环境由容器 UID 创建；轮转测试前仅为测试文件补充了宿主测试用户写权限，没有更改生产日志权限。

## 覆盖边界

这些结果验证配置、查询、Dashboard 交互和软件链路。推理性能指标为测试数据，不代表实际 vLLM/SGLang 性能；GPU 为模拟数据，不验证真实 NVIDIA/海光驱动、Exporter 兼容性或硬件数值准确性。海光型号需填写现场值或映射实际 Exporter 的型号标签；利用率和功耗仍保持未知单位的原始值。不同推理服务版本未暴露的指标显示 No data。

当前每个观测栈支持一个 GPU 厂商，自动切换发生在生成/启动时；不覆盖混合厂商集群在同一 Dashboard 内的动态切换。
