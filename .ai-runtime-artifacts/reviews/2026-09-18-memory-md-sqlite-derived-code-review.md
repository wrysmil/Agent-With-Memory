# 代码审查报告：MEMORY.md SQLite 派生 + Dream 降级

## 审查概要

- **审查日期**: 2026-09-18
- **审查范围**: worktree `wt-memory-md-sqlite-derived` 分支上 WU-2~WU-10 新增/变更文件
- **审查方法**: 逐文件静态代码审查 + 测试覆盖率检查 + 边界情况分析

## 审查结果

### 严重问题（必须修复）

#### 1. `_safe_write_with_backup` 回滚逻辑反了
**文件**: `nanobot/memory/lifecycle.py:192-202`

```python
def _safe_write_with_backup(self, path: Path, content: str) -> None:
    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        shutil.copy2(path, backup)
    try:
        path.write_text(content, encoding="utf-8")
    except Exception:
        if backup.exists():
            shutil.copy2(backup, path)  # ← 写入失败时回滚
        raise
```

**问题**: 代码注释说 "restore on failure"，但逻辑是对的（`except` 块内 `shutil.copy2(backup, path)` 确实是在失败时恢复）。**但实际上这段代码是正确的**。

让我重新检查——实际逻辑没问题，是注释误导了读者。再检查其他问题。

#### 2. `_render_memory_md` 缺少 workspace_id 过滤
**文件**: `nanobot/memory/lifecycle.py:111-129`

```python
def refresh_memory_md_sync(self, workspace_id: str) -> dict[str, Any]:
    with self.services.database.connect() as conn:
        try:
            memories = list_memories(
                conn,
                scope="user",
                min_importance=0.5,
                limit=200,
                order_by="importance",
            )
        except TypeError:
            # fallback path...
```

**问题**: `list_memories` 的 SQLite 查询**没有传入 `workspace_id`**！虽然 `list_memories` 签名支持 `workspace_id` 参数，但派生时未传入，导致会返回**所有 workspace** 的 user scope 记忆。

**影响**: 多 workspace 场景下，MEMORY.md 会混入其他 workspace 的数据。

**修复建议**: 在 `list_memories` 调用中添加 `workspace_id=workspace_id`。

#### 3. 类级别 `_derive_lock` 导致跨 workspace 互斥
**文件**: `nanobot/memory/lifecycle.py:79`

```python
class MemoryLifecycle:
    _instances: dict[str, "MemoryLifecycle"] = {}
    _refresh_tasks: dict[str, asyncio.Task] = {}
    # ...
    _derive_lock = threading.Lock()  # ← 所有 workspace 共用一把锁
```

**问题**: `_derive_lock` 是类级别共享的 `threading.Lock()`，当 workspace A 正在派生时，workspace B 的派生请求会被阻塞。

**影响**: 多 workspace 并发场景下，一个 workspace 的派生会阻塞其他 workspace。

**修复建议**: 使用 workspace 级别的锁，或改用 `asyncio.Lock`。

---

### 中等问题（建议修复）

#### 4. 异常处理吞掉了真实错误
**文件**: `nanobot/memory/lifecycle.py:149-155`

```python
except Exception:
    logger.exception(
        "Failed to refresh MEMORY.md for workspace {}",
        workspace_id,
    )
    return {"status": "error", "reason": "write_failed"}
```

**问题**: 无论失败原因是「数据库空」「文件权限问题」「编码错误」还是「内存不足」，返回值都是相同的 `{"status": "error", "reason": "write_failed"}`。调用方无法区分错误类型。

**影响**: 调试困难，用户看到的是同一个错误消息。

**修复建议**: 区分异常类型，或在日志中记录具体原因（已有 `logger.exception`）。

#### 5. `_refresh_memory_md_after_mutation` 日志格式错误
**文件**: `nanobot/webui/memory_api.py:497-499`

```python
except Exception:
    _logger = logging.getLogger(__name__)
    _logger.warning("[MemoryLifecycle] refresh_memory_md failed after mutation: %s")
```

**问题**: 使用 `%s` 但没有传入实际参数（应该是 `logger.warning(..., e)` 或 `logger.warning(..., exc_info=True)`）。

#### 6. `truncate_memory_md` 截断后可能丢失结尾换行符
**文件**: `nanobot/memory/lifecycle.py:334`

```python
return ("\n\n".join(result_parts)).rstrip()
```

**问题**: 使用 `rstrip()` 去掉了尾部换行，但 `_render_memory_md` 返回的内容末尾有换行（第 239 行）。如果内容被截断，输出与原始格式不一致。

**修复建议**: 移除 `.rstrip()` 或在末尾追加 `\n`。

#### 7. `truncate_memory_md` 正则边界情况
**文件**: `nanobot/memory/lifecycle.py:258-261`

```python
section_pattern = re.compile(
    r"(?:^|\n)(## [^\n]+)\n((?:.*?\n?)*?)(?=\n## |$)", re.DOTALL | re.MULTILINE
)
```

**问题**: 当 `max_chars` 很小时（例如 50），高优先级规则段落可能被截断标记覆盖，导致正文为空但仍显示 `...（规则被截断）`。

---

### 轻微问题（可选）

#### 8. 测试文件有重复函数名
**文件**: `tests/memory/test_lifecycle_integration.py:168-189` 和 `191-196`

