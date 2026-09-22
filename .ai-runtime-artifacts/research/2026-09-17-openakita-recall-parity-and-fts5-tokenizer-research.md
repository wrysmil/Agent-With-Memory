# 记忆召回对齐调研：情节通道触发条件 + FTS5 中文分词

- 日期：2026-09-17
- 范围：`nanobot/memory/retrieval/`（四路召回）对照 `openakita/src/openakita/memory/`
- 性质：调研，**未改代码**
- 触发：用户实测发现「情节通道恒 0 条」「FTS5 中文恒 0 条」，要求参考 openakita 并调研分词器

---

## 0. 结论摘要

| 问题 | 结论 | 修复成本 |
|------|------|---------|
| 情节通道为何恒 0 | **缺一个关键词兜底**。openakita 把拆解关键词当实体去搜 episode，nanobot 只认路径/文件名 | 低（~10 行） |
| FTS5 为何中文恒 0 | `unicode61` 把连续汉字整段当一个 token | — |
| 是否必须换分词器 | **不必**。nanobot 的 LIKE 兜底其实能命中中文，失效另有原因（见 §3.3） | 低 |
| 若仍要换分词器 | `trigram` 只能救 ≥3 字查询，2 字常用词（创作/游泳）仍失效；`icu`/自定义均不可行 | — |

---

## 1. 情节通道（episode）：什么时候该检索

### 1.1 openakita 的实现

`openakita/src/openakita/memory/retrieval.py:438-475` + `:945-953`

```python
def _search_episodes(self, query: str, limit: int = 5) -> list[RetrievalCandidate]:
    entities = self._extract_query_entities(query)
    ...

def _extract_query_entities(self, query: str) -> list[str]:
    entities = []
    for m in re.finditer(r'[A-Za-z]:[\\\/][^\s"\']+', query):      # Windows 路径
        entities.append(m.group(0))
    for m in re.finditer(r"[\w-]+\.(?:py|js|ts|md|json|yaml|toml)\b", query):  # 文件名
        entities.append(m.group(0))
    words = [w for w in query.split() if len(w) > 2]               # ★ 关键词兜底
    entities.extend(words[:5])
    return entities
```

**★ 那一行是分水岭。** 因为 openakita 在检索前先 `_build_enhanced_query`：

```python
def _build_enhanced_query(self, query, recent_messages=None, search_keywords=None):
    parts = [query]
    if search_keywords:
        for kw in search_keywords:
            if kw not in query:
                parts.append(kw)        # 关键词以【空格】拼接进查询串
    ...
    return " ".join(parts)
```

于是 `query.split()` 恰好切出 LLM/规则拆解出的关键词，它们被当作实体去 `LIKE` 匹配 episode。

### 1.2 nanobot 的实现

`nanobot/memory/retrieval/channels/episodes.py:10-23`

```python
_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+|/[^\s\"']+\.\w{1,5}\b")
_EXT_RE  = re.compile(r"\b[\w一-鿿\-]+\.\w{1,5}\b")

def _extract_query_entities(query: str) -> list[str]:
    entities = []
    for pat in (_PATH_RE, _EXT_RE):
        for m in pat.finditer(query):
            ...
    return entities          # ← 没有 query.split() 兜底
```

且 `engine.py:174-184` 传给通道的是 `prepared.cleaned_query`（**未与关键词拼接**），所以即使补了 `split()` 也切不出关键词——需要连 `_build_enhanced_query` 一并移植。

### 1.3 实测：补上兜底能召回吗？

用 nanobot 真实库（episodes 表 7 条）验证 `repository.search_episodes(entity=...)`：

```
entity='健身视频'  -> 3 条  ['用户希望 nanobot 持续提供健身视', ...]   ← 高度相关！
entity='创作'      -> 3 条
entity='游泳'      -> 1 条
entity='脚本怎么'   -> 0 条
```

而当前 `_extract_query_entities('健身视频脚本怎么写')` 返回 **`[]`** → 永不查询。

