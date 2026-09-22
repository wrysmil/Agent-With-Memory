---
artifact: spec
route: superpowers:brainstorming
skills:
  - brainstorming
  - source-driven-development
skills_evidence:
  - C:/Users/Huangqh/.claude/skills/brainstorming/SKILL.md
  - .claude/skills/source-driven-development/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - harness-kit/project.profile.md
  - .ai-runtime-artifacts/stack/2026-09-17-stack-recall-parity.md
  - .ai-runtime-artifacts/specs/2026-09-16-vector-retrieval-spec.md
  - .ai-runtime-artifacts/research/2026-09-17-openakita-recall-parity-and-fts5-tokenizer-research.md
  - https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion
  - https://qdrant.tech/documentation/concepts/hybrid-queries/
created_at: 2026-09-17
status: draft
approved: false
topic: memory-recall-parity
---

# 方案：记忆召回对齐（情节通道激活 + 中文 FTS 逐词 + RRF 融合）

> 本 spec 只描述**目标 / 设计 / 接口 / 边界 / 验收**，不写实现代码。
> 范围：`nanobot/memory/retrieval/` 的**召回层**。向量层、reranker 权重公式、前端均不在范围。

## 0. 证据基础（Step 0 结果）

### 0.1 已读产物

| 产物 | 用途 |
| --- | --- |
| `research/2026-09-17-openakita-recall-parity-and-fts5-tokenizer-research.md` | 主要依据（openakita 对照 + 分词器实测，本会话产出） |
| `specs/2026-09-16-vector-retrieval-spec.md` | 上游 spec；本方案的 RRF 是其 §3.3 已登记的 **v2 方案**，风险 R-4 亦由其登记 |
| `stack/2026-09-17-stack-recall-parity.md` | 零新依赖判定 |
| `harness-kit/project.profile.md` | 验收命令、交付口径 |

**无产物冲突。** 本方案**提前兑现** `2026-09-16-vector-retrieval-spec.md` §3.3 的 v2 备选，理由见 §4.3。

### 0.2 三份实测证据（本机，2026-09-17）

**证据 A：情节通道是死通道**（`channels/episodes.py:15-23` 只认路径/文件名）

```
_extract_query_entities('健身视频脚本怎么写')  -> []
_extract_query_entities('看看 nanobot/x/db.py')  -> ['/x/db.py', 'db.py']
```

而库里数据是好的——补上关键词兜底即可召回：

```
search_episodes(entity='健身视频')  -> 3 条（首条即最相关的健身视频 episode）
search_episodes(entity='创作')      -> 3 条
```

**证据 B：中文 FTS 逐词可命中，整串不可**（`repository.py:586` 用整串作 LIKE 模式）

```
repository.search_semantic_scored(conn, '健身视频脚本怎么写') -> 0 条
repository.search_semantic_scored(conn, '健身视频')          -> 1 条
repository.search_semantic_scored(conn, '创作')              -> 3 条
```

FTS5 侧同样：`unicode61` 把连续汉字整段当一个 token，`MATCH '健身视频' -> 0`。
（分词器方案对比见 §4.2，结论：**不换分词器、不加依赖**。）

**证据 C：现融合策略的量纲缺陷是活的**

`store_adapter.py:69-89` 用 `max(fts5_score, vector_score)` 融合，而 LIKE 兜底
（`repository.py:738-743`）打的是 `_pseudo_bm25_score(idx) = 1/(1+idx)`——**枚举下标，首名恒 1.0**。

模拟（真实数据）：

```
向量路:  a6da7565=0.706(第1)  cc066841=0.406(第3)
LIKE路:  cc066841(第1)        a6da7565(第2)
max 融合后:  cc066841=1.000 反超 a6da7565=0.706
```

这正是 `search_semantic_scored` docstring（`repository.py:701-705`）记录的
「向量接上了但排序毫无变化」。

## 1. 问题陈述

