# 提案 spec v2：nanobot 记忆提取加强（深度版）

> 日期：2026-09-12
> 状态：**草稿 / 待用户确认**
> 输入调研：`.ai-runtime-artifacts/research/2026-09-12-openakita-source-survey-v2.md`
> 前置版本：`.ai-runtime-artifacts/specs/2026-09-12-openakita-nanobot-improvements.md`（v1, 4 项 P0/P1，本版本**重构覆盖**）
> 本 spec **仅描述目标 / 接口 / 约束 / 取舍**，不写实现代码

---

## 0. 背景与目标

### 0.1 现存诊断（基于 v2 调研）
| 病灶 | nanobot 现状 | OpenAkita 参考 | 关键差异 |
| --- | --- | --- | --- |
| **每轮 LLM 调用成本** | `after_run` 每轮 1 次 | `end_session` 集中编排 | 高频低质 |
| **会话级编排** | 无 | 4 任务依赖链 | 缺骨架 |
| **话题检测每轮都判** | 无预筛，无间隔 | 仅 IM + 4 轮门 | 缺廉价过滤 |
| **语义记忆单轨** | 单 prompt | 双轨（画像 + 经验）| 缺第二轨 |
| **Episode 抽取** | prompt 未接通 | ✅ | 缺接通 |
| **Scratchpad 重构** | 常量未接通 | ✅ | 缺接通 |
| **引用评分** | 无 | 同次 LLM 拼装 | 缺闭环 |
| **画像持续更新** | 每次重抽 | ✅ 增量覆盖 | 缺增量逻辑 |
| **超时丢累积** | 无累积 | 30s 清空 turns | 反向规避 |
| **fire-and-forget** | 无去重 | 同问题 | 必修节流 |

### 0.2 加强原则
1. **职责分离**：每轮即同步 vs 会话级深度提取（不混在一个调用里）
2. **依赖链显式**：Episode → Track1+Track2 → Scratchpad → 关联回填
3. **可降级**：每步独立 feature flag，单点失败不影响整体
4. **不破坏现有行为**：`extract_session` 兼容保留、4 个 hook 不变、单测不改

### 0.3 范围（用户已确认）
- ✅ 双轨提取 + 画像持续更新
- ✅ 话题检测加预筛 + 间隔节流
- ✅ 会话级 4 任务编排
- ❌ 多路检索增强（本轮不做）
- ❌ 关系图谱 / Relational（本轮不做）
- ❌ 长期巩固（本轮不做）

---

## 1. 总览：会话级提取编排器

### 1.1 新组件：`SessionEndOrchestrator`

```
SessionEndOrchestrator
├── step1: generate_episode(transcript, session_key)
├── step2a: extract_user_profile(transcript, episode_id, cited)        # 默认 ON
├── step2b: extract_experience(transcript, episode_id)                # 默认 OFF（feature flag）
├── step3: format_scratchpad_with_llm(scratchpad, episode)            # 默认 OFF（feature flag）
└── step4: link_relations(episode_id, memory_ids, turn_ids)
```

**触发来源（5 种，全部汇聚到同一个编排器入口）**：
| 触发 | 现状 | 加强后 |
| --- | --- | --- |
| `after_run` | 跑完整 `extract_session` | **降级为 T0 即时同步**（仅写 scratchpad） |
| 用户主动 close | 无 | **新增** SessionEndEvent 触发完整编排 |
| `idle_timeout` | 无 | **新增** 同上 |
| 进程退出 `on_finally` | 仅等 pending 收尾 | **新增** 兜底再触发一次（fire-and-forget） |
| 话题切换（IM） | 单次 `extract_session` | **保留 + 加节流**（≥60s 间隔 + topic_hash 去重）|

### 1.2 失败隔离矩阵

| 步骤 | 失败影响 | 兜底策略 |
| --- | --- | --- |
| Step 1 Episode | 后续无法关联 | 用 heuristic summary，episode 仍写库 |
| Step 2a 用户画像 | 画像缺失 | 跳过本步，记 `record_health_event` |
| Step 2b 任务经验 | 经验缺失 | 跳过本步，记事件 |
| Step 3 Scratchpad | 便签未更新 | 用 episode 摘要追加到 `近期进展` |
| Step 4 关联回填 | 跨表关联丢失 | 重试 1 次，仍失败则告警 |