**结论：数据是好的，通道没去查。** 补上关键词兜底即可召回那条最相关的 episode
「用户希望 nanobot 持续提供健身视频创作灵感，包括选题、脚本构思、内容方向等方面的…」。

### 1.4 回答「要不要检索」

按 openakita 的口径：**要**，但触发条件是「query（含拆解关键词）里出现 ≥3 字符的词」，
而不是「必须出现文件路径」。当前 nanobot 的设计把 episode 通道窄化成了「文件相关查询专用」，
在普通对话里等于死通道。

---

## 2. FTS5 中文分词：实测

### 2.1 unicode61 的真实行为

建临时表 `tokenize='unicode61 remove_diacritics 2'`，用 `fts5vocab` 打出真实 token：

```
doc: '用户热爱创作'              ← 一整段是一个 token
doc: '希望ai主动提供创作灵感'      ← 连 "AI" 都被吞进同一 token
doc: '用户的创作方向与健身视频相关'  ← 一整段是一个 token
doc: '选题' / '脚本' / '剪辑'      ← 被顿号括号隔开的才是独立 token
```

`MATCH` 探测：

```
MATCH '创作'                        -> 0 条   ← 库里没有裸 '创作'
MATCH '健身视频'                     -> 0 条
MATCH '用户的创作方向与健身视频相关'    -> 1 条   ← 整段一字不差才行
MATCH '脚本'                        -> 1 条   ← 恰好被标点隔开
```

官方文档印证（https://sqlite.org/fts5.html）：token 字符的**连续串构成一个 token**，CJK 不做词切分。

### 2.2 四个候选方案对比

| 方案 | 中文效果 | 2 字词 | 依赖 | 判据 |
|------|---------|-------|------|------|
| `unicode61`（现状） | 仅整段匹配 | ✗ | 无 | 已实测 |
| `trigram` | ≥3 字子串可匹配 | **✗** | 无（SQLite 3.34+ 内置） | 已实测 |
| `icu` | 好 | ✓ | ICU 扩展，**发行版普遍不带** | FTS5 官方文档**未提及** |
| 自定义 tokenizer | 好 | ✓ | 仅 C API（需 `fts5.h` + C 扩展） | 官方文档只给 C 路径，Py 不可行 |
| **jieba 预分词** | 好 | ✓ | 纯 Python ~15MB | openakita 已采用 |

`trigram` 实测（本机 SQLite 3.47.1）：

```
MATCH '健身视频'  -> 1 条   ✓ 4 字可命中
MATCH '创作'     -> 0 条   ✗ 2 字不可
MATCH '游泳'     -> 0 条   ✗
MATCH '脚本'     -> 0 条   ✗
```

官方原文："Substrings consisting of fewer than 3 unicode characters do not match any rows."
**中文常用词大量是 2 字**（创作/游泳/脚本/灵感），trigram 救不了。

### 2.3 openakita 的做法：jieba 预分词

`openakita/src/openakita/memory/search_backends.py:124-139`

```python
def _segment(self, text: str) -> str:
    """jieba 中文分词, 回退到原文"""
    ...
    return " ".join(self._jieba.cut_for_search(text))
```

写入与查询**都**先经 jieba → 空格分隔 → `unicode61` 就能正确切词。
零 C 依赖，`jieba` 是纯 Python。

---

## 3. 意外发现：nanobot 的 LIKE 兜底其实能命中中文

### 3.1 兜底确实存在

`nanobot/memory/repository.py:738-743`（FTS5 零结果时）

```python
return [
    (mem, _pseudo_bm25_score(float(idx)))
    for idx, mem in enumerate(
        _search_memories_like(conn, query, ...)
    )
]
```

其 docstring 已自述该问题（`:568-583`）：
> ``memories_fts`` 使用 ``tokenize='unicode61'``…对连续 CJK 不做分词，整串是一个 token。
> 因此任何**中文子串**查询（「创作」「记忆」）都命中不了 FTS5。

### 3.2 实测：短词能命中，长句不能

走真实检索路径 `repository.search_semantic_scored`：