| # | 问题 | 影响 |
| --- | --- | --- |
| P1 | 情节通道仅对「含路径/文件名」的查询触发 | 普通对话中该通道等于死代码，7 条 episode 永不参与召回 |
| P2 | 中文 FTS/LIKE 用整串匹配 | 任何多词中文查询 FTS 侧恒零命中，全靠向量路单腿走路 |
| P3 | `max()` 融合要求两路同量纲，而 LIKE 侧分数与相关性无关 | 一旦 LIKE 开始命中（P2 修复的直接后果），首位被无关结果以 1.0 占据 |

> 🔴 **P3 是 P2 修复的必然后果，不是独立问题。** 放宽 LIKE 命中面会让「首名恒 1.0」从偶发变常态，因此
> 本方案必须同时处理融合策略，否则修完 P2 反而会更糟。

## 2. 目标与非目标

### 目标

- **G1**：情节通道在普通中文对话中能基于关键词召回 episode。
- **G2**：中文多词查询在 FTS/LIKE 侧能逐词命中。
- **G3**：融合策略不再依赖两路分数同量纲；修复 P3。

### 非目标（明确不做）

- ❌ **不引入 jieba 或任何分词依赖**（依据：`specs/2026-09-16-vector-retrieval-spec.md` §4.2
  「核心安装不拉重依赖」边界 + `stack/2026-09-17` 判定）。
- ❌ **不改 `reranker.py` 的权重公式**（`0.4×Rel + 0.2×Rec + 0.2×Imp + 0.2×Access`）。
- ⚠️ **`_MIN_COMPOSITE = 0.35` 的阈值不在本方案中预设调整**：RRF 会改变 relevance 分布，
  是否需调**由 §7 V4 实测结果决定**。若实测判定需调，作为本方案的收尾项执行，不另开 spec。
- ❌ 不改向量层、formatter、前端、`channels/recent.py`、`channels/attachments.py`。
- ❌ 不做 `recent._RELEVANCE_MISS` 归零（用户已明确排除，属独立缺陷）。

## 3. 设计

### 3.1 数据流

```
engine.retrieve_with_ids(query, recent_messages)
  │
  │  keywords = QueryDecomposer.decompose(...).keywords      ← 已有，此前未下沉
  │
  ├─► search_semantic(store, query, keywords, limit, compute_recency)   ★ 增参
  │     └─► MemoryStoreAdapter.search_semantic_scored(query, keywords=…)
  │           ├─ FTS5 路:  MATCH "t1" OR "t2" OR …                      ★ 逐词
  │           ├─ LIKE 兜底: 逐词 OR，ORDER BY 命中词数 DESC, importance DESC  ★ 新增
  │           └─ ★ RRF 融合 → 绝对归一化 → relevance                      ★ 替换 max()
  │
  ├─► search_episodes(store, query, keywords, limit, compute_recency)   ★ 增参
  │     └─ entities = 路径/文件名(原逻辑) + keywords                    ★ 激活死通道
  │
  ├─► search_recent(...)        不动
  └─► search_attachments(...)   不动

  dedupe → rerank → format → 注入块      全部不动
```

### 3.2 改动 ①：语义通道逐词 FTS/LIKE

**接口**（`store_adapter.py`）：

```python
def search_semantic_scored(
    self, query: str, *, keywords: list[str] | None = None, limit: int = 30
) -> list[tuple[Memory, float]]:
```

**FTS5 路**——逐词加双引号成短语查询后 OR：

```
match_expr = ' OR '.join(f'"{t}"' for t in terms)     # "健身视频" OR "脚本怎么"
```

双引号包裹的理由：`"` `*` `(` `)` `:` `^` `~` 等 FTS5 特殊字符被中性化，
配合既有的 `_is_fts_syntax_error`（`repository.py:545-557`）保持原有容错语义。

**LIKE 兜底**（FTS5 零命中时触发，非取代）：

```sql
SELECT <cols>,
       (CASE WHEN content LIKE ? ESCAPE '\' THEN 1 ELSE 0 END
      + CASE WHEN content LIKE ? ESCAPE '\' THEN 1 ELSE 0 END) AS _hits
FROM memories
WHERE (content LIKE ? ESCAPE '\' OR content LIKE ? ESCAPE '\')
ORDER BY _hits DESC, importance_score DESC
```

