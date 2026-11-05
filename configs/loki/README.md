<!--
 * @Author: shangbinbin shangbinbin1993@outlook.com
 * @Date: 2026-07-27 14:40:23
 * @LastEditors: shangbinbin shangbinbin1993@outlook.com
 * @LastEditTime: 2026-07-31 19:26:19
 * @FilePath: /vllm bench serve自定义数据集压测/config/loki/README.md
 * @Description: loki配置说明
 * 
 * Copyright (c) 2026 by shangbinbin shangbinbin1993@outlook.com, All Rights Reserved. 
-->

# loki配置说明

loki组件使用的配置文件参见[此处](./loki.yaml)，使用的镜像是由[此处](./loki-dockerfile/Dockerfile)的Dockerfile构建得到的，启动loki组件的启动命令如下所示：

```shell
/usr/bin/loki -config.file=/private/benchmark-data/loki/config/loki.yaml
```

其中的`loki`文件夹对应层级结构如下所示：

```console
[root /private/benchmark-data]$tree ./loki -L 2
./loki
├── config
│   └── loki.yaml
├── data
│   ├── chunks
│   ├── compactor
│   ├── rules
│   ├── tsdb-cache
│   ├── tsdb-index
│   └── wal
└── loki-dockerfile
    └── Dockerfile

9 directories, 2 files
```

上述文件层级中的`Dockerfile`对应的就是前述提到的构建loki镜像时使用的Dockerfile。

使用当前镜像和前述的配置文件，在k8s环境中实际启动的loki pod对应的yaml文件配置参见[此处](./loki-pod-example.yml)。

## 分布式多 Pod 扩展规则

SGLang Pod 数量增加时通常不需要为每个 Pod 新建 Loki。Alloy 仍向同一个
`/loki/api/v1/push` 写入，不同 Pod 通过 `deployment`、`inference_group`、`role`、
`node_rank`、`replica_id` 和 `pod` 等低基数标签形成独立 stream。请求 ID、完整 prompt、
容器 ID等高基数字段只能保留在日志正文中，不能直接设为 label。

需要随 Pod 数量重新评估日志写入速率、WAL、chunk、索引缓存和 PVC 容量。当前配置仍是
`replication_factor: 1`、in-memory ring 和本地 filesystem 的单实例短周期方案；多 Pod
日志接入不等于 Loki 已具备高可用或 retention。生产长期使用前仍需单独设计对象存储、
compactor/retention、容量告警和备份。
