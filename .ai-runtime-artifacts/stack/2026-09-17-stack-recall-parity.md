---
artifact: stack-detection
route: source-driven-development
skills:
  - source-driven-development
skills_evidence:
  - .claude/skills/source-driven-development/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - pyproject.toml
  - harness-kit/project.profile.md
  - .ai-runtime-artifacts/stack/2026-09-16-stack.md
  - .ai-runtime-artifacts/specs/2026-09-16-vector-retrieval-spec.md
  - .ai-runtime-artifacts/research/2026-09-17-openakita-recall-parity-and-fts5-tokenizer-research.md
created_at: 2026-09-17
topic: memory-recall-parity
---

# STACK DETECTION（增量）：记忆召回对齐

> 检测日期：2026-09-17 ｜ 分支：`feature/memory-system`
> 基线：`.ai-runtime-artifacts/stack/2026-09-16-stack.md`（向量层依赖，**仍然有效**）
> 本文件只记**本次任务的增量**：是否引入新依赖。

## 一、复用既有结论（不重复检测）

| 项 | 值 | 出处 |
| --- | --- | --- |
| 包名 / 版本 | `nanobot-ai` **0.3.0** | `pyproject.toml:2-3` |
| 语言基线 | `requires-python >= 3.11` | `pyproject.toml:6` |
| 行宽 / lint | 100；`ruff` E,F,I,N,W（忽略 E501） | `pyproject.toml:189-193` |
| 类型检查 | `basedpyright` strict | `pyproject.toml:195-198` |
| 测试 | pytest，`asyncio_mode=auto`，覆盖率下限 75% | `pyproject.toml:200-212` |
| 本机 SQLite | **3.47.1** | 实测 `sqlite3.sqlite_version` |
| 本机 Python | 3.13.13（CI 基线 3.11） | 2026-09-16 stack |

## 二、本次改动涉及的模块（全部为**已有**代码，无新增依赖面）

| 文件 | 本次用途 |
| --- | --- |
| `nanobot/memory/retrieval/channels/episodes.py` | 补关键词当实体的兜底 |
| `nanobot/memory/retrieval/engine.py` | 关键词下沉到通道 |
| `nanobot/memory/repository.py` | FTS/LIKE 逐词 OR |
| `nanobot/memory/retrieval/channels/recent.py` | `_RELEVANCE_MISS` 归零 |
| `nanobot/memory/retrieval/reranker.py` | 相关分地板 / 权重（待定） |

**全部只用标准库 + 既有依赖（`sqlite3` / `loguru` / `pydantic`）**，不触碰 `pyproject.toml`。

## 三、🔴 关键判定：**不引入 jieba**

### 3.1 现状

```
$ python -c "import importlib.util as u; print(u.find_spec('jieba'))"
None                      ← 未安装

$ grep -n "dependencies" -A 30 pyproject.toml
（无任何分词库；核心依赖 28 项，均为轻量库）
```

候选分词库可用性实测：

| 库 | 状态 | 备注 |
| --- | --- | --- |
| `jieba` | **未装** | openakita 的选择；纯 Python ~15MB |
| `pkuseg` / `thulac` / `hanlp` | 未装 | 均需额外模型权重 |
| `regex` | 已装 | 非分词器 |

### 3.2 为什么不加

1. **违反既有边界纪律**。`specs/2026-09-16-vector-retrieval-spec.md` §4.2 立下「**核心安装不拉重依赖**」，
   并据此把 chromadb/torch 放进 `vector` extra。jieba 若进核心 `dependencies` 直接破这条线；
   若放 extra，则 FTS5 中文能力变成**默认不可用**——失去意义。
2. **收益不必要**。调研已证实：nanobot 的 LIKE 兜底**本来就能命中中文短词**（`'健身视频'`→1 条、`'创作'`→3 条），
   失效根因是**模式用了整串**（`repository.py:586`），不是缺分词器。
3. **已有等价物**。`QueryDecomposer` 已产出 `keywords`（实测 `['健身视频','脚本怎么']`），
   当前只是**没传给通道**。复用它 = 零新依赖达成同样效果。

### 3.3 结论

> 走「关键词下沉」路线，**不动依赖树**。
> 若未来仍要 jieba，应作为独立决策走 `decisions/`，并重新评估 extra 划分。

## 四、外部文档依据

| 主题 | 来源 | 关键结论 |
| --- | --- | --- |
| FTS5 tokenizer | https://sqlite.org/fts5.html | `unicode61` 连续 token 字符 = 一个 token；`trigram` 明文限制「< 3 unicode 字符不匹配任何行」；`icu` 未在核心文档出现；自定义 tokenizer **仅 C API** |
| openakita 实现 | `src/openakita/memory/search_backends.py:124-139` | jieba 预分词（`cut_for_search` → 空格拼接） |
| openakita LIKE 兜底 | `src/openakita/memory/storage.py:1545-1551` | 按空格拆词后逐词 `OR` |

## 五、验收命令（沿用 project.profile.md）

```bash
pytest tests/memory/ -q                 # 必须全绿
ruff check nanobot/                     # 匹配 CI
uv run --no-sync basedpyright
nanobot gateway                         # 涉及检索装配，须确认可启动
```
