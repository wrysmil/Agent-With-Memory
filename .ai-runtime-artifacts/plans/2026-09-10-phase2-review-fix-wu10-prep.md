# WU-10: Phase 2 Review+Security Fix(派发预备文档)

> 双审查(reviewer + security-auditor)汇总后派发。
> 范围严格限定为修复,不引入新功能。

## 双审查汇总 verdict

| 审查方 | verdict | 阻塞项 |
| --- | --- | --- |
| reviewer | no-go | C-1 / C-2 均为 Phase 3(plan §14)延后项,非 Phase 2 范围 |
| security-auditor | needs-fixes | C-α / C-β 真实待爆缺陷(默认 `memory_extraction_enabled=False` 时不可触发) |

## WU-10 范围(6 项必修)

### FIX-1(Sec-C-α):ScratchpadWriter 跨用户串数据
- **文件**:`nanobot/agent/loop.py:507-510` 构造缺 user_id
- **现问题**:`ScratchpadWriter(services.database, workspace_id=...)` → 默认 `user_id="default"`;`update_focus(session_key, ...)` 显式忽略 session_key → 所有 channel/chat 共享一行
- **修复**:
  1. `ScratchpadWriter` 增加显式 `user_id` 必传参数(去掉默认值)
  2. `AgentLoop._wire_memory_extraction` 派生 `user_id`(从 `session_key` 解析 `chat_id` 部分:`channel:chat_id` → `chat_id`,缺失则回退到 `workspace_id`)
  3. `_extractor_provider` / `_scratchpad_writer_provider` 改为 per-session_key 闭包工厂
  4. 测试:不同 session_key 写 focus 后,scratchpad 表有独立行

### FIX-2(Sec-C-β):删除会话 provenance 丢失
- **文件**:`nanobot/memory/models.py:32-36`(EpisodeSource)+ `nanobot/memory/extractor.py:84-89`(_SOURCE_MAP)
- **现问题**:`_SOURCE_MAP` 把 `"deletion"` 折叠成 `SESSION_END` → 事后无法定向清除
- **修复**:
  1. `EpisodeSource` 增 `DELETION = 'deletion'`
  2. `_SOURCE_MAP` 增 `"deletion": EpisodeSource.DELETION`,不再折叠
  3. `_persist` 透传 source → episode.source 正确落 `deletion`
  4. 数据库迁移:`migrate_episode_source` 把历史折叠值保留 SESSION_END 不动;只新增列支持
  5. 测试:`extract_session(source="deletion")` 后 `episodes.source == EpisodeSource.DELETION`

### FIX-3(Sec-M-1):ActionNode 输出未截断,凭据可外发并落库
- **文件**:`nanobot/memory/extractor.py` `_collect_action_nodes`(line ~251-281)
- **现问题**:`output=results.get(call_id, "")` 全文拼进 prompt + 落库
- **修复**:
  1. `ActionNode` 类增 `OUTPUT_MAX_CHARS = 2048`、`INPUT_MAX_CHARS = 512` 常量
  2. `_collect_action_nodes` 截断 input/output;截断后追加 `"...[truncated N chars]"`
  3. 添加 `_REDACT_PATTERNS`:`(r'(?i)(api[_-]?key|secret|password|token)\s*[=:]\s*\S+', '<redacted>')`
  4. 截断前先做 redact
  5. 测试:`bash("env")` 大输出 → ActionNode.output 截断到 2048 字符 + 含 `<redacted>`

### FIX-4(Sec-M-2):`importance` 等数值字段无值域校验
- **文件**:`nanobot/memory/extractor.py` `_coerce_memory_item`(line ~340-353)
- **现问题**:`float("nan")→nan`、`float("1e400")→inf`、负数原样入库;`ORDER BY importance_score DESC` 可被劫持
- **修复**:
  1. `_coerce_memory_item` 内:`importance` 用 `math.isfinite(v)` 检查,失败或越界 → `0.7`;合法则 `min(max(v, 0.0), 1.0)` 钳制
  2. `content` 长度上限 8192,超长截断
  3. `tags` 条数上限 32,超出截断
  4. `subject` / `predicate` 长度上限 256
  5. 测试:LLM 输出 `{"importance":"nan"}` → 入库值 = 0.7;`{"importance":"1e400"}` → 入库值 = 1.0;`{"importance":-1}` → 0.0

