---
artifact: document-review
route: superpowers:document-review
skills:
  - document-review
skills_evidence:
  - harness-kit/.agents/skills/document-review/SKILL.md
  - harness-kit/.agents/skills/document-review/review-rules/plan.md
  - harness-kit/.agents/skills/document-review/checklists/review-checklist.md
subject: .ai-runtime-artifacts/plans/2026-09-11-phase3-memory-retrieval-plan.md
created_at: 2026-09-11
reviewer: leader (self-review)
status: passed-with-gaps
---

# 阶段三记忆检索实施方案 — 文档审查报告

## 文档类型

**实施计划（Plan）** — 信号词：「实施方案」「Task」「GROUP」「TDD 强制」「dispatch」。

## 审查规则加载

- [x] 通用审查流程（`harness-kit/.agents/skills/document-review/SKILL.md`）
- [x] 文档类型特定规则：`review-rules/plan.md`（实施计划 6 维度）
- [x] 通用清单：`checklists/review-checklist.md`（基础/内容/清晰度/环境/可执行/向后兼容）

## 审查结果

### 1. 阶段结构 — **基本完整（1 项缺口）**

| 检查项 | 结果 |
| --- | --- |
| 阶段划分清晰、依赖关系明确 | ✅ 5 个 GROUP（A/B 并行，C 串行，D 串行，E 用户补充）依赖显式标注 |
| 每个阶段有可验证的完成标准 | ✅ 每 Task 的 Step 4「跑绿」即阶段完成标准 |
| 阶段粒度合理 | ✅ 每 Task 5 步、单步 2-5 分钟 |
| **Phase 1 必须是环境准备** | ⚠️ **缺口** — 计划无独立「环境前置」Phase；只在「关键约束」隐含说明「Phase 1+2 已交付」作为环境基线。审查规则要求**显式声明** |

> **建议**：在 Goal 之后追加「## 环境前置」段（≤15 行）：明确 pytest 基线命令、`uv sync` 验证、T-14 用到的 ToolLoader 接口假定 nanobot>=某版本。

### 2. 任务粒度 — **完整**

- ✅ 所有 14 个 Task（T-01..T-15，跳 T-09）Step 1..5 结构完整
- ✅ 文件路径精确到 `nanobot/memory/retrieval/channels/episodes.py:919` 级别
- ✅ 每个 Task 都有 pytest 验证命令 + 预期输出
- ✅ 测试代码覆盖 happy path / edge / error（Gate 四类 + 反注入 + 缓存命中 + LLM 失败兜底）

### 3. 环境准备完整性 — **不完整（3 项缺口）**

| 检查项 | 结果 |
| --- | --- |
| 依赖安装步骤完整（含版本号、安装命令、锁文件） | ⚠️ Tech Stack 表仅列名、**缺版本号与 `uv add` 命令** |
| 环境变量配置步骤完整 | N/A（无外部 env） |
| 外部服务配置完整 | N/A（无外部服务） |
| 环境验证命令可执行（一键验证） | ⚠️ **缺一键验证命令**（如 `uv sync && pytest tests/memory/test_database.py -q`） |
| 平台差异说明 | ⚠️ **缺平台差异** — T-06 `_PATH_RE` 写 `[A-Za-z]:\\` 是 Windows 风格；macOS/Linux 用 POSIX `/path/to/file.py`，但 T-06 同时给了 `/[^\s\"']+\.\w{1,5}\b` 兼容。**plan 没说**。 |

> **建议**：Tech Stack 表补版本号（如 `pytest>=8.0`、`pytest-asyncio>=0.23`、`jieba>=0.42`），环境前置段补 `bash harness-kit/scripts/install-ai-skills.sh` 一键验证、平台差异段标注 `Windows / POSIX` 双路径正则并存。

### 4. 测试计划 — **基本完整（2 项缺口）**

