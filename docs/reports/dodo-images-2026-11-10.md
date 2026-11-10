# 联网构建及离线交付镜像验证（2026-11-10）

范围：正式 containers/ Dockerfile 和 configs/stacks/nvidia-single.json。references/ 是历史参考资料，不是当前交付构建入口；Mac 模板未改动。本次未发布环境或部署服务。

使用 `docker --host=unix:///var/run/docker.sock pull --platform linux/amd64 <镜像>` 实测，成功项再执行 image inspect 检查架构并记录 image ID、RepoDigests。测试记录见同目录 JSON。成功表示当前环境的 pull 命令退出码为 0；已有缓存层可能复用，不代表空缓存冷拉取验收。

| 完整上游镜像名 | 渡渡鸟对应地址 | 结果 |
| --- | --- | --- |
| `docker.io/library/python:3.11-slim-bookworm` | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim-bookworm` | 拉取失败：Forbidden；保留原地址 |
| `docker.io/prom/prometheus:v3.5.0` | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/prom/prometheus:v3.5.0` | 拉取成功，linux/amd64；已修改正式模板 |
| `docker.io/grafana/grafana:12.0.2` | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/grafana/grafana:12.0.2` | 拉取成功，linux/amd64；已修改正式模板 |
| `docker.io/grafana/loki:3.5.0` | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/grafana/loki:3.5.0` | 拉取成功，linux/amd64；已修改正式模板 |
| `docker.io/grafana/alloy:v1.8.3` | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/grafana/alloy:v1.8.3` | 拉取成功，linux/amd64；已修改正式模板 |
| `docker.io/prom/node-exporter:v1.9.1` | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/prom/node-exporter:v1.9.1` | 拉取成功，linux/amd64；已修改正式模板 |
| `nvcr.io/nvidia/k8s/dcgm-exporter:4.6.1-4.8.4` | 未找到 | 指定版本未找到；保留原地址 |
| `harbor.sourcefind.cn:5443/hcu/admin/base/hcu-exporter:v3.0.0` | 未找到 | 指定版本未找到；保留原地址 |

## 可直接使用的拉取命令

```bash
docker pull --platform linux/amd64 swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/prom/prometheus:v3.5.0
docker pull --platform linux/amd64 swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/grafana/grafana:12.0.2
docker pull --platform linux/amd64 swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/grafana/loki:3.5.0
docker pull --platform linux/amd64 swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/grafana/alloy:v1.8.3
docker pull --platform linux/amd64 swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/prom/node-exporter:v1.9.1
```

Python 是在线构建两个项目镜像时共用的基础镜像，渡渡鸟页面使用 docker.io/python（省略 library）路径，但本次拉取配置/镜像层时报 Forbidden，因此两个 Dockerfile 保持原值。两个 Exporter 按 GPU 厂商选择，精确版本页面返回 404，站内按名称及标签搜索也无结果；没有更换版本。

项目镜像 `llm-benchmark-aisbench:e330dc1-amd64`、`llm-benchmark-toolbox:local-amd64` 由本项目构建，不能从公共站点拉取。离线交付需保存它们及所选观测/Exporter 镜像；Python 基础镜像仅在线构建需要，离线只运行成品时不必携带。

未改动 generated/ 已运行配置、Mac 的 arm64 模板及 references/ 历史快照。修改正式模板后，交付包导出脚本会使用同一镜像名，目标机应按这些标签导入。

## 现场镜像配置后续调整

按用户要求，两个正式 Dockerfile 的 Python 基础镜像已改为 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim-bookworm`；所有 stack 模板的 DCGM 镜像已改为 `linseerhub-registry.h3c.com:8099/linseer/dcgm-exporter:4.4.2-4.7.0-distroless`（现场离线已备）。本次只修改配置，没有在线拉取或构建。上文和 JSON 保留此前实测结果，不代表调整后的配置。在线准备脚本跳过 DCGM 拉取。

## 后续配置更新（2026-11-11）

按用户明确要求，当前所有 stack 模板已切换为 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nvidia/dcgm-exporter:4.8.4`，本次不重新验证拉取。离线准备脚本已恢复将 DCGM 纳入公共镜像拉取流程。上文保留当时的测试和配置历史，当前镜像以 stack 模板和离线部署文档为准。
