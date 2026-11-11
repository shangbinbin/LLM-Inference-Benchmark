# NVIDIA 适配

`h200/profile.json` 选择 `dcgm/` 适配器；DCGM 的采集 job 和 GPU Dashboard 位于该适配器内。节点地址、24 卡规模与 TP/PP 不属于适配器，见 `configs/environments/nvidia-h200-3node-8gpu/environment.json`。

面板沿用来源版本的 `DCGM_FI_*` 指标及 `node`、`gpu`、`UUID`、`modelName` 标签，未改为跨厂商指标。Exporter 安装方式、支持的计数器及现场核对步骤见[原始 GPU 说明](../../../references/nvidia-h200-3node-8gpu/gpu-monitoring/README.md)。新增型号时先核实所需计数器是否存在，再决定共享或新建适配器。

本地 NVIDIA DCGM Exporter 显式使用 `--runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=compute,utility`，保留 `SYS_ADMIN` 能力；宿主机需注册名称为 `nvidia` 的容器运行时，不要求将其设为默认值。启动器不使用 Docker 的 `--gpus all` 设备请求机制。仅修改启动参数，无需因此重建镜像；已有栈需先用原配置正常 down，再以当前源码 up。观测/压测客户端无需 GPU，external Exporter 模式和无 GPU 模拟不使用这些参数。独立 containerd/nerdctl 的 NVIDIA runtime 配置方式需另行适配，不能直接套用 Docker runtime 名称。

## 本地 DCGM 默认使用已验证的基础指标

`gpu.nvidia_metrics_profile` 默认 basic。启动器只读挂载本目录 dcgm/basic.csv，并传入 --collectors，采集 GPU 利用率、显存已用/空闲、温度、功率、SM/显存时钟、XID 共 8 项指标，不包含 DCGM_FI_PROF_*。

现场 A800 80GB PCIe、DCGM Exporter 4.8.4 默认采集集合出现 `The third-party Profiling module returned an unrecoverable error` 并退出；相同镜像使用上述 8 项基础列表后 HTTP 服务正常启动，DCGM_FI_DEV_GPU_UTIL 返回有效数值。它验证了基础采集可用，不等于已解决 profiling 模块与其他 GPU/驱动组合的兼容问题。无 NVSwitch、CPU 模块未加载的提示应结合硬件判断，不应单独当成致命错误。

如硬件、驱动及 profiling 环境已验证兼容，可在 gpu 对象中设置：

```json
"nvidia_metrics_profile": "full"
```

full 使用所选 Exporter 镜像自带的默认采集集合，不挂基础列表；换镜像标签时默认集合可能变化。基础模式未采集的 profiling、PCIe 错误及其他扩展面板可能显示无数据，这不是指标为零；基本 GPU 利用率、显存、功率、温度仍可查看。external 模式和海光不受该字段影响，模拟模式也不启动真实 Exporter。

更新离线源码时须同时交付 scripts/benchmark_stack.py 和 configs/gpu/nvidia/dcgm/basic.csv；无需重建镜像。已有项目栈先使用原配置 down，再更新源码/配置并 up；只 restart 不能切换采集参数。不要为此重启推理服务、Docker 或重置 GPU。