| 检查项 | 结果 |
| --- | --- |
| 单元测试覆盖核心逻辑（TDD） | ✅ 14 个 Task 全 TDD |
| 集成测试覆盖关键流程 | ✅ T-13（build_system_prompt）+ T-09（未来增强） |
| 测试数据准备方案（fixture/seed/mock） | ⚠️ **缺统一 fixture 约定** — 测试中大量 `SimpleNamespace` inline 创建，没有 `tests/memory/retrieval/conftest.py` 集中 mock 模板 |
| 测试环境与生产环境差异已标注 | ⚠️ **缺** — 应注明「FTS5 + LIKE 测试用 sqlite3 in-memory / tmp_path；生产 db 是 nanobot workspace 路径」 |
| 测试覆盖率目标明确 | ⚠️ **缺** — 无 `pytest --cov` 目标（如 80% 行覆盖） |

> **建议**：在验收口径追加 `pytest --cov=nanobot/memory/retrieval --cov-fail-under=85`；在 File Structure 段加 `tests/memory/retrieval/conftest.py`。

### 5. 风险与回滚 — **不完整（1 项严重缺口）**

| 检查项 | 结果 |
| --- | --- |
| 高风险任务已标注 | ✅（T-04 chromadb、T-13 system prompt 大小、T-14 自动发现） |
| **每个阶段有回滚方案** | ❌ **严重缺口** — plan 完全无回滚段。审查规则明示「数据迁移、架构变更」需回滚 |
| 关键任务有备选方案（Plan B） | ✅ T-04 chromadb 缺依赖 → 回退 FTS5；T-05/T-12 search_semantic_scored 双实现路径 |
| 变更影响范围已预估 | ⚠️ 部分 — 未量化（如「影响 system prompt +500 tokens」无数据） |

> **建议**：在「风险与权衡」表后追加「## 回滚方案」段：每 GROUP 一个回滚路径（git revert + feature flag 默认 opt-in + 数据库 schema 迁移兼容旧版）。

### 6. 时间与资源 — **缺失（2 项缺口）**

| 检查项 | 结果 |
| --- | --- |
| 任务有预估耗时（量级级别即可） | ❌ **缺** — 完全无 |
| 可并行任务已标注 | ✅ |
| 阻塞性依赖已高亮 | ✅ |
| 外部依赖交期已确认 | N/A |

> **建议**：任务汇总表追加「预估」列：T-01（10 分钟）/ T-12（30 分钟，含 asyncio.gather）/ T-13（45 分钟，跨 context + loop）/ T-14（20 分钟）等。

### 7. 可执行性 — **基本完整（1 项内部冲突）**

| 检查项 | 结果 |
| --- | --- |
| 新成员可独立执行 | ✅ |
| 无「待定」占位 | ⚠️ T-04 Step 3 注释提到「T-04.5（增强）」、T-12 提到「T-14 Phase 3.5 真实 bm25 列」——这些是**未来扩展**，写法上像未完成项 |
| 无「视情况而定」模糊分支 | ⚠️ **T-05 与 T-12 之间 `search_semantic_scored` 实现存在冲突** |
| 外部依赖交期已确认 | N/A |

**内部冲突详情**：

| 位置 | 内容 |
| --- | --- |
| T-05 Step 3 | `def search_semantic_scored(...): raise NotImplementedError("T-05: 实际实现由 RetrievalEngine 注入 backend 完成")` |
| T-12 Step 3 | `def search_semantic_scored(conn, query, *, limit=30): results = search_memories(conn, query=query, limit=limit); ...`（伪 rank 实现） |

两个 Task 都声称要写 `repository.search_semantic_scored`，但给出了**互相矛盾**的实现方案。执行 coder 看到会困惑「到底 T-05 写 raise 还是写伪实现？」

> **建议**：明确单一所有权。建议改 T-12 为「**仅修改 repository，删 T-05 的 stub**」；或者 T-05 不写 stub 改为「声明接口契约」，T-12 写真实实现。