### 1.3 总 LLM 调用成本变化

| 场景 | 当前 | 加强后 |
| --- | --- | --- |
| 每轮 `after_run` | 1 次 | **0 次**（T0 即同步） |
| 会话结束（默认配置） | — | **2 次**（Episode + Track1） |
| 会话结束（全开） | — | **4 次**（+ Track2 + Scratchpad） |
| 话题切换 | 1 次 | 1 次（保留 + 节流）|

**净变化**：高频用户从 N×1 降到 N×0，会话结束从 0 变 2~4。**总 LLM 调用次数显著降低，但每次语义更深度**。

---

## 2. S1 — 会话结束事件 + 编排器

### 2.1 现状
- nanobot 没有"会话结束"概念
- `after_run` 每轮都跑 T1 异步提取
- 真正关会话/进程退出没有专门触发

### 2.2 目标
1. 引入 `SessionEndEvent` 与 `SessionEndHook`
2. 把 `after_run` 降级为 T0 即时同步
3. `on_finally` 增加兜底触发

### 2.3 关键接口（仅描述）

```python
@dataclass
class SessionEndEvent:
    session_key: str
    reason: Literal["user_close", "idle_timeout", "process_shutdown", "channel_disconnect"]
    transcript: list[dict]
    emitted_at: datetime


class SessionEndHook(Protocol):
    async def on_session_end(self, event: SessionEndEvent) -> None: ...


class SessionEndOrchestrator:
    async def run(self, event: SessionEndEvent) -> None:
        # 按 1.1 顺序执行（异常隔离 + feature flag）
        ...
```

### 2.4 边界

- 进程退出场景：**fire-and-forget**，主进程不等待编排完成
- 用户主动 close：**带超时等待**（参考 `EXTRACTION_WAIT_TIMEOUT=5s`，可调）
- **幂等保护**：同一 session_key + reason 在 30s 内重复触发 → 跳过
- **进程退出兜底**与正常 close 不重复触发（用 `_pending_tasks` 登记保护）

### 2.5 验收
- [ ] 5 种触发源都能调到编排器
- [ ] 重复触发被幂等保护拦下
- [ ] 进程退出兜底不阻塞 main loop（<100ms 内返回）
- [ ] 现有 `MemoryExtractionHook.after_run` 行为不变（兼容）

---

## 3. S2 — Episode 抽取接通

### 3.1 现状
- `prompts.py:EPISODE_PROMPT` 已定义
- `MemoryExtractor` 未在 `_run_extraction` 链路调用 `generate_episode`
- `Episode` 模型未在提取链路写库

### 3.2 目标
在会话结束链路 **Step 1** 调 `generate_episode`，把 episode 写入数据库，**ep_id 传给后续步骤**形成关联。

### 3.3 关键接口

```python
class MemoryExtractor:
    async def generate_episode(
        self,
        transcript: list[dict],
        session_key: str,
        source: str = "session_end",
    ) -> Episode:
        # 内部：
        # 1. 调 EPISODE_PROMPT（LLM）
        # 2. 失败回退：_generate_fallback_summary
        # 3. 提取 ActionNode（参考 OpenAkita _extract_action_nodes）
        # 4. 返回 Episode（不入库，由 orchestrator 决定）
```

### 3.4 失败 / 边界
- LLM 抽取失败 → heuristic summary + 正则 entities fallback
- `tool_calls` 缺失 → entities 字段为空数组
- transcript 为空 → 返回 `None`，orchestrator 跳过本次会话

### 3.5 验收
- [ ] Episode 写入数据库（`episodes` 表已存在则用，否则新增）
- [ ] ActionNode 至少包含 `tool_name / timestamp`
- [ ] LLM 失败时仍能写入（heuristic 回退）
- [ ] transcript 为空时返回 None 不抛

---

## 4. S3 — 双轨提取 + 引用评分 + 画像持续更新

