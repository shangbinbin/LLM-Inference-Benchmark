# NVIDIA 适配

`h200/profile.json` 选择 `dcgm/` 适配器；DCGM 的采集 job 和 GPU Dashboard 位于该适配器内。节点地址、24 卡规模与 TP/PP 不属于适配器，见 `configs/environments/nvidia-h200-3node-8gpu/environment.json`。

面板沿用来源版本的 `DCGM_FI_*` 指标及 `node`、`gpu`、`UUID`、`modelName` 标签，未改为跨厂商指标。Exporter 安装方式、支持的计数器及现场核对步骤见[原始 GPU 说明](../../../references/nvidia-h200-3node-8gpu/gpu-monitoring/README.md)。新增型号时先核实所需计数器是否存在，再决定共享或新建适配器。

本地 NVIDIA DCGM Exporter 显式使用 `--runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=compute,utility`，保留 `SYS_ADMIN` 能力；宿主机需注册名称为 `nvidia` 的容器运行时，不要求将其设为默认值。启动器不使用 Docker 的 `--gpus all` 设备请求机制。仅修改启动参数，无需因此重建镜像；已有栈需先用原配置正常 down，再以当前源码 up。观测/压测客户端无需 GPU，external Exporter 模式和无 GPU 模拟不使用这些参数。独立 containerd/nerdctl 的 NVIDIA runtime 配置方式需另行适配，不能直接套用 Docker runtime 名称。
