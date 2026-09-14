# 记忆系统：抽取模型独立配置 + 文字化记忆管理方案

> 日期：2026-09-14
> 类型：Feature Spec
> 状态：草稿
> 关联分支：feature/memory-system
> 关联背景：与 Leader 讨论「记忆提取是否需要强大模型」后的两路优化点

---

## 1. 背景与目标

### 1.1 当前问题（来自调研）

#### A. 抽取模型与对话模型强耦合

| 现象 | 影响 |
|------|------|
| `MemoryExtractor`（T1/S1/T5 等所有 SQLite 通道抽取）直接读 `self.runtime.provider`，跟会话 runtime 绑死 | 用户为了节省 token 用便宜模型开聊，但抽取仍跑贵模型 |
| 仅 `Dream` 有 `dream.model_override` 一个独立模型入口 | 实时抽取 / Dream / T5 judge 三类能力需求差距大却共用 |
| T5 话题切换检测每跑一次都按会话模型扣费 | 输出仅一个 `same_topic: bool`，价值与价格严重不匹配 |
| 无 fallback 链 | 抽取失败仅 logger.warning，用户前端完全感知不到 |

#### B. 记忆管理界面与英文 schema 强耦合

| 现象 | 影响 |
|------|------|
| `MemoryListView` 等记忆子页面 i18n 只接了导航名 `settings.nav.memory`，子页面文案全走 fallback 永远英文 | zh-CN 用户看到的还是 "Semantic memory / Episode / Scratchpad" |
| `MemoryEditDialog` 的 type 下拉是 `fact/preference/skill/error/rule/experience` 英文枚举 | 中文用户读不懂；schema 不能改成中文（破坏 FTS5 与契约），但 UI 可以本地化显示 |
| Episode 后端 `memory_api.update_episode` 已实现 `summary/goal/tags/importance_score` 修改，前端只挂 Delete + View | 用户无法编辑一条抽取出来的情节摘要 |
| `merge_profile_incremental` 已写但未接入主流程 | 用户没有「标记为过时/废弃」路径，错误记忆只能 Delete + 重输 |
| Dream prompt 是英文（`nanobot/templates/agent/dream.md`）且无 UI 可视化 | 中文用户调不动 Dream 行为；`/dream-prompt` 只展示路径不展示内容 |
| 用户只能靠 LLM 抽，不能"对话式告诉 agent 记住 X" | 添加记忆没有"显式入口" |

### 1.2 优化目标

#### A. 记忆模型独立配置

1. **解耦**：让记忆抽取（含 T5 / SessionEnd / Dream）的 LLM 调用与会话模型解耦，按能力梯度分层
2. **降本**：高频廉价判断（T5 话题切换）默认走最便宜的预设
3. **兜底**：抽取失败时按用户配置的兜底链重试，并在 UI 暴露状态
4. **可控**：所有新配置可在 Settings → Models 面板调整，UX 与现有模型预设一致

#### B. 文字化记忆管理

1. **本地化**：记忆管理子页面（MemorySection）全 i18n 中文化，含 type/priority 枚举 UI 映射
2. **补齐 Episode 编辑 UI**：复用 MemoryEditDialog 模式
3. **自然语言入口**：让用户能用中文"快速添加"记忆 + chat 中加"📌 记住"按钮
4. **纠正/废弃语义**：给 memory 加 `status` 字段，UI 支持标记 superseded / deprecated
5. **Dream 可视化**：在 UI 查看当前 prompt 内容、支持复制到工作区 / 查看历史 diff

---

## 2. 范围

### 2.1 In Scope