### 4.1 现状
- nanobot 单一 prompt（`MEMORY_EXTRACTION_PROMPT`）
- 无"任务经验"轨道
- 无"引用评分"
- 画像（用户级）每次重抽，无增量

### 4.2 目标

#### 4.2.1 双轨

| 轨道 | prompt | 筛选 | 默认 |
| --- | --- | --- | --- |
| **Track 1：用户画像** | 沿用 `MEMORY_EXTRACTION_PROMPT` | 全部 transcript | ON |
| **Track 2：任务经验** | 新增 `EXPERIENCE_EXTRACTION_PROMPT` | `assistant_turns >= 2` | OFF |

#### 4.2.2 引用评分（同次 LLM 拼装）

在 Track 1 的 LLM 调用里拼装 `CITATION_SCORING_SECTION`：

```json
{
  "memories": [...],
  "citation_scores": [
    {"memory_id": "xxx", "useful": true/false}
  ]
}
```

#### 4.2.3 画像持续更新

引入 **增量合并** 而不是每次覆盖：

| 来源 | 处理 |
| --- | --- |
| 新抽取的画像 | 走 `is_update / update_hint` 字段（OpenAkita V2 设计） |
| 已有画像 | 若 `is_update=true` + 命中同 subject+predicate → 增量更新（保留 importance 累加） |
| 冲突画像 | **保留**旧值 + 记录 `conflicts_with` 列表（避免覆盖错误） |

### 4.3 关键接口

```python
class MemoryExtractor:
    async def extract_user_profile(
        self,
        transcript: list[dict],
        episode_id: str,
        cited_memories: list[dict] | None,
    ) -> tuple[list[MemoryItem], list[CitationScore]]:
        # 内部调 EXTRACTION_PROMPT + 可选 CITATION_SCORING_SECTION

    async def extract_experience(
        self,
        transcript: list[dict],
        episode_id: str,
    ) -> list[MemoryItem]:
        # assistant_turns < 2 → 返回 []
```

### 4.4 失败 / 边界

- 任一轨道失败 → 不影响另一轨道
- **Track 2 默认 off**：feature flag `NANOBOT_MEMORY_S3_TRACK2_EXPERIENCE=0` 默认
- 引用评分失败 → items 正常返回，scores 留空数组
- 增量合并：subject+predicate 完全相同才更新；不同则新建（避免误合并）
- `conflicts_with` 上限 5 条 / 记忆（防止无限增长）

### 4.5 验收
- [ ] Track 1 输出 (items, scores) 双字段
- [ ] Track 2 默认禁用，flag 开启时执行
- [ ] 引用评分失败不阻塞 items 写入
- [ ] 已有画像被新画像命中 `is_update` 时按 subject+predicate 合并
- [ ] 冲突记忆保留旧值 + 记录 `conflicts_with`

---

## 5. S4 — Scratchpad LLM 重构

### 5.1 现状
- `SCRATCHPAD_FORMAT_PROMPT` 常量已定义
- 没有任何调用点接通

### 5.2 目标
会话结束链路 **Step 3** 调 `SCRATCHPAD_FORMAT_PROMPT` 重构便签本，输出 4 段 Markdown（≤2000 字符）：

```
## 当前项目
- ...
## 近期进展
- ...
## 未解决的问题
- ...
## 下一步
- ...
```

### 5.3 关键接口

```python
class ScratchpadWriter:
    async def format_with_llm(
        self,
        current_scratchpad: Scratchpad | None,
        episode: Episode,
    ) -> Scratchpad:
        # 内部：
        # 1. 拼 SCRATCHPAD_FORMAT_PROMPT（输入：旧便签本 + episode.summary）
        # 2. 解析 4 段 Markdown
        # 3. 超 2000 字符 → smart_truncate
        # 4. 失败回退：episode 摘要追加到"近期进展"
```

### 5.4 失败 / 边界
- 依赖 **Step 1 Episode 必须先成功**（用 `episode.summary`）
- LLM 失败 → 用 episode 摘要最小回退
- 输出 >2000 → 截断
- 默认 off（feature flag `NANOBOT_MEMORY_S4_SCRATCHPAD_REFORMAT=0`）