★ **排序键由 `importance_score DESC` 改为 `_hits DESC, importance_score DESC`**。

理由：RRF 只消费**名次**，因此 LIKE 路的内部顺序**必须有相关性含义**。
沿用「按重要度排序」等于把一个与查询无关的顺序当相关性喂给 RRF——这会让 P3 以新形态复活。

**terms 的构造**（`_search_terms`）：

```
terms = [query] + keywords        # query 恒在首位；去重、去空、保留原顺序
```

- `query` **恒被包含**：即便关键词拆解质量差，原始查询仍有一条独立通路，
  保证「新机制不会让任何现有查询变差」。
- `keywords` 为空（decomposer 降级失败）时自然退化为 `[query]`。

**★ 一条重要推论：RRF 只看名次，所以分数函数不用动**

RRF 的输入是**有序列表**，不消费分数。因此：

| 原本打算改的 | 实际是否要改 |
| --- | --- |
| `_pseudo_bm25_score`（LIKE 的 `1/(1+idx)`，首名恒 1.0） | **不用改**——它的值不再参与融合 |
| FTS5 侧的页内 min-max 归一化（`repository.py:727-736`） | **不用改**——同理 |
| LIKE 的 `ORDER BY importance_score DESC` | **要改** → `_hits DESC, importance DESC` |

即：P3 的解法不是「把分数算对」，而是**让分数退出融合**——由 RRF 改用名次。
唯一仍需修的是 LIKE 的**排序键**，因为名次必须携带相关性含义。

> 遗留：`_pseudo_bm25_score` 与页内 min-max 在 RRF 下成为纯排序辅助（值无意义）。
> 是否清理属独立改动，本方案**不动**，仅在代码注释中标注。

### 3.3 改动 ②：情节通道关键词兜底

```python
def search_episodes(store, *, query, keywords, limit, compute_recency):
    entities = _extract_query_entities(query)      # 路径/文件名，原逻辑保留
    for kw in keywords:                            # ★ 新增兜底
        if kw and kw not in entities:
            entities.append(kw)
    for entity in entities[:3]:                    # 取前 3，与 openakita 一致
        ...
```

其余**全部沿用**：relevance 固定 `0.6`、`repository.search_episodes` 的 LIKE 匹配、
`compute_recency` 用 `ended_at`。本改动**只补「拿什么当实体」**。

依据（openakita `memory/retrieval.py:945-953`）：其三段实体抽取的最后一段
`words = [w for w in query.split() if len(w) > 2]` 正是此兜底。nanobot 缺的只有这一段。

### 3.4 改动 ③：RRF 融合

替换 `store_adapter.py:69-89` 的 `max()` 融合：

```python
K = 60                       # Elasticsearch 默认 rank_constant
RRF_MAX = 2.0 / (K + 1)      # 两路皆第 1 名的理论上界 ≈ 0.032787

rrf: dict[str, float] = {}
for rank, (mem, _score) in enumerate(fts5_hits, start=1):
    rrf[mem.id] = rrf.get(mem.id, 0.0) + 1.0 / (K + rank)
for rank, (mid, _score) in enumerate(vector_hits, start=1):
    rrf[str(mid)] = rrf.get(str(mid), 0.0) + 1.0 / (K + rank)

relevance = min(1.0, rrf[mid] / RRF_MAX)     # 绝对归一化
```

**绝对归一化 vs 页内 min-max**（本方案选前者）：

| 情形 | RRF 原值 | 绝对归一化 | 页内 min-max |
| --- | --- | --- | --- |
| 两路都第 1 | 0.03279 | **1.000** | 1.000 |
| 只单路第 1 | 0.01639 | **0.500** | **1.000** ← 退回老坑 |
| 只单路第 50 | 0.00909 | **0.277** | 0.000 |

页内 min-max 在**单条结果**时 `span <= 0`，现有实现直接给 `1.0`
（`repository.py:734`）——正是 P3 的同一形态。绝对归一化以常量 `RRF_MAX` 为分母，
**不含页内相对性**，故无此病。

