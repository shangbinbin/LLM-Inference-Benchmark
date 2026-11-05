<!--
 * @Author: shangbinbin shangbinbin1993@outlook.com
 * @Date: 2026-07-27 13:32:36
 * @LastEditors: shangbinbin shangbinbin1993@outlook.com
 * @LastEditTime: 2026-08-01 15:24:47
 * @FilePath: /vllm bench serve自定义数据集压测/config/alloy/README.md
 * @Description: alloy配置说明
 * 
 * Copyright (c) 2026 by shangbinbin shangbinbin1993@outlook.com, All Rights Reserved. 
-->

# alloy配置说明

alloy组件使用的配置文件参见[此处](./config.alloy)，使用的镜像为`m.daocloud.io/docker.io/grafana/alloy:v1.18.0`。该组件的启动命令具体如下所示：

```shell
/bin/alloy run --storage.path=/private/benchmark-data/alloy/data --server.http.listen-addr=0.0.0.0:12345 /private/benchmark-data/alloy/config/config.alloy
```

前述示例命令给出的是针对单机部署时的样例，如下启动命令对应的是针对分布式部署时的样例，区别在于读取了不同的配置文件：

```shell
/bin/alloy run --storage.path=/private/benchmark-data/alloy/data --server.http.listen-addr=0.0.0.0:12345 /private/benchmark-data/alloy/config/config-distributed-example.alloy
```

GLM5.2 三节点、每节点 8 GPU 场景不要继续使用上面的两节点配置，必须改用
[`config-glm52-3node-8gpu.alloy`](./config-glm52-3node-8gpu.alloy)，以便同时采集
rank 0/1/2，并让文件前缀及 Loki 标签与三节点 profile 一致：

```shell
/bin/alloy run --storage.path=/private/benchmark-data/alloy/data --server.http.listen-addr=0.0.0.0:12345 /private/benchmark-data/alloy/config/config-glm52-3node-8gpu.alloy
```

每次修改 `profiles/glm52-3node-8gpu.json` 的 `logging.persistent.filePrefix` 时，必须同步修改
三节点 Alloy 配置中的三个 `__path__`。共享 RWX PVC 上只能运行一个负责这些文件的 Alloy
reader，不能让两节点和三节点 Alloy 实例同时重复采集同一文件。

其中`alloy`文件夹内部层级结构如下所示：

```console
[root /private/benchmark-data]$tree ./alloy/
./alloy/
├── config
│   ├── config.alloy
│   ├── config-distributed-example.alloy
│   └── config-glm52-3node-8gpu.alloy
└── data
    ├── alloy_seed.json
    ├── loki.source.file.sglang
    │   └── positions.yml
    └── remotecfg

4 directories, 3 files
```

使用当前镜像和前述的配置文件，在k8s环境中实际启动的alloy pod对应的yaml文件配置参见[此处](./alloy-pod-example.yml)。

两节点 TP=2 分布式推理场景下，同时采集 master 和 worker 独立日志文件的 Alloy 配置
示例参见[此处](./config-distributed-example.alloy)。该示例使用 `deployment`、
`inference_group`、`topology`、`node_rank`、`role`、`replica_id`、`pod` 和 `instance`
标签区分、关联两个 Rank，并启用 `file_match` 每 2 秒同步一次文件系统。这样 Alloy 可以
先于 SGLang 启动；即使首次同步时两个 Rank 文件尚不存在，文件出现后也会被自动发现。

`tail_from_end=false` 只控制“没有已保存 position 时从文件开头还是末尾读取”，不负责
发现启动后新增的文件。没有启用 `file_match` 时，静态 target 指向的文件若在 Alloy 启动
时不存在，会出现 `failed to create source, skipping`，后续创建文件也不会自动恢复采集。

## 分布式多 Pod 扩展规则

- 所有 Pod 都必须采集日志，包括不暴露 `/metrics` 的计算 worker。
- 每个 Pod 使用唯一活跃文件，并设置 `deployment`、`inference_group`、`topology`、
  `role`、`node_rank`、`replica_id`、`pod`、`model` 和 `environment` 标签。
- 固定小规模 StatefulSet 可以显式列出 target；N Pod 或频繁变更的 Deployment 应由模板
  生成 target，或从可控文件名/Pod 元数据发现，不能使用无法区分来源的无标签 glob。
- 保持 `file_match.enabled=true`；`sync_period="2s"` 表示文件创建后通常在下一次同步中开始
  采集。显式 target 中的路径和 Rank 标签仍然一一对应，不会因为启用发现而丢失标签。
- 共享 RWX PVC 上的同一文件只能由一个 Alloy 实例读取，否则 Loki 会收到重复日志。
  节点本地日志才适合每节点一个 Alloy，并需要保证采集分片互斥。
- `--storage.path` 必须持久化并为不同 Alloy 实例隔离；positions 丢失且
  `tail_from_end=false` 时，活跃文件可能被重新采集。

当前示例的 master/worker 都属于 `sglang-ib-2node-group-0`，使用两个显式 target。
增加推理组时，需要同时新增该组全部 Pod 的 `__path__` 和标签，并同步修改 logrotate
受管路径；不要为每个 Rank 新建 Loki writer。

三节点配置将三个 Rank 统一标记为 `deployment="glm52-sglang-3node"`、
`inference_group="glm52-sglang-3node-group-0"` 和 `topology="3node-8gpu-tp8-pp3"`，
仅 rank 0 使用 `role="master"`，rank 1/2 使用 `role="worker"`。这些值与 Prometheus 和
Grafana 的三节点过滤条件保持一致。

若平台固定从 `config.alloy` 启动 Alloy，需要把分布式示例内容部署到该文件名；否则应让
启动命令直接读取 `config-distributed-example.alloy`。修改磁盘上的配置文件后必须重载或
重启 Alloy，已经运行的进程不会因为仓库文件变化自动采用新配置。