- 后端 Pydantic schema：新增 `MemoryExtractionConfig`
- 后端 runtime 解析：MemoryExtractor / SessionEndOrchestrator / TopicChange 改走新配置
- 后端 fallback 链：抽取失败按顺序重试，UI 暴露最近一次抽取状态
- 前端 ModelsSettings：新增"记忆抽取专用模型"子区块（复用 ModelControls）
- 前端 SettingsPayload TS 类型同步
- 前端 i18n：10 个 `common.json` 补 `settings.memory.*` 完整键组
- 前端 MemoryListView / MemoryEditDialog：中文化、type/priority 枚举本地化
- 前端 EpisodeListView：补 Edit 按钮 + EpisodeEditDialog
- 前端 MemorySection：加"快速添加记忆"对话框 + chat composer "📌 记住"按钮
- 前端：Dream prompt 可视化（只读预览 + 复制模板 + 查看 diff 链接）
- 数据库：SQLite 加 `memories.status` 列，启动时迁移

### 2.2 Out of Scope

- 改 SQLite 列名 / type 枚举值（破坏 FTS5 与代码契约，NOT chosen）
- 改默认 Dream prompt 模板（仅做可视化，不主动翻译）
- 抽取本身的算法 / 合并逻辑深度改进（`merge_profile_incremental` 接入列入但仅做最小接入）
- 离线 / 移动端支持
- 多语言 prompt 自动翻译

---

## 3. 现状摘要

### 3.1 后端

- **3 种 SQLite 记忆**：Semantic（append-only + dedup，无 merge）、Episode（append-only，后端 update 已实现前端未挂 Edit）、Scratchpad（覆盖式 + merge）
- **抽取 prompt**：`nanobot/memory/prompts.py` 4 个常量全是中文指令，字段名英文
- **Dream prompt**：`nanobot/templates/agent/dream.md` 英文；独立 runtime（已有 `dream.model_override`）
- **写入路径**：`MemoryExtractor._persist` → `repository.add_memory/add_episode/upsert_scratchpad` → SQLite `workspace/memory/state.db`；失败 fallback 到 `_memory_fallback/*.json`
- **抽取 LLM runtime**：`self.runtime.provider` 直接绑死会话 runtime（`nanobot/memory/extractor.py:902,1271`）
- **schema 字段**：`agents.defaults.dream: DreamConfig`（已存在）；**未存在** `extraction_model` / `topic_judge_model`

### 3.2 前端

- **记忆 UI**：Settings → 记忆，三 tab（semantic / episode / scratchpad），6 个组件均已实现
  - `webui/src/components/settings/memory/MemorySection.tsx`
  - `MemoryListView.tsx` / `MemoryEditDialog.tsx`（Semantic 增改删）
  - `EpisodeListView.tsx` / `EpisodeDetailPanel.tsx`（仅 Delete + View）
  - `ScratchpadEditor.tsx`（Markdown + 4 列表，800ms 防抖）
- **模型 UI**：Settings → 模型，`ModelsSettings`（预设列表 + 行内编辑器） + `ProvidersSettings` + 共享 `ModelControls`
  - 用户**不能直编辑** `agents.defaults.model` / `fallback_models`，用 `model_call_order` 隐式表达
- **API**：全部 `/api/settings/...`，后端 handler 在 `nanobot/webui/{settings_api,settings_routes,memory_routes}.py`
- **i18n**：i18next + react-i18next 已接，10 种 locale，`common.json` 各 ~1579 行
  - **仅有 `settings.nav.memory`**，**无 `settings.memory.*` 子键** → 记忆子页面永远 fallback 英文硬编码

---

## 4. 方案要点

### 4.1 块 A：记忆模型独立配置（后端 schema）

#### 4.1.1 新增 schema 字段

`nanobot/config/schema.py`：

```python
class MemoryExtractionConfig(Base):
    """记忆抽取专用 LLM 配置。None/空表示跟随会话 runtime。"""

    enabled: bool = True
    model_preset: str | None = Field(
        default=None,
        validation_alias=AliasChoices("modelPreset", "model_preset"),
    )  # 主抽取模型（Semantic / Episode / SessionEnd）
    topic_judge_model_preset: str | None = Field(
        default=None,
        validation_alias=AliasChoices("topicJudgeModelPreset", "topic_judge_model_preset"),
    )  # T5 话题切换专用（默认同主模型，可降级）
    fallback_models: list[str] = Field(default_factory=list)  # 兜底链
    temperature: float | None = None  # 覆盖 default 0.1
    max_tokens: int | None = None     # 覆盖 default 8192
```

