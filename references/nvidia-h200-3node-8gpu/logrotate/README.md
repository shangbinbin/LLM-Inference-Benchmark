<!--
 * @Author: shangbinbin shangbinbin1993@outlook.com
 * @Date: 2026-07-27 14:24:14
 * @LastEditors: shangbinbin shangbinbin1993@outlook.com
 * @LastEditTime: 2026-08-01 15:24:47
 * @FilePath: /LLM-Inference-Benchmark/references/nvidia-h200-3node-8gpu/logrotate/README.md
 * @Description: logrotate配置说明
 * 
 * Copyright (c) 2026 by shangbinbin shangbinbin1993@outlook.com, All Rights Reserved. 
-->

# logrotate配置说明

> 本文及配套文件来自 `sglang-inference-platform`，作为压测链路搭建参考。
> 来源版本、适用范围和配置适配说明见[参考配置索引](../README.md)。

logrotate组件使用的配置文件参见[此处](./sglang-logrotate.conf)，使用的镜像是由[此处](https://github.com/shangbinbin/sglang-inference-platform/blob/46609ee6b3095e0851e612a5b5c587e7a78f280e/logrotate/logrotate-dockerfile/Dockerfile)的Dockerfile构建得到的，该文件夹下的[`docker-entrypoint.sh`](https://github.com/shangbinbin/sglang-inference-platform/blob/46609ee6b3095e0851e612a5b5c587e7a78f280e/logrotate/logrotate-dockerfile/docker-entrypoint.sh)对应的是该镜像的启动命令。配置的启动命令具体如下所示：

```shell
LOGROTATE_LOG_FILE=/private/benchmark-data/sglang-log/sglang-server.log LOGROTATE_CONFIG=/private/benchmark-data/logrotate/config/sglang-logrotate.conf LOGROTATE_STATE=/private/benchmark-data/logrotate/data/status LOGROTATE_INTERVAL_SECONDS=60 docker-entrypoint.sh
```

前述示例命令给出的是针对单机部署时的样例，如下启动命令对应的是针对分布式部署时的样例，区别在于读取了不同的配置文件：

```shell
LOGROTATE_LOG_FILE=/private/benchmark-data/sglang-log/sglang-server-rank-0.log LOGROTATE_CONFIG=/private/benchmark-data/logrotate/config/sglang-distributed-logrotate-example.conf LOGROTATE_STATE=/private/benchmark-data/logrotate/data/status LOGROTATE_INTERVAL_SECONDS=60 docker-entrypoint.sh
```

GLM5.2 三节点、每节点 8 GPU 场景不要继续使用上面的两节点配置，必须改用
[`sglang-glm52-3node-8gpu-logrotate.conf`](./sglang-glm52-3node-8gpu-logrotate.conf)。
以下命令使用独立 state，避免与单机或两节点轮转状态互相覆盖：

```shell
LOGROTATE_LOG_FILE=/private/benchmark-data/sglang-log/glm52-3node-8gpu-run-01-rank-0.log LOGROTATE_CONFIG=/private/benchmark-data/logrotate/config/sglang-glm52-3node-8gpu-logrotate.conf LOGROTATE_STATE=/private/benchmark-data/logrotate/data/status-glm52-3node-8gpu LOGROTATE_INTERVAL_SECONDS=60 docker-entrypoint.sh
```

入口脚本只预创建 `LOGROTATE_LOG_FILE` 指定的 rank 0 文件，rank 1/2 文件由各自 SGLang Pod
创建；配置中的 `[0-2]` 会在文件出现后覆盖全部三个 Rank。每次修改三节点 profile 的
`logging.persistent.filePrefix` 时，必须同步修改这里的活跃文件 glob 和上述启动命令。

其中的`logrotate`文件夹层级结构如下所示：

```console
[root /private/benchmark-data]$tree ./logrotate/
./logrotate/
├── config
│   └── sglang-logrotate.conf
├── data
│   └── status
└── logrorate-dockerfile
    ├── docker-entrypoint.sh
    ├── Dockerfile
    ├── README.md
    └── sglang-logrotate.conf

3 directories, 6 files
```

上述文件层级中的`logrorate-dockerfile`文件夹中的内容对应的就是前述Dockerfile所在的`logrorate-dockerfile`文件夹中的内容。

使用当前镜像和前述的配置文件，在k8s环境中实际启动的logrotate pod对应的yaml文件配置参见[此处](./logrotate-pod-example.yml)。

该 Pod YAML 是单机 `sglang-server.log` 示例，不能不修改就用于分布式 Rank 日志。分布式
场景需要使用上面的分布式配置和启动参数。入口脚本只会 `touch` `LOGROTATE_LOG_FILE`
指定的一份文件，因此示例中的 rank 1 文件仍由 worker Pod 创建；Alloy 通过周期文件发现
接入后续出现的文件，日志采集不能依赖 logrotate 预创建所有 Rank 文件。

两节点 TP=2 分布式推理场景下，同时轮转 master 和 worker 独立日志文件的 glob 配置示例参见[此处](./sglang-distributed-logrotate-example.conf)。

## 分布式多 Pod 扩展规则

- 每个 Pod 必须写入独立活跃文件，配置 glob 覆盖所有受管 Pod 文件。
- 共享 PVC 上同一文件集合只能由一个 logrotate 实例处理；每个 Pod 都运行同一 glob 会
  产生并发轮转竞争。
- 多个推理组并行时优先使用 `<deployment>/<inference_group>/` 分组目录，或为每组设置
  独立前缀和配置块；路径变化必须与 SGLang `SGLANG_LOG_DIR`、Alloy `__path__` 同步。
- `copytruncate` 继续用于保持 `tee` 和 Alloy 的活跃路径不变，但轮转时需要同时容纳
  活跃文件与复制文件，并保留极短竞争窗口的风险。

当前 `sglang-distributed-logrotate-example.conf` 只管理
`sglang-server-rank-*.log`，对应两节点单 GPU 样例的 rank 0/1。增加其他前缀或分组目录
后必须显式扩大或增加配置块，不能假设当前 glob 会自动覆盖。

三节点 GLM5.2 已提供独立配置，固定管理
`glm52-3node-8gpu-run-01-rank-[0-2].log`。共享 RWX PVC 上只能有一个 logrotate 实例处理
这三个文件，不能在每个 SGLang Pod 中分别启动相同的轮转任务。
