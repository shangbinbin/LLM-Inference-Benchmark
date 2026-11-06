# NVIDIA 适配

`h200/profile.json` 选择 `dcgm/` 适配器；DCGM 的采集 job 和 GPU Dashboard 位于该适配器内。节点地址、24 卡规模与 TP/PP 不属于适配器，见 `configs/environments/nvidia-h200-3node-8gpu/environment.json`。

面板沿用来源版本的 `DCGM_FI_*` 指标及 `node`、`gpu`、`UUID`、`modelName` 标签，未改为跨厂商指标。Exporter 安装方式、支持的计数器及现场核对步骤见[原始 GPU 说明](../../../references/nvidia-h200-3node-8gpu/gpu-monitoring/README.md)。新增型号时先核实所需计数器是否存在，再决定共享或新建适配器。