### 5.5 验收
- [ ] Step 3 在 Step 1 成功后才执行
- [ ] 输出 Markdown 包含 4 个标题
- [ ] 长度超 2000 触发截断
- [ ] LLM 失败时便签本仍可写回（最小回退）

---

## 6. S5 — 话题检测加预筛 + 间隔节流（用户勾选）

### 6.1 现状
- 每轮都进 `_detect_topic_change` 函数
- 仅有 `_next_check_count` 节流 LLM 调用
- 无前置廉价规则
- 无后台 fire-and-forget 去重

### 6.2 目标

#### 6.2.1 廉价预筛（在调 LLM 之前挡掉）

| 规则 | 实现 | 预期挡掉 |
| --- | --- | --- |
| **空消息 / 长度 < 5** | 直接 False | 误触 + 控制词 |
| **寒暄/控制词整句** | 复用 `nanobot/memory/intent.py` 的 `_CHAT_FULL` | 闲聊 |
| **CHAT 意图** | 复用 `IntentType.CHAT` 判定 | 闲聊 |
| **长度突变 < 30%** | 当前消息 vs 上一条字符数比 | 短追问 |
| **追问前缀** | 复用 `_FOLLOW_UP_CJK/EN` | 追问 |

**任一命中 → 直接返回 False（不调 LLM）**。

#### 6.2.2 后台任务最小间隔

| 维度 | 当前 | 加强 |
| --- | --- | --- |
| fire-and-forget 间隔 | 无 | **同一 session_key 60s 内不重复** |
| 去重键 | 无 | `session_key + topic_hash`（用首条用户消息前 50 字符 SHA1） |
| 超时不清空 | — | **永不主动清空 turns**（保留给下次会话级提取） |

### 6.3 关键接口

```python
class TopicChangeDetector:
    def cheap_prefilter(self, message: str, recent: list[str]) -> bool:
        # True = 放行调 LLM；False = 跳过

    def should_fire_extraction(self, session_key: str, topic_hash: str) -> bool:
        # True = 允许触发；False = 60s 内/同 topic_hash 已触发
```

### 6.4 边界
- 预筛全 False → 不调 LLM，节省 100% 成本
- 预筛命中 → 不算"话题切换"，下次轮还会再判
- 60s 间隔可调（`NANOBOT_MEMORY_TOPIC_MIN_INTERVAL_SEC=60`）
- 旧累积不清空的设计要求 transcript 走 db（避免内存堆积）

### 6.5 验收
- [ ] 长度 < 5 / 寒暄整句 / CHAT 意图 / 长度突变 / 追问前缀 任一命中不调 LLM
- [ ] 同一 session_key 60s 内不重复触发 fire-and-forget
- [ ] 同 topic_hash 60s 内不重复触发
- [ ] 现有 `_next_check_count` 行为不变（叠加在新机制上）

---

## 7. 全局约束

### 7.1 依赖链（强制）

```
会话结束 / 话题切换
   ↓
[1] generate_episode         (S2, P0)
   ↓ (ep_id)
[2a] extract_user_profile    (S3 Track 1, P0)  ← 同次 LLM 拼装引用评分
[2b] extract_experience      (S3 Track 2, P1, flag)
   ↓
[3] format_scratchpad_with_llm  (S4, P1, flag, 依赖 [1])
   ↓
[4] link_relations           (episode ↔ memories ↔ turns)
```

### 7.2 与现有代码的兼容

| 现有 | 加强后 |
| --- | --- |
| `MemoryExtractionHook.after_run` 调 `extract_session` | 保留兼容入口，**默认走降级 T0 同步**；`extract_session` 仍可在 flag 启用 |
| `MemoryExtractionHook` 4 个 hook | **行为不变**（仅 on_finally 增加兜底） |
| 现有单测 | **不改** |
| `_next_check_count` 节流 | **保留叠加**（与新 60s 间隔共存） |
| `extract_session` 接口 | 保留兼容路径 |