挂到 `AgentDefaults`：

```python
memory_extraction: MemoryExtractionConfig = Field(default_factory=MemoryExtractionConfig)
```

`_validate_model_preset`（schema.py:454）扩展校验三个新字段都指向已存在的 preset。

#### 4.1.2 runtime 解析优先级

新增 helper `nanobot/utils/memory_runtime.py`：

```python
def resolve_extraction_runtime(
    config: Config,
    session_runtime: LLMRuntime,
) -> tuple[LLMRuntime, LLMRuntime | None]:
    """返回 (extraction_runtime, topic_judge_runtime)；
    任意字段为空时 fallback 到 session_runtime。
    """
```

调用点替换：

| 调用方 | 文件 | 当前行为 | 改后行为 |
|--------|------|----------|----------|
| MemoryExtractor 构造 | `nanobot/agent/loop.py:529` `_build_extractor` | 用 session runtime | 用 extraction_runtime |
| T5 judge | `nanobot/agent/hooks/memory_extraction.py:441` | 用 `self._runtime` (注入的 session runtime) | 用 topic_judge_runtime（_runtime_provider 在 factory 里注入） |
| Dream runtime | `nanobot/agent/loop.py:265` `dream_runtime` | 已独立 | 不变（已支持 `dream.model_override`） |

`create_memory_extraction_hook_factory` 扩展 `runtime_provider` 签名，传入 topic_judge_runtime 而非 session runtime。

#### 4.1.3 兜底链

`MemoryExtractor._run_extraction` 包一层重试：

```
attempt = 1
for runtime in [extraction_runtime, *fallback_runtimes]:
    try:
        await runtime.provider.chat_with_retry(...)
        record_success(attempt)
        break
    except Exception:
        logger.warning("extraction attempt %d failed: %s", attempt, exc)
        attempt += 1
else:
    record_failure(all_attempts_failed)
```

`record_success/failure` 写到 `session.metadata["memory_extraction_status"]`，由 `runtime_event_publisher` 推给前端。前端可在 MemorySection 顶部加一个状态条。

### 4.2 块 B：前端模型配置 UI 改造

#### 4.2.1 ModelsSettings 新增「记忆抽取专用模型」子区块

位置：`webui/src/components/settings/models/MemoryExtractionSettings.tsx`（新文件）。

字段：
- **主模型**：`ModelPresetPicker`（复用 `webui/src/components/settings/shared/ModelControls.tsx`），选项含「跟随会话」+ 全部命名预设
- **T5 judge 模型**：同上，独立选项
- **兜底链**：复用 `model_call_order` 的拖拽排序组件（找 `ModelsSettings.tsx` 中已有的 `ReorderableList` / dnd-kit）
- **覆盖参数**：temperature / max_tokens 两个 input（数字 + None 清空）

`AgentSettingsDraft` 类型扩展：

```ts
export type AgentSettingsDraft = {
  // ... 既有字段
  memoryExtraction: {
    enabled: boolean;
    modelPreset: string | null;
    topicJudgeModelPreset: string | null;
    fallbackModels: string[];
    temperature: number | null;
    maxTokens: number | null;
  };
};
```

`SettingsPayload` 同步 + `settings_api.update_agent_settings` handler 透传字段。

#### 4.2.2 WebSocket mutation

`settings_routes.py` 的 `_SYSTEM_ROUTES` / `_SETTINGS_MUTATION_PATHS` 不动（字段已含在 `update_agent_settings` payload 里），只需 `settings_api.update_agent_settings` 写入 `agents.defaults.memory_extraction` 字段。

### 4.3 块 C：前端 i18n 补齐 + 文字化记忆管理

#### 4.3.1 i18n 补齐（10 个 `common.json`）