```
'健身视频脚本怎么写'  -> 0 条     ← 用户实际查询
'健身视频'          -> 1 条     ← 短词能命中！
'创作'             -> 3 条     ← 短词能命中！
'健身 视频 脚本'      -> 0 条
```

### 3.3 根因：LIKE 用的是**整串**

`repository.py:586`

```python
params = [f"%{_escape_like(query)}%"]      # 整个 query 当一个 LIKE 模式
```

`%健身视频脚本怎么写%` 自然匹配不到任何行。**不是 LIKE 不行，是模式太长。**

对照 openakita 的 LIKE 兜底（`storage.py:1545-1551`）：

```python
# Fallback: LIKE search for CJK text that FTS5 unicode61 can't tokenize
keywords = query.strip().split()                          # ← 按空格拆
like_conditions = " OR ".join(["content LIKE ?"] * len(keywords))
like_params = [f"%{kw}%" for kw in keywords]              # ← 逐词 OR
```

它按空格拆词再 OR —— 而它的查询串**已被 jieba 分词成空格分隔**，所以拆出来就是有意义的词。

### 3.4 这意味着

nanobot 要在中文上达到 openakita 的效果，**两条路都通向「先把查询切成词」**：

- 走 jieba：写入侧 + 查询侧都分词 → FTS5 本身就能工作
- 走关键词：把已有的 `QueryDecomposer` 拆出的 keywords 传进 FTS/LIKE 并 OR →
  不必引入 jieba，复用手头的拆解结果

**后者成本明显更低**，因为 `engine.py` 已经算出了 `keywords`（实测为 `['健身视频','脚本怎么']`），
只是没有传给语义通道——目前语义通道只吃 `prepared.cleaned_query`。

---

## 4. 建议改动清单（未实施，待决策）

| # | 改动 | 位置 | 价值 | 成本 |
|---|------|------|------|------|
| 1 | 关键词当 episode 实体（移植 openakita 的 `words[:5]` + enhanced query） | `channels/episodes.py` | 激活死通道 | 低 |
| 2 | 语义通道接受 keywords，FTS/LIKE 逐词 OR | `engine.py` + `repository.py` | 中文 FTS 立即可用 | 中 |
| 3 | 近因通道 `_RELEVANCE_MISS` 0.2 → 0.0 | `channels/recent.py:12` | 去掉凭空垫分 | 极低 |
| 4 | 检索加相关分地板 / 重配权重 | `reranker.py` | 修「非相关项单独过阈值」 | 中 |
| 5 | （备选）jieba 预分词 | `search_backend.py` + schema 写入侧 | 最接近 openakita | 高（需重建索引） |

其中 **1 与 2 是本次调研直接指向的修复**；3、4 是此前发现的独立缺陷。

---

## 5. 复现命令

```bash
# 情节通道实体抽取（返回 [] 即复现）
python -c "from nanobot.memory.retrieval.channels.episodes import _extract_query_entities as f; print(f('健身视频脚本怎么写'))"

# FTS5 分词行为
python -c "
import sqlite3; c=sqlite3.connect(':memory:')
c.execute(\"CREATE VIRTUAL TABLE t USING fts5(content, tokenize='unicode61 remove_diacritics 2')\")
c.execute('INSERT INTO t(rowid,content) VALUES (1,?)', ('用户的创作方向与健身视频相关',))
c.execute(\"CREATE VIRTUAL TABLE v USING fts5vocab(t,'row')\")
print(list(c.execute('SELECT term FROM v')))"

# 真实库检索路径
python -c "
from nanobot.config.loader import load_config; from nanobot.webui.memory_services import MemoryServices
from nanobot.memory import repository; from nanobot.agent.loop import _MEMORY_WORKSPACE_ID
cfg=load_config(); svc=MemoryServices.for_workspace(_MEMORY_WORKSPACE_ID, cfg.workspace_path)
with svc.database.connect() as c:
    for q in ['健身视频脚本怎么写','健身视频']:
        print(q, len(repository.search_semantic_scored(c,q,limit=10)))"
```