### 7.3 配置项（feature flag）

| Flag | 默认 | 含义 |
| --- | --- | --- |
| `NANOBOT_MEMORY_S1_SESSION_END` | **on** | 启用会话结束事件 |
| `NANOBOT_MEMORY_S3_TRACK2_EXPERIENCE` | **off** | Track 2 任务经验 |
| `NANOBOT_MEMORY_S4_SCRATCHPAD_REFORMAT` | **off** | Scratchpad LLM 重构 |
| `NANOBOT_MEMORY_TOPIC_MIN_INTERVAL_SEC` | **60** | 话题切换提取间隔 |
| `NANOBOT_MEMORY_TOPIC_PREFILTER` | **on** | 话题预筛总开关 |
| `NANOBOT_MEMORY_COMPAT_EXTRACT_SESSION` | **on** | 兼容旧 `extract_session` |

### 7.4 观测性

每个提取轨道独立日志 tag：
- `[S1:SessionEnd]`
- `[S2:Episode]`
- `[S3:Track1]` / `[S3:Track2]`
- `[S4:Scratchpad]`
- `[S5:TopicDetect]`

每步失败记录到 `record_health_event`（参考 OpenAkita），主聊天不受影响。

---

## 8. 验收口径

### 8.1 功能验收
- [ ] S1：5 种触发源能调到编排器，幂等保护生效
- [ ] S2：Episode 写入 + ActionNode 结构化
- [ ] S3：Track 1 输出 items + scores；Track 2 默认 off；增量合并走 `is_update`
- [ ] S4：SCRATCHPAD_FORMAT_PROMPT 在 Step 3 实际被调
- [ ] S5：预筛规则挡掉率 ≥ 30%（基于真实聊天统计）

### 8.2 性能验收
- [ ] 每轮 `after_run` 的 LLM 调用 = 0
- [ ] 会话结束的 LLM 调用次数 ≤ 4（Episode + Track1 + Track2 + Scratchpad）
- [ ] 话题切换的 fire-and-forget 60s 内不重复
- [ ] 预筛全 False 时 0 次 LLM 调用

### 8.3 回归验收
- [ ] 现有 `extract_session` 单测全过
- [ ] `MemoryExtractionHook` 4 个 hook 行为不变
- [ ] 进程退出时 pending 任务等待 5s 超时不变
- [ ] 现有 LLM 调用次数不增长（除非 flag 开启新轨道）

### 8.4 观测验收
- [ ] 5 个新日志 tag 在生产可定位
- [ ] `record_health_event` 5 类失败可监控

---

## 9. 待你拍板的事项

1. **S1 P0 确认**：会话结束事件是 P0（因为 S2/S4 依赖它）
2. **S3 Track 2 默认 off**：同意吗？
3. **S4 Scratchpad 重构默认 off**：同意吗？
4. **S5 预筛 5 条规则**：是否全部采纳？是否有额外想加的？
5. **兼容 `extract_session` 默认 on**：用 flag 控制新旧切换 OK 吗？
6. **优先级排期**：建议排期 P0 = S1+S2；P1 = S3+S4+S5。同意吗？

---

## 10. 与 v1 spec 的对照

| 维度 | v1 | v2 |
| --- | --- | --- |
| 项数 | 4 项 | 5 项（新增 S5 话题预筛+间隔）|
| 默认 off 数量 | 2（S3 Track2、S4） | 2（同上）|
| 画像持续更新 | ❌ 未提 | ✅ 纳入 S3 |
| 引用评分 | ❌ 未提 | ✅ 纳入 S3（同次 LLM 拼装）|
| 幂等保护 | ❌ 未提 | ✅ 纳入 S1 |
| 后台最小间隔 | 简单提了"60s" | ✅ 完整设计（session_key + topic_hash）|
| 反向规避 OpenAkita 坑 | 部分 | ✅ 完整（30s 清空 + 无去重 + 每轮 LLM 判定）|
| 调研深度 | 中等 | **深度**（relational/retrieval/consolidator/storage 都覆盖）|

确认后进入 `writing-plans` 阶段拆 WU。