最小一组键（仅 `en` / `zh-CN` 必填，其他 fallback 到 `en`）：

```jsonc
"settings.memory": {
  "tabSemantic": "Semantic memory | 语义记忆",
  "tabEpisode": "Episodes | 情节",
  "tabScratchpad": "Scratchpad | 工作记忆",
  "type": { "fact": "Fact | 事实", "preference": "Preference | 偏好",
            "skill": "Skill | 技能", "error": "Error | 错误",
            "rule": "Rule | 规则", "experience": "Experience | 经验" },
  "priority": { "long_term": "Long-term | 长期", "short_term": "Short-term | 短期" },
  "status": { "active": "Active | 生效", "superseded": "Superseded | 已替代",
              "deprecated": "Deprecated | 已废弃" },
  "actions": { "newMemory": "New memory | 新建记忆",
              "quickAdd": "Quick add | 快速添加",
              "markSuperseded": "Mark as superseded | 标记为已替代",
              "restore": "Restore | 恢复" },
  "extractionStatus": {
    "lastSuccess": "Last extraction: {time} | 上次抽取：{time}",
    "lastFailed": "Last extraction failed | 上次抽取失败",
    "never": "No extraction yet | 尚未抽取"
  }
}
```

> 枚举值（type / priority / status 的 value）保持英文不翻译，只翻译 UI label。 翻译字段命名规范：`label` 走 i18n，`value`（数据库存储）保持英文。

#### 4.3.2 EpisodeListView 加 Edit 按钮

- 复用 `MemoryEditDialog` 模式抽 `EpisodeEditDialog.tsx`
- 字段：`summary / goal / tags / importance_score`
- 后端 `episode.update` 已实现（`nanobot/webui/memory_api.py`），只需前端挂按钮 + 调 `episode.update` mutation
- i18n 键：`settings.memory.episodeEdit.*`

#### 4.3.3 快速添加记忆（自然语言入口）

入口两处：

1. **MemorySection 顶部按钮** `+ 快速添加`：弹出一个简洁 Dialog，用户输入一段中文
2. **chat composer 右侧** `📌 记住` 按钮：把当前 input 直接转成记忆

后端：复用现有 `memory.create` mutation 路径，前端在 `payload` 里新增 `parse_with_llm: boolean` 字段。

后端 handler 增强（`memory_api.py memory-create`）：

```python
if payload.get("parse_with_llm"):
    # 用 extraction_runtime（或 session_runtime）跑一次轻量解析：
    # 输入：自然语言；输出：{ type, priority, content, subject, predicate, importance, tags }
    # 解析失败降级：type=RULE, priority=long_term, content=原文
    parsed = await _parse_natural_memory(payload["raw_text"], extraction_runtime)
    memory = parsed
else:
    memory = payload  # 旧路径不变
```

`parse_with_llm` 复用现有 prompt 体系（`prompts.py` 加一个 `NATURAL_MEMORY_PARSE_PROMPT`，中文指令）。

#### 4.3.4 status 字段（纠正/废弃语义）

SQLite 迁移：启动时检测 `memories` 表是否有 `status` 列，无则 `ALTER TABLE memories ADD COLUMN status TEXT NOT NULL DEFAULT 'active'`，写入迁移日志。

schema（`models.py`）：
```python
class Memory(Base):
    # ... 既有
    status: Literal["active", "superseded", "deprecated"] = "active"
```

repository 层：
- `list_memories` 默认 filter `status='active'`，传 `include_inactive=True` 返回全部
- 新方法 `update_memory_status(memory_id, status)`，权限校验：与现有 update_memory 一致

UI（MemoryListView 行尾）：
- 加 status badge（彩色圆点）
- 右键菜单 / hover 按钮组：标记 superseded / deprecated / restore to active
- 默认列表不显示 superseded / deprecated（折叠区"已归档 N 条"）

#### 4.3.5 Dream prompt 可视化