### 8. 一致性自查

| 检查项 | 结果 |
| --- | --- |
| 类型一致性 | ✅ `RetrievalCandidate` 字段在 T-01/05..08/10/11 一致 |
| 方法签名一致性 | ✅ `engine.retrieve(query=, recent_messages=, max_tokens=)` 在 T-12/T-13/T-14 一致 |
| **Task 编号一致性** | ⚠️ **小错位** — File Structure 表 line 80 把 `__init__.py` 标 T-09，但 T-09 段落（line 1225）声明「嵌入 T-12，不单独建 Task」。同一文档中 T-09 既存在又不存在。 |
| T-09 段落格式 | ⚠️ T-09 段落无 Step 1..5，仅一个「决议声明」——读者会困惑为什么这个 Task 没代码 |

### 9. 总体评分

| 维度 | 评分 |
| --- | --- |
| 文档完整性 | **基本完整** — 编号小错位 + T-12/T-05 实现冲突 |
| 逻辑清晰度 | **清晰** — Goal/Architecture/File Structure/Tasks/Risk/Acceptance 层次分明 |
| 环境准备完整性 | **不完整** — 缺 Tech Stack 版本号 + 平台差异 + 一键验证 |
| 测试计划 | **基本完整** — 缺覆盖率目标 + conftest 文档 |
| 风险与回滚 | **不完整** — 缺回滚方案（严重） |
| 时间与资源 | **缺失** — 无耗时预估 |
| 可执行性 | **基本完整** — T-05/T-12 实现冲突（中等） |

**总体：通过（含 7 项缺口 + 2 项小错位）** — 计划主体质量良好、可执行；缺口主要在环境元信息、回滚、预估、覆盖目标等「计划文档元数据」层面，**不阻塞实现**。

## 缺失项清单（按优先级排序）

| 优先级 | 缺失项 | 建议修复 Task |
| --- | --- | --- |
| **P0**（影响执行） | T-05 与 T-12 `search_semantic_scored` 实现冲突 | 决定单一所有权，删一保留一 |
| **P0**（影响执行） | T-09 编号错位（File Structure 标 T-09、T-09 段落声明不单独建 Task） | 删 File Structure 表的 T-09 列或补 T-09 段落为完整 Task |
| **P1**（审查规则硬要求） | 缺「环境前置」Phase 段 | 在 Goal 后追加 ≤15 行段 |
| **P1**（审查规则硬要求） | 缺回滚方案 | 在「风险与权衡」后追加「## 回滚方案」段 |
| **P1**（审查规则硬要求） | Tech Stack 表缺版本号 + 平台差异 + 一键验证命令 | Tech Stack 表补版本号；新增「## 平台差异说明」短段；环境前置段加 `uv sync` 一键命令 |
| **P2**（提升质量） | 缺测试覆盖率目标 + conftest 约定 | 验收口径加 `pytest --cov-fail-under=85`；File Structure 加 `conftest.py` |
| **P2**（提升质量） | 缺耗时预估 | 任务汇总表加「预估」列 |
| **P3**（润色） | T-04/T-12 提到「T-04.5」「T-14 Phase 3.5」未来扩展——措辞改「未来增强（不在本 plan）」 | 注释改写 |

## 改进建议（具体修改片段）

### 建议 1：在 Goal 之后插入「## 环境前置」段

