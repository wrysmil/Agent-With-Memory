---
artifact: collective-test
route: orchestration:dispatcher-workflow
skills:
  - verification-before-completion
source:
  - .ai-runtime-artifacts/plans/2026-09-16-vector-retrieval-plan.md
created_at: 2026-09-16
scope: 3b61993..9d30d00
---

# 向量检索接入 —— 集体测试（尾盘 A）

Leader 在全部 WU 与审查修复落地后，手动执行的全量回归。

## 1. 全量测试

```
$ uv run pytest tests/ -q
11 failed, 6249 passed, 49 skipped in 324.65s
```

**11 个失败全部为既有环境失败**（非本批引入），判定证据：把这些失败文件在**会话起点
`3b61993`** 上跑，同样 **32 failed**、失败集完全相同：

```
$ git checkout 3b61993 && uv run pytest tests/cli/test_commands.py tests/webui/test_gateway_webui_smoke.py -q
32 failed, 119 passed, 1 skipped
```

失败集中在绑端口（`18791`）/ 杀进程树 / PDF 解析一类**环境依赖测试**（本机既有 flaky）。

## 2. 向量域（本批直接相关）

```
$ uv run pytest tests/memory/ -q          # 全部 WU 落地后
774 passed

$ uv run pytest tests/memory/ tests/webui/ -q    # 审查修复后
1124 passed, 2 skipped
```

## 3. 静态检查

```
$ uv run ruff check nanobot/               # 当前
Found 12 errors
$ git checkout 3b61993 && uv run ruff check nanobot/   # 会话起点
Found 12 errors
```
✅ 无新增。

```
$ uvx basedpyright                          # 当前
3225 errors
$ git checkout 3b61993 && uvx basedpyright  # 会话起点
3198 errors
```
⚠️ 净增 27（详见验收产物 R4 小节）；本机 3.13，须 CI@3.11 复跑定论（R7）。

## 4. 覆盖率（R5）

见 `verifications/2026-09-16-vector-retrieval-verification.md` 的 R5 小节。

## 5. 结论

向量域全绿；全量失败经基线比对确认为既有环境 flaky；ruff 无新增；basedpyright 净增 27
（同质于基线 3198）。详见验收产物总判定。