在 MemorySection 加一个 tab `dream`（或在 Settings → Models 加子区块"Dream 配置"），UI：

- 当前 prompt 路径（默认 `nanobot/templates/agent/dream.md` 或工作区覆盖 `<workspace>/dream.md`）
- 只读代码块预览（折叠 / 展开）
- 按钮组：「复制默认到工作区」（= `/dream-prompt init`）/「查看最近 diff」（= `/dream-log` 跳转或调新 API）/「恢复」（= `/dream-restore`）

后端：复用现有 `/dream-prompt`、`/dream-log`、`/dream-restore` 命令即可，前端只加调用入口。

---

## 5. 权衡与决策

| 议题 | 选项 | 决策 | 理由 |
|------|------|------|------|
| 抽取模型独立 vs 复用 | 独立 / 复用 / 双轨 | **独立（块 A）** | 能力梯度差距大；复用 = 浪费 |
| T5 judge 模型独立 | 独立 / 同主模型 | **独立但默认同主模型** | 给用户降本入口，不强制 |
| type 枚举改 UI vs 改 schema | UI 翻译 / schema 改中文 | **UI 翻译** | 改 schema 破坏 FTS5 索引和契约 |
| 快速添加解析用 LLM | LLM / 规则 / 都给 | **LLM（默认）+ 规则 fallback** | 准确度优先；规则兜底保零成本 |
| status 字段加列 vs 软删 | 加 status 列 / 物理删除 | **加 status 列** | 物理删除丢失历史，加列向后兼容 |
| Dream prompt 中文化 | 主动改 / 仅可视化 | **仅可视化** | 主动改可能影响现有用户行为 |
| fallback 链 UI 复杂度 | 拖拽排序 / 简单列表 | **复用 model_call_order 拖拽组件** | UX 一致 |

---

## 6. 验收口径

### 6.1 后端

1. `config.json` 设 `agents.defaults.memory_extraction.model_preset = "cheap"` 后重启，`MemoryExtractor` 日志显示使用 "cheap" 预设的模型
2. T5 话题切换日志显示使用 `topic_judge_model_preset`（若配置），否则回落到主模型
3. 抽取主模型 fail → 兜底链按顺序重试 → 全部失败仅 warning 不抛
4. `session.metadata["memory_extraction_status"]` 含 success/failure + timestamp，runtime_event_publisher 推送前端

### 6.2 前端

1. ModelsSettings 出现「记忆抽取专用模型」子区块，能配 主模型 / T5 judge / 兜底链 / temperature / max_tokens，保存后 `~/.nanobot/config.json` 实际写入
2. zh-CN 下 MemorySection 全中文，含 type / priority 下拉本地化
3. EpisodeListView 行尾出现 Edit 按钮，能编辑 summary/goal/tags/importance_score 并保存
4. MemoryListView 顶部出现「快速添加」按钮，输入「我喜欢喝美式咖啡」→ 入库 type=preference, content=我喜欢喝美式咖啡
5. chat composer 出现 📌 按钮，点击把当前 input 转成记忆
6. MemoryListView 行尾出现状态 badge 与「标记为已替代」操作，默认列表不显示已替代条目，折叠区显示 N 条
7. Settings → Dream 配置（或 MemorySection → Dream tab）能查看 prompt 内容、复制默认、查看最近 diff

### 6.3 兼容与迁移

1. 老 `config.json` 缺 `memory_extraction` 字段时启动不报错（Pydantic 默认值）
2. SQLite 缺 `status` 列时启动自动迁移
3. 老用户不开新功能时行为不变（`enabled=True` 但所有字段 `None` 时跟随会话 runtime）

---

## 7. 风险与依赖

