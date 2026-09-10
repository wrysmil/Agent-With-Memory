---
artifact: implementation-dispatch
route: orchestration:dispatcher-workflow
plan: docs/记忆系统/plan/阶段二设计_记忆提取.md
skills:
  - orchestration
  - writing-plans
skills_evidence:
  - .claude/skills/orchestration/SKILL.md
  - harness-kit/core/orchestration/dispatcher-workflow.md
created_at: 2026-09-10
dispatcher: leader
topic: phase2-memory-extraction
phase: 1-of-1
---

# 阶段二记忆提取 — Harness 执行图

> 本文件描述并行 GROUP / WU 与派发，不改 plan。plan Task 编号对应 §十。

## 执行图

```markdown
GROUP-1（并行，无依赖，4 WU）:
  WU-01: 防污染过滤器 filters.py | 文件: nanobot/memory/filters.py | 依赖: 无 | wu_type: feature | agent_role: coder | wu_skills: auto
  WU-02: 轻量意图识别器 intent.py | 文件: nanobot/memory/intent.py | 依赖: 无 | wu_type: feature | agent_role: coder | wu_skills: auto
  WU-03: LLM 提示词常量 prompts.py | 文件: nanobot/memory/prompts.py | 依赖: 无 | wu_type: feature | agent_role: coder | wu_skills: auto
  WU-04: ScratchpadWriter scratchpad_writer.py | 文件: nanobot/memory/scratchpad_writer.py | 依赖: WU-02 | wu_type: feature | agent_role: coder | wu_skills: auto

GROUP-2:
  WU-05: MemoryExtractor 主类 | 文件: nanobot/memory/extractor.py | 依赖: WU-01,02,03,04 | wu_type: feature | agent_role: coder | wu_skills: auto

GROUP-3（并行，无依赖，3 WU）:
  WU-06: AgentHook 适配 | 文件: nanobot/agent/hooks/memory_extraction.py | 依赖: WU-05 | wu_type: feature | agent_role: coder | wu_skills: auto
  WU-07: SessionManager 删除观察者 | 文件: nanobot/agent/loop.py, nanobot/session/manager.py | 依赖: WU-06 | wu_type: feature | agent_role: coder | wu_skills: auto
  WU-08: Quick Facts 压缩集成 | 文件: nanobot/agent/autocompact.py | 依赖: WU-05 | wu_type: feature | agent_role: coder | wu_skills: auto

GROUP-4（串行，最后）:
  WU-09: 端到端集成测试 | 文件: tests/memory/test_extraction_integration.py | 依赖: WU-06,07,08 | wu_type: test | agent_role: test-engineer | wu_skills: auto
```

## GROUP 流水线

```
GROUP-1（并行 01-04）
     ↓ WU-04 依赖 WU-02（intent.py 先产出才能 import）
GROUP-2（串行 WU-05）
     ↓
GROUP-3（并行 06-08）
     ↓
GROUP-4（串行 WU-09）
     ↓
尾盘 A+B → WORKTREE-CLOSE
```

## 变更记录

| 轮次 | 日期 | 变更摘要 |
| --- | --- | --- |
| 1 | 2026-09-10 | 初稿：4 GROUP，9 WU |
