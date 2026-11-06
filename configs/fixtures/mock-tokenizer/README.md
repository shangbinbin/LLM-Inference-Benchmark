# 模拟专用 tokenizer

256 项 WordLevel 词表，用于 Mac 流式端点及 AISBench 汇总的功能验证。`A` 为一个 token，另含 254 个普通词与 `[UNK]`。没有模型权重、训练能力或真实模型性能含义。生产 `storage.tokenizer` 必须使用被测模型的真实 tokenizer；不要复用此词表。
