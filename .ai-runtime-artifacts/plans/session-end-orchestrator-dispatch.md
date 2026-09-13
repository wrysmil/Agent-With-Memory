# 执行图：会话结束编排优化

> 日期：2026-09-13
> 计划：`.ai-runtime-artifacts/plans/session-end-orchestrator-optimization-plan.md`
> Worktree: `wt-session-end-guard`

---

## 执行图

### GROUP-1（无依赖，并行）

| WU | 描述 | 文件 | 依赖 | wu_type | agent_role | wu_skills |
|----|------|------|------|---------|------------|-----------|
| WU-01 | 守卫逻辑实现 | `orchestrator.py`, `extractor.py` | 无 | feature | coder | `source-driven-development@harness-kit/.agents/skills/source-driven-development/SKILL.md` |

### GROUP-2（依赖 WU-01）

| WU | 描述 | 文件 | 依赖 | wu_type | agent_role | wu_skills |
|----|------|------|------|---------|------------|-----------|
| WU-02 | WebUI 端点接入 | `api/endpoints/sessions.py`, `api/schemas.py` | WU-01 | feature | coder | `source-driven-development@harness-kit/.agents/skills/source-driven-development/SKILL.md` |
| WU-03 | 单元测试补强 | `tests/unit/memory/test_orchestrator_guards.py` | WU-01 | test | test-engineer | `source-driven-development@harness-kit/.agents/skills/source-driven-development/SKILL.md` |

---

## 实现顺序

```
GROUP-1: WU-01（守卫逻辑）
    ↓
GROUP-2: WU-02 + WU-03（并行）
    ↓
[尾盘] verification-before-completion
    ↓
[尾盘] requesting-code-review
```

---

## 验收标准

- [ ] 单条"你好"会话不触发 LLM 提取
- [ ] 两条消息会话不触发 LLM 提取
- [ ] 短消息但有工具调用的会话正常触发提取
- [ ] `extract_user_profile` 过滤短用户消息
- [ ] `extract_experience` 要求 ≥2 assistant 轮次
- [ ] `POST /sessions/{key}/end` 能触发完整 4 步编排
- [ ] 守卫逻辑有单元测试覆盖

---

## Worktree 信息

```
WorktreePath: /Users/mima0000/Documents/学习-001/do-project/.harness-worktrees/Agent-With-Memory/wt-session-end-guard
Branch: harness/wt-session-end-guard
Base: feature/memory-system
```
