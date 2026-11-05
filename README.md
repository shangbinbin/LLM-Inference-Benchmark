<!--
 * @Author: shangbinbin shangbinbin1993@outlook.com
 * @Date: 2026-11-05 14:18:10
 * @LastEditors: shangbinbin shangbinbin1993@outlook.com
 * @LastEditTime: 2026-11-05 15:15:06
 * @FilePath: /LLM-Inference-Benchmark/README.md
 * @Description: LLM推理服务压测自动化套件
 *
 * Copyright (c) 2026 by shangbinbin shangbinbin1993@outlook.com, All Rights Reserved.
-->

# LLM推理服务压测自动化套件

面向 LLM 推理服务的全链路压测，逐步覆盖压测客户端、推理服务指标、GPU、服务器、网络及日志观测。

当前处于参考资料整理和方案设计阶段，已收集监控链路配置与其他项目的压测场景；尚未提供自动化执行入口。

## 从这里开始

| 想做什么 | 入口 | 当前内容 |
| --- | --- | --- |
| 参考已有平台搭建监控与日志链路 | [平台配置参考](references/sglang-inference-platform/README.md) | Prometheus、Grafana、GPU/IB 监控、Alloy、Loki、logrotate |
| 研究其他项目如何设计压测场景 | [压测场景参考](references/benchmark-scenarios/README.md) | 南网、移动、招行方案中的推理测试场景与测试项清单 |
| 添加本项目实际使用的配置 | [配置约定](configs/README.md) | 待按本项目环境适配，目前仅有目录说明 |
| 了解目录划分、迁移位置与后续扩展 | [仓库结构说明](docs/repository-layout.md) | 文件归属、旧路径映射和新增文件约定 |

## 目录职责

| 路径 | 用途 |
| --- | --- |
| `README.md` | 项目简介和导航 |
| `docs/` | 本项目的结构说明，后续存放方案、操作指南和指标口径 |
| `configs/` | 本项目实际使用、可复现的配置 |
| `references/sglang-inference-platform/` | 从已有推理平台复制的配置、Dashboard、镜像构建文件及配套说明 |
| `references/benchmark-scenarios/` | 其他项目的测试要求、场景清单及分析材料 |

参考配置中的节点地址、Service、命名空间、挂载路径和账号配置属于来源环境。使用时先依据[配置约定](configs/README.md)适配到本项目，再记录验证结果。场景清单中的待澄清项应确认后再转为执行配置。
