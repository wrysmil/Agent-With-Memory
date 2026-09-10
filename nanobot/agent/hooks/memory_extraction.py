"""MemoryExtractionHook：把三层记忆提取接入 AgentHook 生命周期。

设计依据：`docs/记忆系统/plan/阶段二设计_记忆提取.md`
§2（触发时机）、§6.5.3（T0 与 intent 集成）、§7.3（签名）、§9（错误处理与并发）、
§10 Task 7 / Task 12。

触发点
------
T0   ``after_run``
     取最后一条 user 消息 → ``classify_intent``；CHAT 不写，其余写
     ``scratchpad.current_focus``（同步、不调 LLM）。
T0'  ``on_error``
     异常退出时紧急保存 ``current_focus``（不调 LLM、不调 extractor）。
T1   ``after_run`` + ``on_finally``
     ``after_run`` 创建 fire-and-forget 提取任务并登记；``on_finally`` 等待其完成
     （默认 5s 上限，超时取消并 warning，不抛）。
T5   ``before_iteration``
     从 ``AgentHookContext.messages`` 推导本会话 user 消息序列（plan §2.2
     「session.messages 中 user 消息数 ≥ 4」），达阈值时用轻量 LLM
     （``TOPIC_CHANGE_DETECTION_PROMPT``）判断话题是否切换；命中 NEW 则：
     ① 旧 focus 滚入 ``active_projects`` ② fire-and-forget 触发提取（不登记、不等待）
     ③ 抬高本实例的检测阈值。

为什么 T5 不用实例缓冲
----------------------
``build_agent_turn_hook`` 每轮（每个 inbound 消息）都会调用工厂新建 hook 实例，
实例级缓冲无法跨轮累积，若以实例缓冲作阈值判定则 T5 永不触发。因此 T5 从当前转录
推导窗口（对齐 plan §2.2 / §10 Task 12 原文），与实例生命周期解耦。

代价（已披露）：实例内冷却（``_next_check_count``）只覆盖同一 run 内的多次
``before_iteration``；跨轮因实例重建而重置，故会话 user 消息数达到 4 之后，
**每轮** ``before_iteration`` 会调一次轻量 judge LLM。这与 plan §2.2 所述
「每轮 before_iteration 时累计 / 每轮检测」一致，属设计内成本。

失败隔离
--------
四个回调的内部逻辑各自 ``try/except``，异常只 ``logger.warning`` / ``logger.exception``，
绝不上抛到 agent 主循环；``AgentHook._reraise`` 保持默认 ``False``。

装配
----
``AgentRunHookContext`` 不含 ``session_key``，因此优先通过
:func:`create_memory_extraction_hook_factory` 构造**按轮工厂**，由
``AgentTurnHookContext.session_key`` 解析出 per-session 的 extractor 与 runtime。
``MemoryExtractionHook(extractor, session_key, scratchpad_writer)`` 仍可直接实例化（单测）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.hook import (
    AgentHook,
    AgentHookContext,
    AgentRunHookContext,
    AgentTurnHookContext,
    AgentTurnHookFactory,
)
from nanobot.memory.intent import IntentType, classify_intent
from nanobot.memory.prompts import TOPIC_CHANGE_DETECTION_PROMPT
from nanobot.session.manager import Session

if TYPE_CHECKING:
    from nanobot.memory.extractor import MemoryExtractor
    from nanobot.memory.scratchpad_writer import ScratchpadWriter
    from nanobot.utils.llm_runtime import LLMRuntime


# ---------------------------------------------------------------------------
# 模块级后台任务强引用
# ---------------------------------------------------------------------------
# T5 的 fire-and-forget 任务不登记到 hook（per-turn 实例会被丢弃），事件循环只持有
# 任务的弱引用，必须在此保强引用防止未完成即被 GC。
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _spawn_background_task(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


# ---------------------------------------------------------------------------
# 消息处理工具（避免依赖 extractor 的私有函数）
# ---------------------------------------------------------------------------


def _content_text(content: Any) -> str:
    """把消息 content（str / content-block list / 其他）归一化为纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return str(content)


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    """返回消息列表中最后一条非空 user 文本；无则返回空串。"""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        text = _content_text(message.get("content")).strip()
        if text:
            return text
    return ""