```python
def test_dream_editable_files_includes_draft(memory_store):
    """验证 Dream 白名单包含 draft_file（memory_file 不在其中）。
    ...
    """

def test_dream_editable_files_includes_draft(memory_store):
    """验证 draft_file 路径正确。"""
    assert memory_store.draft_file is not None
```

**问题**: 两个同名函数，后者会覆盖前者（Python 允许）。第一个测试的断言永远不会被执行。

#### 9. `MemoryLifecycle` 单例不清除旧实例
**文件**: `nanobot/memory/lifecycle.py:88-95`

```python
@classmethod
def for_workspace(cls, workspace_id: str, services: MemoryServices) -> "MemoryLifecycle":
    if workspace_id not in cls._instances:
        cls._instances[workspace_id] = cls(workspace_id, services)
    return cls._instances[workspace_id]
```

**问题**: 如果同一 `workspace_id` 用不同的 `services` 实例重新调用，返回的是旧的单例。可能导致状态不一致。

#### 10. `refresh_memory_md_sync` 中 `scope="user"` 硬编码
**文件**: `nanobot/memory/lifecycle.py:114`

**观察**: 派生时硬编码 `scope="user"`。如果未来需要支持 `scope="global"` 或其他 scope，需要改代码。

---

### 正面发现（可保留）

1. **`content_hash_legacy` 实现简洁清晰**: SHA-1 哈希用于去重，逻辑正确。

2. **去抖实现健壮**: `schedule_refresh_md` 的去抖逻辑（时间窗口 + 取消旧任务）设计合理。

3. **截断算法保护规则段落**: `truncate_memory_md` 的规则优先截断策略符合业务需求。

4. **测试覆盖率良好**: `test_lifecycle.py` 覆盖了渲染、去重、截断、去抖、单例等核心逻辑。

5. **前端 Modal 组件结构清晰**: `MemoryMdCard.tsx`、`MemoryMdViewerModal.tsx`、`MemoryDraftDiffModal.tsx` 职责分离良好。

6. **API 钩子正确隔离**: `_refresh_memory_md_after_mutation` 失败不传播给 mutation 调用方，设计正确。

7. **Dream 白名单降级**: `build_dream_tools` 中 `editable_files` 移除了 `memory_file`，只保留 `draft_file`，符合降级设计。

---

## 验证建议

需要验证以下具体测试点：

### 必须修复后验证
1. **跨 workspace 数据隔离**: 创建两个 workspace 的记忆，验证派生出的 MEMORY.md 只包含对应 workspace 的数据
2. **并发派生无阻塞**: 两个 workspace 同时触发派生，验证不互相阻塞
3. **写入失败回滚**: 模拟文件写入失败（权限问题），验证 `.bak` 备份被正确恢复
4. **截断后格式一致**: 验证截断后的 MEMORY.md 格式与未截断时一致（末尾有换行）

### 建议验证
5. **异常类型区分**: 模拟不同类型的失败（DB空/文件权限/编码错误），验证错误日志/返回值有区分
6. **重复测试修复**: 验证 `test_lifecycle_integration.py` 中两个同名测试都能运行

---

## 五轴检查摘要

| 轴 | 状态 | 关键问题 |
|----|------|---------|
| **正确性** | ⚠️ | workspace_id 未传入查询；并发阻塞；异常类型未区分 |
| **可读性** | ✅ | 代码结构清晰，注释充分 |
| **架构** | ⚠️ | 单例模式 + 类级别锁导致跨 workspace 耦合 |
| **安全** | ✅ | 无注入风险；文件路径无穿越；错误信息不泄露敏感数据 |
| **性能** | ⚠️ | 类级别锁在高并发下可能成为瓶颈；去抖有效防止频繁派生 |

---

## 证据

**已读文件列表**:
- `nanobot/memory/lifecycle.py` (335 行)
- `nanobot/memory/filters.py` (263 行)
- `nanobot/memory/repository.py` (942 行)
- `nanobot/webui/memory_api.py` (703 行)
- `nanobot/webui/memory_routes.py` (217 行)
- `nanobot/webui/settings_routes.py` (788 行)
- `nanobot/webui/gateway_services.py` (200 行)
- `nanobot/agent/memory.py` (1278 行)
- `nanobot/templates/agent/dream.md` (139 行)
- `nanobot/command/builtin.py` (部分，451-530 行)
- `webui/src/components/settings/memory/MemoryMdCard.tsx` (184 行)
- `webui/src/components/settings/memory/MemoryMdViewerModal.tsx` (67 行)
- `webui/src/components/settings/memory/MemoryDraftDiffModal.tsx` (84 行)
- `webui/src/components/settings/memory/MemorySection.tsx` (145 行)
- `tests/memory/test_lifecycle.py` (450 行)
- `tests/memory/test_lifecycle_integration.py` (278 行)
- `tests/memory/test_memory_api_hooks.py` (238 行)
- `tests/memory/test_repository_scope_filter.py` (187 行)
- `tests/webui/test_refresh_md_route.py` (227 行)

**未读文件**:
- `nanobot/memory/models.py` (模型定义，依赖类型推断)
- `nanobot/webui/memory_services.py` (依赖推断)

---

## Skills 使用
- `code-review-and-quality@.claude/skills/code-review-and-quality/SKILL.md` 已加载
- `security-and-hardening@.claude/skills/security-and-hardening/SKILL.md` 已加载
- `frontend-ui-engineering@.claude/skills/frontend-ui-engineering/SKILL.md` 已加载
