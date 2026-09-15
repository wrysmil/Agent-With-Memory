You are implementing **WU-C (GROUP-C, Task 6)** — the test cleanup pass for the session-end idle extraction feature. The full plan is in `.ai-runtime-artifacts/plans/2026-09-15-session-end-idle-extraction-plan.md` (Task 6 section). Your scope:

1. Delete T5 dead tests (T5 was disabled in commit 6ce9b0d, its tests still exist and fail)
2. Delete old T1 tests that depend on `_schedule_run_extraction` / `EXTRACTION_WAIT_TIMEOUT` / `_await_pending_extractions` (these were replaced by the idle-timer design in WU-A)
3. Create new end-to-end integration tests for the idle extraction flow
4. Verify `pytest tests/memory/ -q` has **0 failures** (the previous baseline of ~20 T5 failures + a few old T1 failures should all be gone after your work)

## Context

The codebase has accumulated dead tests:
- **T5 (topic change detection)** was disabled in commit `6ce9b0d` (T5 caused 5-7s latency per turn). The implementation is now a no-op, but the test classes `TestTopicChangeDetection` (~8 tests) and `TestTopicChangeIncrementalExtraction` (~12 tests) still exist and fail.
- **Old T1 (per-turn full extraction)** was replaced by idle-timer extraction in WU-A. Any test that pokes at `_schedule_run_extraction`, `EXTRACTION_WAIT_TIMEOUT`, or `_await_pending_extractions` is now testing deleted code.

The previous behavior was: every agent turn → full re-extraction → 5s timeout → often cancel. New behavior: agent turn → arm 10-min idle timer → on timer fire → incremental extraction → state advances. The integration tests should cover the new behavior.

## Pre-flight: this WU needs the WU-A backend changes

This WU must run on a branch that has BOTH:
- The WU-A backend changes (idle timer, `run_idle_extraction`, state table, etc.)
- The WU-B frontend changes (already merged into `feature/memory-system`)

So before starting, you need to **merge WU-A into `feature/memory-system` first**. Wait — that's actually the leader's job. Let me clarify: the WU-A worktree exists at `.worktrees/wt-session-idle-extraction-backend` with branch `wt-session-idle-extraction-backend`. The leader will merge that into `feature/memory-system` before launching you. So when you start, `feature/memory-system` should already have WU-A merged.

To verify: `git log --oneline -5` on `feature/memory-system` should show:
- A WU-A commit (something like `feat(memory): idle extraction + state table`)
- WU-B commit `7876d0d feat(memory): remove working memory UI surface (scratchpad tab)`
- Earlier history

If WU-A is NOT yet merged into `feature/memory-system`, STOP and report back. Do not try to merge it yourself.

## Working directory — MANDATORY worktree

```bash
cd /d/studyspace/源码学习/Agent-With-Memory
git fetch origin 2>/dev/null || true
git worktree add ../.worktrees/wt-session-idle-test-cleanup -b wt-session-idle-test-cleanup feature/memory-system
cd ../.worktrees/wt-session-idle-test-cleanup
```

All work happens in `D:\studyspace\源码学习\.worktrees\wt-session-idle-test-cleanup`.

## Task 6 — Test cleanup + integration tests

### Step 1: Identify dead tests

```bash
cd tests/memory
grep -n "TestTopicChangeDetection\|TestTopicChangeIncremental\|_schedule_run_extraction\|EXTRACTION_WAIT_TIMEOUT\|_await_pending_extractions" test_hook_memory_extraction.py test_memory_extraction_hook.py
```

This should reveal the test classes to delete and any individual tests that depend on removed APIs.

### Step 2: Delete the T5 dead test classes

Open `tests/memory/test_hook_memory_extraction.py` and `tests/memory/test_memory_extraction_hook.py`. Find and **delete entirely**:
- `class TestTopicChangeDetection` (and all its methods)
- `class TestTopicChangeIncrementalExtraction` (and all its methods)

If either file becomes empty (or nearly empty) of meaningful tests after the deletion, that's fine — keep whatever's left.

### Step 3: Delete old T1 tests that reference removed APIs

