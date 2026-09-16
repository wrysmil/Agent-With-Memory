---
artifact: verification
route: execution
plan: .ai-runtime-artifacts/plans/2026-09-16-vector-retrieval-plan.md
created_at: 2026-09-16
status: pass
scope: Task 2 (WU-02) 前置依赖
---

# Task 2 前置依赖验证

## 环境

| 项目 | 值 |
| --- | --- |
| torch | 2.14.0+cpu（`cuda False`） |
| chromadb | 1.5.9 |
| sentence-transformers | 6.0.1 |
| transformers | 5.17.0 |
| bge-small-zh-v1.5 | 已下载至 `~/.nanobot/models/bge-small-zh-v1.5/`（14 个文件） |

## 模型目录（14 个文件）

```
1_Pooling/
README.md
config.json
config_sentence_transformers.json
configuration.json
model.safetensors
modules.json
pytorch_model.bin
sentence_bert_config.json
special_tokens_map.json
tokenizer.json
tokenizer_config.json
vocab.txt
```

## 冒烟测试结果

```
dim = (1, 512)                        ← ✓ 维数正确（应为 512，不是 768/384）
distances = [[0.23947358131408691]]   ← ✓ cosine 距离远 < 1，语义相近
chromadb ok: 1.5.9
```

## 判据

| 判据 | 实际 | 结果 |
| --- | --- | --- |
| `dim == (1, 512)` | `(1, 512)` | **PASS** |
| distance 明显 < 1 | 0.239 | **PASS** |
| chromadb 往返正常 | 无异常 | **PASS** |

## 已知现象（非错误）

- `PermissionError: [WinError 32]` — Windows `TemporaryDirectory` 退出时清理冲突，
  不影响执行结果，不影响向量检索正确性
- `AttributeError: 'SentenceTransformer' object has no attribute 'model_name_or_path'` —
  sentence-transformers 6.x API 改名，不影响编码与 ChromaDB 往返

## 结论

Task 2 前置依赖全部满足，可以进入 WU-05~WU-07 向量核心开发。