## 4. 关键决策与取舍

### 4.1 关键词用显式参数下沉（而非 openakita 式增强查询）

| 方案 | 取舍 |
| --- | --- |
| **显式 `keywords` 参数** ← 选 | 契约显式、可单测；向量路仍用原 query（不被拼接词稀释）；不依赖空格拆分这个隐式约定 |
| openakita 式 `query + " ".join(keywords)` | 改动面最小，但依赖 `query.split()` 取词（脆），且向量会收到拼接串 |

### 4.2 不换 FTS5 分词器

| 方案 | 中文 | 2 字词 | 依赖 | 结论 |
| --- | --- | --- | --- | --- |
| `unicode61`（现状） | 整段才匹配 | ✗ | 无 | 逐词 OR 后可绕过 |
| `trigram` | ≥3 字可匹配 | **✗** | 无 | 实测 `创作`/`游泳`/`脚本` 全零 |
| `icu` | 好 | ✓ | ICU 扩展，发行版普遍不带 | FTS5 官方文档未列为核心 tokenizer |
| 自定义 tokenizer | 好 | ✓ | **仅 C API** | Python 不可行 |
| jieba 预分词 | 好 | ✓ | +15MB 纯 Python | 违反「核心不拉重依赖」边界 |

官方依据：https://sqlite.org/fts5.html —
「Substrings consisting of fewer than 3 unicode characters do not match any rows」（trigram 限制）。

### 4.3 融合策略提前上 RRF（而非继续 `max()`）

**权威依据**（本会话抓取原文）：

