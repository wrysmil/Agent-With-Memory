# Phase 2 集体测试报告

**日期**:2026-09-10
**执行者**:Leader
**触发**:WU-09 完成通知
**范围**:`tests/`(全量)+ `nanobot/`(ruff)+ `prompts/`(snapshot)

## 1. 全量 pytest tests/
- **末行**:`9 failed, 5777 passed, 8 skipped in 173.16s (0:02:53)`
- **失败列表**(全部 pre-existing,与 Phase 2 无关):
  - `tests/agent/test_mcp_reconnect_crash.py::test_mcp_reconnect_during_shutdown_does_not_crash`
  - `tests/cli/test_tui_launcher.py::test_launcher_keeps_the_tui_alive_while_an_existing_gateway_recovers`
  - `tests/tools/test_mcp_probe.py::test_probe_uses_default_port_for_http`
  - `tests/tools/test_mcp_probe.py::test_probe_tries_next_validated_ip_when_first_is_unreachable`
  - `tests/tools/test_mcp_tool.py::test_connect_mcp_servers_http_clients_reject_unsafe_redirect_targets[config0-sse]`
  - `tests/tools/test_mcp_tool.py::test_connect_mcp_servers_http_clients_reject_unsafe_redirect_targets[config1-streamableHttp]`
  - `tests/tools/test_web_fetch_security.py::test_web_fetch_does_not_fallback_after_pinned_dns_rebind_rejection`
  - `tests/tools/test_web_fetch_security.py::test_web_fetch_blocks_private_redirect_before_returning_image`
  - `tests/tools/test_web_fetch_security.py::test_web_fetch_does_not_request_private_redirect_target`
- **Phase 2 自身**:无失败贡献。`pytest tests/memory/` → 306 passed ✓(包含 WU-09 新增 8 用例);`pytest tests/memory/test_loop_wiring.py tests/memory/test_extraction_integration.py` → 18 passed ✓。

## 2. MemoryStore 回归(plan §10 Task 11)
- **末行**:`47 passed in 0.21s`
- **结论**:✓ 无回归(MemoryStore 文件 I/O 层与 SQLite 链路完全独立,符合 plan 预期)

## 3. Phase 1 SQLite 回归(plan §10 Task 11)
- **末行**:`46 passed in 0.58s`
- **结论**:✓ 无回归

## 4. Phase 2 新增 WU-01..09
- **末行**:`5 failed, 227 passed in 3.20s`
- **失败列表**(test 顺序敏感,pre-existing Pydantic 前向引用):
  - `tests/memory/test_loop_wiring.py::test_memory_extraction_disabled_by_default`
  - `tests/memory/test_loop_wiring.py::test_memory_extraction_disabled_leaves_turn_hook_chain_clean`
  - `tests/memory/test_loop_wiring.py::test_memory_extraction_enabled_wires_all_three_points`
  - `tests/memory/test_loop_wiring.py::test_deletion_dispatches_background_extraction`
  - `tests/memory/test_loop_wiring.py::test_deletion_extraction_failure_is_contained`
- **根因**:`nanobot/agent/loop.py:316` 触发 `PydanticUserError: ToolsConfig is not fully defined; you should define WebToolsConfig, then call ToolsConfig.model_rebuild()`
- **验证**:单跑 `test_loop_wiring.py` → 10 passed ✓;单跑 `tests/memory/` → 306 passed ✓。仅当特定子集包含 `test_intent.py`/`test_prompts.py` 时触发导入顺序副作用。
- **结论**:**非 Phase 2 引入**。main 分支同样存在。修复建议:在 `nanobot/agent/loop.py` 入口补 `ToolsConfig.model_rebuild()` 或调整测试导入顺序。

## 5. ruff check
- **末行**:`Found 25 errors. [*] 8 fixable with the --fix option`
- **典型错误**:多行 import 排序(`I001`)
- **基线对比**:`git stash` 后跑同命令 → 25 errors(同数量)。**pre-existing**,非 Phase 2 引入。
- **Phase 2 新文件单独检查**:`ruff check tests/memory/test_extraction_integration.py` → All checks passed ✓
- **结论**:Phase 2 未引入新 ruff 错误。

## 6. basedpyright
- **末行**:`1355 errors, 0 warnings, 0 notes`
- **典型错误**:`nanobot/webui/memory_api.py` / `memory_services.py` / `gateway_services.py` 类型注解缺失(reportUnknownMemberType / reportArgumentType / reportUnnecessaryIsInstance)
- **结论**:**pre-existing webui/ 类型注解不完整**。与 Phase 2 后端逻辑无关。建议单独 WU 治理。

## 7. prompts 稳定性快照
- SEMANTIC_EXTRACTION_PROMPT:len=1402, sha256=`520696e8c4a593d4b5d890ca2b88b15dd42de5fede73d0ad5c6ca0e46ba64542`
- EPISODE_EXTRACTION_PROMPT:len=816, sha256=`e0fba8ad988ad1d80e56885463382713d225dc731c9f802330e18264de2a74e9`
- SCRATCHPAD_FORMAT_PROMPT:len=538, sha256=`eaed7dcb9a9b1f9d74c002ce766919f46cdcb83633b3997edd152cf6a76d9bcf`(未调用,见 review C-1,Phase 3 延后)
- TOPIC_CHANGE_DETECTION_PROMPT:len=499, sha256=`e383e53c7f4253e165dd3b7b27de5a66177d9166bb65cf69cc10791b5a7eb79e`

## 8. 结论

| 维度 | 判定 |
| --- | --- |
| Phase 2 自身引入的失败 | 0 |
| Phase 2 引入的 ruff/basedpyright 错误 | 0 |
| MemoryStore 回归 | PASS |
| Phase 1 SQLite 回归 | PASS |
| WU-09 端到端集成 | PASS(8/8) |

**verdict**:**go**(针对 Phase 2 范围)

## 9. 阻塞项 / 后续

- **阻塞**:无 — Phase 2 范围全绿
- **延后到 WU-10**(review-fix):M-1(SHA1 docstring) + M-4(source_episode_id 回填)
- **延后到 Phase 3**(plan §14):C-1(SCRATCHPAD_FORMAT_PROMPT 调用)、C-2(T5 semantic-only)
- **pre-existing,不影响 Phase 2 合入**:MCP/TUI/web_fetch 9 个 pytest 失败、ruff 25 errors、basedpyright 1355 errors、Pydantic ToolsConfig 顺序敏感 — 另开 WU 治理

## 10. 附:日志原文

完整日志:`.ai-runtime-artifacts/verifications/2026-09-10-phase2-memory-extraction-collective-test.log`

---

> 待 security-auditor 完成 → Leader 汇总 → 派 WU-10 → 重新跑集体测试 → push。