After Step 2, re-run the grep from Step 1. Any remaining hits for `_schedule_run_extraction`, `EXTRACTION_WAIT_TIMEOUT`, or `_await_pending_extractions` indicate tests that need deletion (they're testing removed code).

Delete those test methods/classes. If a class loses all its methods, delete the class too. **Don't preserve dead tests for "future reference"** — git history has them.

### Step 4: Verify T5 dead code is consistent

Check that the production code in `nanobot/agent/hooks/memory_extraction.py` is consistent (it should already be after WU-A, but verify):
```bash
grep -n "_schedule_run_extraction\|EXTRACTION_WAIT_TIMEOUT\|_await_pending_extractions\|_run_extraction" nanobot/agent/hooks/memory_extraction.py
```

Should be empty (all four are deleted). If any still exist, flag it but don't try to delete them — that's WU-A's job.

### Step 5: Create `tests/memory/test_extraction_integration.py`

Write end-to-end tests for the new idle-extraction flow. Use the `MemoryDatabase` + `MemoryExtractor` + `MemoryExtractionHook` from the codebase, with a `_FakeProvider` (see existing tests in `test_extractor.py` for the pattern).

Required tests (at minimum):

```python
async def test_idle_extraction_full_cycle():
    """Full happy path:
    1. Open memory_enabled
    2. Simulate 3 agent turns (after_run x3 with 3 different user messages)
    3. Verify idle timer is armed (3 times, with each new arm cancelling the previous)
    4. Wait IDLE_THRESHOLD_SECONDS (use override 0.1s)
    5. Verify extraction ran
    6. Verify state.last_count advanced
    7. Verify scratchpad.current_focus updated
    """

async def test_idle_extraction_cancelled_by_new_message():
    """Cancellation path:
    1. after_run #1 → arms timer
    2. after_run #2 (before timer fires) → cancels first timer, arms new one
    3. Wait for second timer to fire
    4. Verify extraction ran ONCE (not twice)
    5. Verify only the second timer produced a state advance
    """

async def test_idle_extraction_no_op_when_state_current():
    """No-op path:
    1. Pre-populate state with last_count == current message count
    2. Trigger run_idle_extraction
    3. Verify 0 LLM calls
    4. Verify state unchanged
    """

async def test_idle_extraction_advances_state_only_on_success():
    """Failure doesn't advance:
    1. State has last_count=2, session has 5 messages
    2. Configure provider to return invalid JSON (triggers parse failure → "invalid_json" failed_tracks)
    3. Run run_idle_extraction
    4. Verify state.last_count is STILL 2
    5. Now fix the provider and re-run
    6. Verify state.last_count advances to 5
    """
```

For the hook-level tests, you'll need to deal with asyncio task scheduling. Use `asyncio.wait_for` with a reasonable timeout (e.g., 5x the `IDLE_THRESHOLD_SECONDS` override).

For database setup, follow the `db` fixture pattern from `test_extractor.py`:
```python
@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database
```

### Step 6: Verify the whole suite is clean

```bash
cd /d/studyspace/源码学习/.worktrees/wt-session-idle-test-cleanup
uv run --no-sync pytest tests/memory/ -q
```

**Target: 0 failures.** If any T5-related failures remain, go back to Step 2 and clean more aggressively. If any T1-old-API failures remain, go back to Step 3.

Also run:
```bash
uv run --no-sync pytest tests/ -q
uv run --no-sync ruff check nanobot/ tests/
```

`ruff` should be clean. `pytest tests/` may have some unrelated failures (the baseline had 135 tsc errors and a few other issues), but **tests/memory/** specifically must be 0 failures.

## DoD

- `uv run --no-sync pytest tests/memory/ -q` → **0 failures** (this is the main success criterion)
- New file `tests/memory/test_extraction_integration.py` exists with ≥4 tests, all passing
- `git status` → all changes committed on branch `wt-session-idle-test-cleanup`
- Do NOT push
- Do NOT merge into `feature/memory-system`

## Final report (return when done)

1. Branch name
2. Worktree path
3. Files changed (list)
4. Tests deleted (count by class)
5. New integration tests added (count, names)
6. `pytest tests/memory/ -q` final result (must be 0 failures)
7. `pytest tests/ -q` final result (note any pre-existing unrelated failures)
8. Any deviations or findings