### FIX-5(Rev-M-1):`compute_content_hash` docstring 与实现不一致
- **文件**:`nanobot/memory/filters.py:141-158`
- **改**:docstring "40 位 SHA1 十六进制字符串(取 SHA256 前 20 字节...)" → "SHA-1 hex digest of `content|subject|predicate` (40 chars)."

### FIX-6(Rev-M-4):`Memory.source_episode_id` 反向回填缺失
- **文件**:`nanobot/memory/extractor.py` `_persist`
- **依据**:plan §10 Task 5
- **修复**:
  1. `repository` 新增 `update_memory_source_episode(conn, memory_id, episode_id)`
  2. `_persist` 写完 episode 后遍历 `linked_memory_ids` 执行 UPDATE
  3. 单条失败仅 `logger.warning`,不抛
  4. 测试:3 用例(集成路径 / 边界空 linked_ids / 失败隔离)

## 约束

- 行为兼容:除 FIX-2 / FIX-4 的截断和钳制外,`extract_session` 返回签名不变
- 复用现有 `_persist` 结构,所有写入同一事务
- 不引入新功能(无 Phase 3 越界)
- 不重命名公共 API

## 延后到 WU-11(security-hardening 单独轮次)

- Sec-M-3:转录 role 标记转义(`[assistant]` 伪造)
- Sec-M-4:SQLite 文件 chmod 0o600 + WAL + busy_timeout
- Sec-M-5:DB/过滤走 `asyncio.to_thread` + `_BACKGROUND_TASKS` semaphore
- Sec-M-6:WebUI scope/owner 过滤
- Rev-C-1:`SCRATCHPAD_FORMAT_PROMPT` 调用路径(Phase 3)
- Rev-C-2:T5 semantic-only(Phase 3)
- Minor:`update_focus` 跨事务 lost update、`search_memories` FTS 语法兜底

## 验证命令

```bash
cd /Users/mima0000/Documents/学习-001/do-project/Agent-With-Memory

# 1. 修改文件 lint(零 error 期望)
uv run --no-sync ruff check nanobot/memory/ nanobot/agent/hooks/memory_extraction.py nanobot/agent/loop.py nanobot/session/manager.py tests/memory/

# 2. 既有测试不回归
uv run --no-sync pytest tests/memory/test_filters.py tests/memory/test_extractor.py tests/memory/test_hook_memory_extraction.py tests/memory/test_loop_wiring.py tests/memory/test_extraction_integration.py tests/memory/test_scratchpad_writer.py tests/memory/test_quick_facts.py tests/memory/test_intent.py tests/memory/test_prompts.py -q

# 3. 全 memory 测试
uv run --no-sync pytest tests/memory/ -q

# 4. MemoryStore 无回归
uv run --no-sync pytest tests/agent/test_memory_store.py -q

# 5. 新文件单测
uv run --no-sync pytest tests/memory/test_security_fixes.py -v
```

## 派发角色

- `agent_role`:coder
- `wu_type`:feature(行为补全)+ docs(FIX-5)
- 预计 2-3 个 commit:
  1. `feat(memory): per-session scratchpad writer and deletion provenance (Sec-C-α/β)`
  2. `feat(memory): clamp action_node output and importance numeric ranges (Sec-M-1/2)`
  3. `feat(memory): backfill memory.source_episode_id after episode persistence (Rev-M-4)` + `docs(memory): fix compute_content_hash docstring (Rev-M-1)`(可合并或独立)

## 关联

- Reviewer 报告:`.ai-runtime-artifacts/reviews/2026-09-10-phase2-memory-extraction-code-review.md`
- Security 报告:`.ai-runtime-artifacts/reviews/2026-09-10-phase2-memory-extraction-security-review.md`
- 集体测试报告:`.ai-runtime-artifacts/verifications/2026-09-10-phase2-memory-extraction-collective-test.md`