def _user_messages(messages: list[dict[str, Any]]) -> list[str]:
    """按时间正序返回消息列表中所有非空 user 文本。"""
    collected: list[str] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        text = _content_text(message.get("content")).strip()
        if text:
            collected.append(text)
    return collected


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """容忍 ```json 围栏地从 LLM 输出中解析 JSON 对象；失败返回 None。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1 :]
        cleaned = cleaned.rstrip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    candidates = [cleaned]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


# ---------------------------------------------------------------------------
# MemoryExtractionHook
# ---------------------------------------------------------------------------


class MemoryExtractionHook(AgentHook):
    """在 agent 生命周期触发三层记忆提取的 AgentHook（plan §7.3）。"""

    #: ``on_finally`` 等待 T1 提取任务的上限（秒）；测试可覆盖实例属性以缩短。
    EXTRACTION_WAIT_TIMEOUT: float = 5.0
    #: T5 话题切换检测的 LLM 调用上限（秒）。
    TOPIC_CHANGE_TIMEOUT: float = 10.0
    #: T5 触发检测所需的 user 消息数（plan §2.2「≥4 轮」）。
    TOPIC_CHANGE_MIN_MESSAGES: int = 4
    #: ``current_focus`` 截断长度（plan §6.2）。
    FOCUS_MAX_CHARS: int = 200

    def __init__(
        self,
        extractor: MemoryExtractor,
        session_key: str,
        scratchpad_writer: ScratchpadWriter,
        *,
        runtime: LLMRuntime | None = None,
    ) -> None:
        super().__init__()
        self._extractor = extractor
        self._session_key = session_key
        self._scratchpad_writer = scratchpad_writer
        # 未显式注入时回落到 extractor 自带的 runtime（plan §7.3 无 runtime 参数）。
        self._runtime = runtime if runtime is not None else getattr(extractor, "runtime", None)

        # T5：允许下一次检测所需的最小 user 消息数。命中 CONTINUE 后 +1（同一转录
        # 状态不重复调 LLM）；命中 NEW 后 +4（同一实例内等效「清空缓冲需再累积 ≥4 条」）。
        # 注意：生产每轮新建实例，故该阈值跨轮重置 —— 见模块 docstring「代价」。
        self._next_check_count: int = self.TOPIC_CHANGE_MIN_MESSAGES

        # T1 任务（``on_finally`` 等待）。T5 后台任务见模块级 ``_BACKGROUND_TASKS``。
        self._pending_tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------
    # AgentHook 回调
    # ------------------------------------------------------------------

    async def before_iteration(self, context: AgentHookContext) -> None:
        """T5 话题切换检测（plan §2.2 / §10 Task 12）。"""
        try:
            await self._detect_topic_change(context)
        except Exception:
            logger.exception(
                "MemoryExtractionHook.before_iteration failed for session {}",
                self._session_key,
            )

    async def after_run(self, context: AgentRunHookContext) -> None:
        """T0 即时同步 + T1 异步提取（plan §2.3）。"""
        try:
            await self._apply_immediate_focus(context)
        except Exception:
            logger.exception(
                "MemoryExtractionHook.after_run T0 failed for session {}",
                self._session_key,
            )

        try:
            self._schedule_run_extraction(context)
        except Exception:
            logger.exception(
                "MemoryExtractionHook.after_run T1 failed for session {}",
                self._session_key,
            )

    async def on_error(self, context: AgentRunHookContext) -> None:
        """T0' 紧急保存 ``current_focus``（不调 LLM、不调 extractor）。"""
        try:
            message = _last_user_message(context.messages)
            if not message:
                return
            await self._scratchpad_writer.update_focus(
                self._session_key,
                message[: self.FOCUS_MAX_CHARS],
            )
        except Exception:
            logger.warning(
                "MemoryExtractionHook.on_error emergency focus save failed for session {}",
                self._session_key,
            )

    async def on_finally(self, context: AgentRunHookContext) -> None:
        """T1 等待登记中的提取任务完成（超时/异常仅 warning）。"""
        try:
            await self._await_pending_extractions()
        except Exception:
            logger.warning(
                "MemoryExtractionHook.on_finally failed for session {}",
                self._session_key,
            )

    # ------------------------------------------------------------------
    # T0：即时同步焦点
    # ------------------------------------------------------------------

    async def _apply_immediate_focus(self, context: AgentRunHookContext) -> None:
        message = _last_user_message(context.messages)
        if not message:
            return
        if classify_intent(message) == IntentType.CHAT:
            logger.debug("T0 skip: chat intent for session {}", self._session_key)
            return
        await self._scratchpad_writer.update_focus(
            self._session_key,
            message[: self.FOCUS_MAX_CHARS],
        )

    # ------------------------------------------------------------------
    # T5：话题切换检测
    # ------------------------------------------------------------------

    async def _detect_topic_change(self, context: AgentHookContext) -> None:
        user_messages = _user_messages(context.messages)
        count = len(user_messages)
        if count < self._next_check_count:
            return

        latest = user_messages[-1]
        recent = user_messages[-self.TOPIC_CHANGE_MIN_MESSAGES : -1]
        if not recent:
            return

        if await self._judge_topic_change(recent, latest):
            # CONTINUE：同一转录状态不重复检测，出现新消息后再判。
            self._next_check_count = count + 1
            return

        logger.info(
            "topic change detected for session {} (user messages={})",
            self._session_key,
            count,
        )

        # ① 旧 current_focus 滚入 active_projects（复用 update_focus 的归档语义）。
        try:
            await self._scratchpad_writer.update_focus(
                self._session_key,
                latest[: self.FOCUS_MAX_CHARS],
            )
        except Exception:
            logger.warning(
                "topic change focus rotation failed for session {}",
                self._session_key,
            )

        # ② fire-and-forget 触发提取（plan §10 Task 12：不等待、不登记）。
        #    extractor 无「仅 semantic 轨」开关，按整体 ``extract_session`` 退化执行；
        #    输入取当前会话转录，保留 assistant / tool 上下文。
        transcript = [dict(message) for message in context.messages]
        _spawn_background_task(self._run_extraction(transcript, source="session_end"))

        # ③ 抬高阈值：同一实例内需再累积 ≥4 条 user 消息才重新检测（跨轮重置）。
        self._next_check_count = count + self.TOPIC_CHANGE_MIN_MESSAGES

    async def _judge_topic_change(self, recent: list[str], latest: str) -> bool:
        """返回 ``True`` 表示同一话题（CONTINUE）；任何失败都按 CONTINUE 处理。"""
        runtime = self._runtime
        if runtime is None:
            logger.warning(
                "topic change detection skipped (no LLM runtime) for session {}",
                self._session_key,
            )
            return True

        # TOPIC_CHANGE_DETECTION_PROMPT 含字面 `{` `}`（JSON 示例），不能用 str.format。
        prompt = TOPIC_CHANGE_DETECTION_PROMPT.replace(
            "{recent_messages}",
            json.dumps(recent, ensure_ascii=False),
        ).replace("{latest_message}", latest)

        try:
            response = await asyncio.wait_for(
                runtime.provider.chat_with_retry(
                    model=runtime.model,
                    messages=[{"role": "user", "content": prompt}],
                    tools=[],
                    temperature=runtime.generation.temperature,
                    max_tokens=runtime.generation.max_tokens,
                    reasoning_effort=runtime.generation.reasoning_effort,
                ),
                timeout=self.TOPIC_CHANGE_TIMEOUT,
            )
        except Exception as exc:
            logger.warning(
                "topic change detection LLM failed for session {}: {}",
                self._session_key,
                exc,
            )
            return True

        content = getattr(response, "content", None)
        if not content or not content.strip():
            return True
        payload = _parse_json_object(content)
        if payload is None:
            logger.warning(
                "topic change detection returned unparseable JSON for session {}",
                self._session_key,
            )
            return True
        same_topic = payload.get("same_topic")
        if isinstance(same_topic, bool):
            return same_topic
        return True

    # ------------------------------------------------------------------
    # T1：异步提取任务
    # ------------------------------------------------------------------

    def _schedule_run_extraction(self, context: AgentRunHookContext) -> None:
        messages = list(context.messages)
        if not messages:
            return
        task = asyncio.create_task(self._run_extraction(messages, source="session_end"))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _await_pending_extractions(self) -> None:
        tasks = [task for task in self._pending_tasks if not task.done()]
        self._pending_tasks.clear()
        if not tasks:
            return

        try:
            _done, pending = await asyncio.wait(
                tasks,
                timeout=self.EXTRACTION_WAIT_TIMEOUT,
            )
        except Exception:
            logger.warning(
                "MemoryExtractionHook.on_finally wait failed for session {}",
                self._session_key,
            )
            return

        if not pending:
            return

        logger.warning(
            "memory extraction timed out after {}s for session {}; cancelled {} task(s)",
            self.EXTRACTION_WAIT_TIMEOUT,
            self._session_key,
            len(pending),
        )
        for task in pending:
            task.cancel()
        # 等待取消落地，避免遗留 pending task。
        await asyncio.gather(*pending, return_exceptions=True)

    async def _run_extraction(
        self,
        messages: list[dict[str, Any]],
        *,
        source: str,
    ) -> None:
        session = Session(
            key=self._session_key,
            messages=[dict(message) for message in messages],
        )
        try:
            await self._extractor.extract_session(session, source=source)
        except Exception:
            logger.warning(
                "memory extraction failed for session {} source={}",
                self._session_key,
                source,
            )
            return
        logger.info(
            "memory extraction finished for session {} source={}",
            self._session_key,
            source,
        )