```markdown
## 环境前置

**前置条件**（已在 Phase 1+2 验证）：
- Python 3.11+ / uv 已安装
- `uv sync --all-extras --dev` 成功
- 测试基线：`pytest tests/memory/ tests/agent/ -q` → 345+ passed

**本阶段额外依赖**（不引入新外部包）：
- `pytest>=8.0`、`pytest-asyncio>=0.23`（已在 pyproject.toml）
- `jieba>=0.42`（可选，默认 off）
- `chromadb` / `httpx`（可选，缺依赖自动回退 FTS5）

**一键环境验证**：
\`\`\`bash
uv sync && pytest tests/memory/test_database.py tests/agent/test_loop_wiring.py -q
\`\`\`
预期：`2+ passed`，无新增 fail。

**平台差异**：
- Windows 路径正则：`[A-Za-z]:\\[^\s\"\']+`
- POSIX 路径正则：`/[^\s\"\']+\.\w{1,5}\b`
- T-06 `_PATH_RE` 已**双正则并存**，macOS/Linux/Windows 均兼容。
```

### 建议 2：在「风险与权衡」后追加「## 回滚方案」

```markdown
## 回滚方案

| GROUP | 回滚命令 | 数据兼容性 |
| --- | --- | --- |
| A | `git revert <commit-A>` | 无 DB schema 变更，安全 |
| B | `git revert <commit-B>` | `Episode.compaction_checkpoint_id` 字段 nullable，回滚后旧版读取忽略 |
| C | `git revert <commit-C>` | 无 |
| D | `git revert <commit-D>` + `active_retrieval_enabled=False` 默认配置 | context.py / loop.py 兼容旧版 |
| E | `git revert <commit-E>` + 删除 identity.md 追加行 | Tool 注册回退不影响其他工具 |

**Feature flag 默认 opt-in**：T-13 的 `active_retrieval_enabled=False` 是兜底开关——
即便所有 WU 已合并，生产默认关闭，出问题时无需回滚代码，仅关 flag。
```

### 建议 3：消除 T-05 / T-12 实现冲突

```markdown
**决议**：T-05 仅在 channels/semantic.py 中实现通道逻辑；`repository.search_semantic_scored` 的真实实现统一在 T-12 写。

T-05 Step 3 删除 `raise NotImplementedError` 段；改为：
> 「本 Task 不修改 repository.py；接口契约由 T-12 落地。」
```

### 建议 4：File Structure 表删 T-09 错位

```markdown
<!-- File Structure line 80 改 -->
| `nanobot/memory/retrieval/__init__.py` | 包入口，导出 `RetrievalEngine / RetrievalCandidate` | T-12 |
```

### 建议 5：任务汇总表加耗时预估列

```markdown
| GROUP | Task | 标题 | 预估 | 依赖 |
| --- | --- | --- | --- | --- |
| A | T-01 | RetrievalCandidate dataclass | 10 分钟 | 无 |
| A | T-02 | MemoryQueryPreprocessor | 15 分钟 | 无 |
| A | T-03 | QueryDecomposer | 25 分钟 | 无 |
| A | T-04 | search_backend 工厂 + FTS5 | 20 分钟 | 无 |
| B | T-05..T-08 | 4 通道 | 各 15 分钟 | T-04 |
| C | T-10 | Reranker | 20 分钟 | 无 |
| C | T-11 | Formatter | 15 分钟 | 无 |
| D | T-12 | RetrievalEngine 编排器 | 30 分钟 | A+B+C |
| D | T-13 | 集成 build_system_prompt | 45 分钟 | T-12 |
| E | T-14 | MemorySearchTool | 20 分钟 | T-12 |
| E | T-15 | identity.md 模板告知 | 5 分钟 | T-14 |
```

## Next

- **审查结果**：通过（含 7 项缺口 + 2 项小错位）——不阻塞「开始实现」
- **建议路径 A（推荐）**：用户在本会话说「接受审查 + 开始实现」 → Leader 直接按 plan 派 GROUP-A；审查改进项作为 issue 留待后续派 review-fix WU
- **建议路径 B**：用户在本会话说「先补齐缺口」 → Leader 修订 plan（追加建议 1~5 段落），完成后再进派发
- **建议路径 C**：用户只接受 P0 修正（消歧 + 编号错位） → 立即修复 2 项 → 通过

请选择 A / B / C。