| 来源 | 关键结论 |
| --- | --- |
| [Elasticsearch · RRF](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/reciprocal-rank-fusion) | `score += 1.0/(k + rank)`；`rank_constant` **默认 60**；「the different relevance indicators **do not have to be related to each other**」「**RRF requires no tuning**」 |
| [Qdrant · Hybrid Queries](https://qdrant.tech/documentation/concepts/hybrid-queries/) | 「a fixed alpha over raw scores tends to be **dominated by whichever retriever has larger raw magnitudes**… **RRF sidesteps this by using ranks**」；无评估集时 RRF 是「the safe default」 |

**与本项目 spec 的关系**：`specs/2026-09-16-vector-retrieval-spec.md` §3.3 已将 RRF 登记为
v2 备选（理由「工业界标准」），§8.1 R-4 登记了量纲混排风险。当时的决策是
「v1 先用 `max()` 拿到端到端可用，v2 备选 RRF」。

**本次提前的理由**：`max()` 的缺陷（P3）在改动 ①（放宽 LIKE 命中面）之后会**从偶发变常态**。
即「先上 v1 攒经验」的前提已不成立——不换融合策略，改动 ① 就是净负收益。

**已知代价**（spec §3.3 原文登记）：`relevance` 语义由「相似度」变为「RRF 归一化共识分」，
`reranker._MIN_COMPOSITE = 0.35` 需重新校准 → 见 §7 的 **V4 实测项**。

## 5. 错误处理与边界

| 边界 | 处理 | 依据 |
| --- | --- | --- |
| `keywords` 为空 / decomposer 降级失败 | `terms = [query]`，行为等同现状 | 不得因拆解失败使检索变差 |
| term 含 FTS5 特殊字符 | 逐词双引号包裹 + 保留 `_is_fts_syntax_error` 兜底 | `repository.py:545-557` |
| term 含 LIKE 元字符（`%` `_` `\`） | 沿用 `_escape_like`（`repository.py:532-539`） | 安全审查 M-4 |
| FTS5 与 LIKE 均零命中 | `fts5_hits = []`，RRF 仅累计向量路 | 自然降级 |
| 向量 store 未就绪 / 报错 | `vector_hits = []`，RRF 仅累计 FTS 路 | 现有 try/except 语义保留（D4） |
| 两路均零命中 | 返回 `[]`，engine 走「无候选」 | 现状 |
| 单条候选 | `relevance = rrf / RRF_MAX`，**不为 1.0** | §3.4 |
| `RRF_MAX` 除零 | 常量 `2/(K+1)`，不依赖数据 | — |

**不新增异常传播路径**：所有既有 `try/except` 原样保留。

## 6. 测试策略

单测落 `tests/memory/retrieval/`：

| 用例 | 断言 |
| --- | --- |
| RRF 融合语义 | 两路共识 > 单路高分（用证据 C 的真实数据作 fixture） |
| 绝对归一化 | **单条候选的 relevance 不得等于 1.0** |
| RRF 名次无关性 | 把某路分数整体 ×10，RRF 结果**不变**（这是 RRF 的核心不变量） |
| LIKE 逐词 OR | 多词命中时 `_hits DESC` 排序键生效 |
| terms 构造 | `keywords` 为空 → 退化为 `[query]` |
| FTS5 OR 表达式 | 含 `"` `*` `(` 的 term 不抛语法错 |
| 情节通道兜底 | `keywords=['健身视频']` 能召回（对齐证据 A 的实测 3 条） |
| 情节通道不回归 | 纯路径查询行为不变 |

## 7. 验收标准

```bash
pytest tests/memory/ -q                 # 全绿
ruff check nanobot/                     # 匹配 CI
uv run --no-sync basedpyright           # strict
nanobot gateway                         # 涉及检索装配，须确认可启动
```

**实测项 V4（🔴 spec 上游已登记，本方案必须复测）**

`reranker._MIN_COMPOSITE = 0.35` 在新 relevance 分布下是否仍合理。

- 旧：单路向量命中 relevance ≈ 0.706 → 相关项贡献 `0.4×0.706 = 0.282`
- 新：单路命中 relevance = 0.500 → 相关项贡献 `0.4×0.500 = 0.200`
- 推论：**可能有更多条目落到 0.35 阈值之下**，即召回面收窄

→ 须在真实记忆库上实测量：分布、落阈条目数、注入块是否变空。
**阈值是否调整由实测结果决定，不在本方案内预设。**

**端到端观测**：gateway 日志须显示中文查询在 FTS 侧不再恒零，且情节通道计数非零。

## 8. 风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| R-1 | RRF 归一化后 relevance 分布下移，触发 V4 阈值问题 | §7 V4 实测；必要时调 `_MIN_COMPOSITE` |
| R-2 | 情节通道激活后引入噪音 episode（relevance 固定 0.6） | 取前 3 限制；0.6 是既有口径，开放 | 
| R-3 | 逐词 OR 放大召回面，弱相关条目变多 | RRF 的共识机制天然抑制单路弱命中；V4 一并观测 |
| R-4 | FTS5 双引号短语查询在极短 term 上行为变化 | 单测覆盖特殊字符；语法错误有既有兜底 |
| R-5 | 情节通道从「几乎不跑」变「每轮都跑」，性能影响 | episodes 表规模小（本机 7 条）+ LIKE 有 LIMIT；V4 观测耗时 |

## 9. 交付边界

**改动文件**（预计 4 个源码文件 + 1-2 个测试文件）：

```
nanobot/memory/retrieval/engine.py                    # keywords 下沉
nanobot/memory/retrieval/channels/semantic.py         # 透传 keywords
nanobot/memory/retrieval/channels/episodes.py         # 关键词兜底
nanobot/memory/retrieval/store_adapter.py             # RRF 融合 + keywords 透传
nanobot/memory/repository.py                          # 逐词 FTS/LIKE + _hits 排序
tests/memory/retrieval/*                              # §6 用例
```

**不改**：`reranker.py`、`formatter.py`、`vector/*`、`channels/recent.py`、
`channels/attachments.py`、前端、`pyproject.toml`（零新依赖）。

## Next

**（写入后须暂停，等用户明确继续 — 见 `harness-kit/core/routing.md` § 阶段门禁）**

- 确认方案无误 → 说「写计划」或「制定实施计划」
- 变更范围小、无需计划 → 说「直接实现」或「直接做」
- 需要调整方案 → 直接说修改意见