# ---------------------------------------------------------------------------
# 按轮工厂（plan §7.1：由 loop.py 注册）
# ---------------------------------------------------------------------------


def create_memory_extraction_hook_factory(
    *,
    extractor_provider: Callable[[str], MemoryExtractor],
    scratchpad_writer: ScratchpadWriter,
    runtime_provider: Callable[[str], LLMRuntime | None] | None = None,
) -> AgentTurnHookFactory:
    """返回 ``AgentTurnHookFactory``：从 ``AgentTurnHookContext`` 取 ``session_key``
    构造 :class:`MemoryExtractionHook`。

    Args:
        extractor_provider: ``session_key -> MemoryExtractor``，为每个会话解析提取器。
        scratchpad_writer: 共享的 scratchpad 写入器。
        runtime_provider: 可选的 ``session_key -> LLMRuntime | None``，用于 T5 话题切换
            检测；缺省时回落 ``extractor.runtime``。

    Returns:
        ``AgentTurnHookFactory``：``session_key`` 缺失或 provider 失败时返回 ``None``，
        由 ``build_agent_turn_hook`` 静默跳过。
    """

    def _factory(context: AgentTurnHookContext) -> AgentHook | None:
        session_key = context.session_key
        if not session_key:
            return None

        try:
            extractor = extractor_provider(session_key)
        except Exception:
            logger.exception(
                "memory extraction hook factory: extractor_provider failed for {}",
                session_key,
            )
            return None
        if extractor is None:
            return None

        runtime: LLMRuntime | None = None
        if runtime_provider is not None:
            try:
                runtime = runtime_provider(session_key)
            except Exception:
                logger.exception(
                    "memory extraction hook factory: runtime_provider failed for {}",
                    session_key,
                )

        return MemoryExtractionHook(
            extractor,
            session_key,
            scratchpad_writer,
            runtime=runtime,
        )

    return _factory