| 风险 | 等级 | 缓解 |
|------|------|------|
| 独立模型 fallback 失败链配置不当导致体验更差 | 中 | UI 默认值兜底为空列表（保持现有行为）；失败仅 warning 不影响主流程 |
| 「快速添加」LLM 解析失败降级 RULE 类型太多导致数据库污染 | 中 | RULE 类型在 UI 高亮提示"自动推断"；用户可一键删除 |
| status 字段 DB 迁移阻塞启动 | 低 | 迁移包 try/except，失败仅 logger.warning 不阻塞 |
| i18n 漏键导致 fallback 英文 | 低 | CI 加 lint：扫所有 `t("settings.memory.*")` 是否在 en/zh-CN common.json 都存在 |
| 改 `AgentSettingsDraft` 类型破坏 TS 编译 | 低 | 同步改 `SettingsPayload` 与 settings_api handler，单测覆盖 |
| 抽 LLM 用便宜模型导致抽取质量下降 | 中 | 默认 `model_preset=None` 保持现有行为；用户主动改才生效；可加 UI 提示"建议用 ≥ Sonnet 档" |
| Dream prompt 可视化涉及 git 操作（diff / restore）走错命令 | 低 | 复用 `/dream-log` `/dream-restore` builtin，不新增命令 |

---

## 8. 后续动作

按 brainstorming skill 门禁：本 spec 经用户确认后 → 进入 `writing-plans` 阶段拆实施计划。

建议的 WU 拆分（不在本 spec 详写，待 plan 阶段敲定）：

| WU | wu_type | 主要文件 |
|----|---------|----------|
| 后端：MemoryExtractionConfig schema + runtime helper | feature | `nanobot/config/schema.py`, `nanobot/utils/memory_runtime.py` |
| 后端：MemoryExtractor 替换 runtime + fallback 重试 | feature | `nanobot/memory/extractor.py`, `nanobot/agent/loop.py`, `nanobot/agent/hooks/memory_extraction.py` |
| 后端：memory status 字段 + DB 迁移 + repository filter | feature | `nanobot/memory/models.py`, `nanobot/memory/repository.py`, `nanobot/memory/database.py` |
| 后端：NATURAL_MEMORY_PARSE_PROMPT + 快速添加 LLM 解析 | feature | `nanobot/memory/prompts.py`, `nanobot/webui/memory_api.py` |
| 前端：ModelsSettings 新增记忆抽取子区块 | ui | `webui/src/components/settings/models/MemoryExtractionSettings.tsx`, `AgentSettingsDraft`, `SettingsPayload` |
| 前端：i18n 补齐 10 个 common.json settings.memory.* | chore | `webui/src/i18n/locales/*/common.json` |
| 前端：MemoryListView / MemoryEditDialog 中文化 + status UI | ui | `webui/src/components/settings/memory/MemoryListView.tsx`, `MemoryEditDialog.tsx` |
| 前端：EpisodeListView Edit + EpisodeEditDialog | ui | `webui/src/components/settings/memory/EpisodeListView.tsx`, `EpisodeEditDialog.tsx` |
| 前端：快速添加 Dialog + chat composer 📌 按钮 | ui | `webui/src/components/settings/memory/MemorySection.tsx`, `webui/src/components/thread/Composer.tsx` |
| 前端：Dream prompt 可视化页 | ui | `webui/src/components/settings/memory/DreamPromptPanel.tsx`（或并入 ModelsSettings） |
| 测试：i18n 键完整性 lint | test | CI script |
| 测试：runtime fallback / status 过滤单测 | test | `tests/memory/...` |

---

## 9. 不确定 / 待用户确认

1. **「快速添加」是否必须走 LLM 解析？** 还是提供"简单模式"（强制 type=preference）以零成本？
2. **status 字段是否要做 UI 默认隐藏？还是折叠区显示？**
3. **Dream prompt 可视化放 MemorySection 还是 ModelsSettings？**（建议前者，与 Dream 同语义域）
4. **T5 judge 模型 UI 是否要给出"推荐：使用便宜模型"的提示？**
5. **chat composer "📌 记住" 按钮位置**（右侧 / 输入框内 / 长按菜单）？

请用户确认本 spec 与上述 5 项不确定项后，进入 writing-plans 拆实施。