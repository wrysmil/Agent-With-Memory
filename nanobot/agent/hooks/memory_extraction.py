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


# WU-A: per-session idle 定时器强引用表。``MemoryExtractionHook._arm_idle_timer``
# 每次 after_run 都会取消旧任务并创建新任务,事件循环只持有任务的弱引用,
# 必须在此保强引用防止未完成即被 GC。键为 ``session_key``,值为当前正在等
# 待/执行的空闲增量提取任务。
_PENDING_IDLE_TIMERS: dict[str, asyncio.Task[Any]] = {}


def _cancel_pending_idle_timer(session_key: str) -> asyncio.Task[Any] | None:
    """取消并弹出指定会话的 idle 任务;返回旧任务以便调用方 await 取消完成。

    - 若该会话当前没有 pending 任务,返回 ``None``(不做任何事)。
    - 若任务已经完成,从 dict 中弹出即可,不再 cancel(避免 ``InvalidStateError``)。
    """
    task = _PENDING_IDLE_TIMERS.pop(session_key, None)
    if task is None or task.done():
        return None
    task.cancel()
    return task


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

    #: WU-A: idle 定时器阈值(秒)。每轮 after_run 重置定时器,空闲超过该时长则
    #: 触发 ``MemoryExtractor.run_idle_extraction``。测试可覆盖实例属性以缩短。
    IDLE_THRESHOLD_SECONDS: float = 600.0
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
        memory_enabled_provider: "Callable[[], bool] | None" = None,
        idle_seconds: float | None = None,
    ) -> None:
        super().__init__()
        self._extractor = extractor
        self._session_key = session_key
        self._scratchpad_writer = scratchpad_writer
        # 未显式注入时回落到 extractor 自带的 runtime（plan §7.3 无 runtime 参数）。
        self._runtime = runtime if runtime is not None else getattr(extractor, "runtime", None)
        # Hot-switchable user-facing memory toggle. ``None`` preserves legacy
        # behaviour (always-on when the loop registered extraction). When set,
        # every callback short-circuits to a no-op while the toggle is off so
        # that flipping it in the WebUI takes effect without restarting the
        # gateway.
        self._memory_enabled_provider = memory_enabled_provider

        # WU-A: 允许通过构造参数覆盖 idle 阈值(供 AgentDefaults.memory_idle_seconds
        # 等配置源注入)。实例属性优先于类属性。
        if idle_seconds is not None:
            self.IDLE_THRESHOLD_SECONDS = float(idle_seconds)

        # T5：允许下一次检测所需的最小 user 消息数。命中 CONTINUE 后 +1（同一转录
        # 状态不重复调 LLM）；命中 NEW 后 +4（同一实例内等效「清空缓冲需再累积 ≥4 条」）。
        # 注意：生产每轮新建实例，故该阈值跨轮重置 —— 见模块 docstring「代价」。
        self._next_check_count: int = self.TOPIC_CHANGE_MIN_MESSAGES

        # WU-A: idle 定时器状态。每个 hook 实例只对应一个 session_key,因此无需
        # 维护 _pending_tasks 集合;模块级 _PENDING_IDLE_TIMERS 按 session_key
        # 维护。on_finally 直接取消并弹出当前会话键。

        # S5: 话题预筛 + 间隔节流（plan 2026-09-12）
        from nanobot.memory.topic_prefilter import TopicChangeGate
        self._topic_gate = TopicChangeGate(interval_seconds=60)

        # S1: 会话结束编排器（feature flag 默认关闭，遵循 spec §7.3）
        self._session_end_enabled: bool = getattr(
            type(self), "SESSION_END_ENABLED", True
        )
        if self._session_end_enabled:
            from nanobot.memory.orchestrator import SessionEndOrchestrator
            self._session_end_orchestrator = SessionEndOrchestrator(
                extractor=self._build_orchestrator_extractor(),
                scratchpad_writer=self._scratchpad_writer,
                enable_track2=getattr(type(self), "S3_TRACK2_ENABLED", False),
                enable_scratchpad_reformat=getattr(
                    type(self), "S4_SCRATCHPAD_REFORMAT_ENABLED", False
                ),
            )
        else:
            self._session_end_orchestrator = None

    def _memory_disabled(self) -> bool:
        """Return ``True`` when the user-facing memory toggle is wired and off.

        When no provider is wired (legacy behaviour) this returns ``False`` so
        callbacks proceed unchanged.
        """
        provider = self._memory_enabled_provider
        if provider is None:
            return False
        try:
            return not provider()
        except Exception:
            logger.warning(
                "MemoryExtractionHook: memory_enabled_provider raised; treating as enabled"
            )
            return False

    # ------------------------------------------------------------------
    # AgentHook 回调
    # ------------------------------------------------------------------

    async def before_iteration(self, context: AgentHookContext) -> None:
        """T5 话题切换检测（plan §2.2 / §10 Task 12）。"""
        if self._memory_disabled():
            return
        try:
            await self._detect_topic_change(context)
        except Exception:
            logger.exception(
                "MemoryExtractionHook.before_iteration failed for session {}",
                self._session_key,
            )

    async def after_run(self, context: AgentRunHookContext) -> None:
        """T0 即时同步 + T1 idle 定时器启动（plan §2.3 / WU-A §3）。"""
        if self._memory_disabled():
            return
        try:
            await self._apply_immediate_focus(context)
        except Exception:
            logger.exception(
                "MemoryExtractionHook.after_run T0 failed for session {}",
                self._session_key,
            )

        try:
            self._arm_idle_timer(context)
        except Exception:
            logger.exception(
                "MemoryExtractionHook.after_run T1 arm failed for session {}",
                self._session_key,
            )

    async def on_error(self, context: AgentRunHookContext) -> None:
        """T0' 紧急保存 ``current_focus``（不调 LLM、不调 extractor）。"""
        if self._memory_disabled():
            return
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
        """WU-A: idle 定时器 fire-and-forget 取消 + S1 会话结束兜底触发。

        行为变更(WU-A Task 3):
        - 不再 await 任何 pending 提取任务(旧的 EXTRACTION_WAIT_TIMEOUT 5s 等待
          路径已删除);改为 ``on_finally`` 时直接取消当前会话的 idle 定时器。
        - idle 定时器本身是 fire-and-forget,即使被取消也不会向用户感知。
        - S1 的 SessionEndOrchestrator 兜底触发保留(进程退出兜底)。
        """
        if self._memory_disabled():
            return

        # 取消当前会话的 idle 定时器(若有)。fire-and-forget:不等待取消完成。
        try:
            _cancel_pending_idle_timer(self._session_key)
        except Exception:
            logger.warning(
                "MemoryExtractionHook.on_finally cancel idle timer failed for session {}",
                self._session_key,
            )

        # S1: 进程退出兜底触发 SessionEndEvent（fire-and-forget）
        if self._session_end_orchestrator is not None:
            from nanobot.memory.session_end_event import SessionEndEvent, SessionEndReason
            event = SessionEndEvent(
                session_key=self._session_key,
                reason=SessionEndReason.PROCESS_SHUTDOWN,
                transcript=list(context.messages),
            )
            try:
                _spawn_background_task(
                    self._session_end_orchestrator.run(event)
                )
            except Exception:
                logger.warning(
                    "SessionEnd orchestrator dispatch failed for session {}",
                    self._session_key,
                )

    def _build_orchestrator_extractor(self) -> Any:
        """构造编排器所需的 extractor 适配形态。

        MemoryExtractor 已具备 generate_episode（Task 2 已接入）；
        ProfileExtractor/ExperienceExtractor 是新模块，由调用方按需构造。
        这里返回的适配对象暴露 3 个 async 方法；对不存在的属性返回 no-op。
        """
        ext = self._extractor

        class _Adapter:
            pass

        adapter = _Adapter()

        async def _noop(*a, **k):
            return None

        async def _noop_pair(*a, **k):
            return [], []

        adapter.generate_episode = getattr(ext, "generate_episode", _noop)
        adapter.extract_user_profile = getattr(ext, "extract_user_profile", _noop_pair)
        adapter.extract_experience = getattr(ext, "extract_experience", _noop)
        return adapter

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
        # T5 已禁用：话题切换检测会导致每轮额外 LLM 调用，性能代价大于收益。
        # 记忆提取仍通过 T1（每轮异步）+ 会话结束完整提取完成。
        return

    def _compute_incremental_start_index(self, messages: list[dict[str, Any]]) -> int:
        """返回倒数第二条非空 user 消息之后的起始下标。

        增量抽取保留最新 user 消息及其后的 assistant / tool 消息，不重扫更早的
        历史。若找不到倒数第二条非空 user 消息（例如只有一条 user 消息），返回 0。
        """
        user_indices: list[int] = []
        for index, message in enumerate(messages):
            if message.get("role") != "user":
                continue
            text = _content_text(message.get("content")).strip()
            if not text:
                continue
            user_indices.append(index)

        if len(user_indices) < 2:
            return 0
        return user_indices[-2] + 1

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
    # WU-A T1：idle 定时器增量提取
    # ------------------------------------------------------------------

    def _arm_idle_timer(self, context: AgentRunHookContext) -> None:
        """注册或重置当前会话的 idle 定时器。

        - 若该 session_key 已有 pending idle 任务(尚未完成),先取消;
          ``_wait_then_extract_idle`` 会捕获 ``CancelledError`` 并直接返回。
        - 新任务的等待时长 = ``self.IDLE_THRESHOLD_SECONDS``(可在 ``__init__``
          或运行时通过覆盖类/实例属性调整)。
        - 完成后通过 ``add_done_callback`` 把任务从 ``_PENDING_IDLE_TIMERS``
          弹出,防止 dict 无限增长。
        """
        # 取消旧任务(若有)。
        _cancel_pending_idle_timer(self._session_key)
        # 启动新任务。
        task = asyncio.create_task(self._wait_then_extract_idle(context))
        _PENDING_IDLE_TIMERS[self._session_key] = task

        def _drop(_t: asyncio.Task[Any]) -> None:
            # 仅当当前 dict 仍持有此任务时才 pop,避免覆盖更近一次的 arm。
            current = _PENDING_IDLE_TIMERS.get(self._session_key)
            if current is _t:
                _PENDING_IDLE_TIMERS.pop(self._session_key, None)

        task.add_done_callback(_drop)

    async def _wait_then_extract_idle(self, context: AgentRunHookContext) -> None:
        """sleep 阈值秒后调用 ``run_idle_extraction``;被 cancel 则直接返回。"""
        try:
            await asyncio.sleep(self.IDLE_THRESHOLD_SECONDS)
        except asyncio.CancelledError:
            # 新一轮 after_run 触发了 arm_idle_timer 重置,定时器被取消属正常路径。
            return
        await self._run_idle_extraction(context)

    async def _run_idle_extraction(self, context: AgentRunHookContext) -> None:
        """构造 Session 并调用 ``MemoryExtractor.run_idle_extraction``。

        失败隔离:任何异常仅 warning,绝不上抛。
        """
        session = Session(
            key=self._session_key,
            messages=[dict(message) for message in context.messages],
        )
        try:
            await self._extractor.run_idle_extraction(session)
        except Exception:
            logger.warning(
                "idle memory extraction failed for session {}",
                self._session_key,
            )
            return
        logger.info(
            "idle memory extraction finished for session {}",
            self._session_key,
        )

    async def _run_incremental_extraction(
        self,
        messages: list[dict[str, Any]],
        last_extracted_index: int,
    ) -> None:
        """在后台执行话题切换触发的增量抽取，失败仅 warning。"""
        session = Session(
            key=self._session_key,
            messages=[dict(message) for message in messages],
        )
        try:
            await asyncio.wait_for(
                self._extractor.extract_incremental(session, last_extracted_index),
                timeout=30.0,
            )
        except Exception:
            logger.warning(
                "incremental memory extraction failed for session {}",
                self._session_key,
            )
            return
        logger.info(
            "incremental memory extraction finished for session {}",
            self._session_key,
        )


# ---------------------------------------------------------------------------
# 按轮工厂（plan §7.1：由 loop.py 注册）
# ---------------------------------------------------------------------------


def create_memory_extraction_hook_factory(
    *,
    extractor_provider: Callable[[str], MemoryExtractor],
    scratchpad_writer: ScratchpadWriter | None = None,
    scratchpad_writer_for_key: Callable[[str], ScratchpadWriter] | None = None,
    runtime_provider: Callable[[str], LLMRuntime | None] | None = None,
    memory_enabled_provider: Callable[[], bool] | None = None,
    idle_seconds: float | None = None,
) -> AgentTurnHookFactory:
    """返回 ``AgentTurnHookFactory``：从 ``AgentTurnHookContext`` 取 ``session_key``
    构造 :class:`MemoryExtractionHook`。

    Args:
        extractor_provider: ``session_key -> MemoryExtractor``，为每个会话解析提取器。
        scratchpad_writer: 共享的 scratchpad 写入器（向后兼容单实例场景，如单测）。
        scratchpad_writer_for_key: ``session_key -> ScratchpadWriter``，FIX-1 Sec-C-α
            推荐入口；优先使用本参数以实现 per-session 隔离。若两者同时给出，
            ``scratchpad_writer_for_key`` 胜出。
        runtime_provider: 可选的 ``session_key -> LLMRuntime | None``，用于 T5 话题切换
            检测；缺省时回落 ``extractor.runtime``。
        idle_seconds: WU-A 可选的 idle 阈值(秒),转发给 ``MemoryExtractionHook``。
            通常来自 ``AgentDefaults.memory_idle_seconds``。

    Returns:
        ``AgentTurnHookFactory``：``session_key`` 缺失或 provider 失败时返回 ``None``，
        由 ``build_agent_turn_hook`` 静默跳过。
    """
    writer_factory = scratchpad_writer_for_key
    if writer_factory is None:
        if scratchpad_writer is None:
            raise ValueError(
                "create_memory_extraction_hook_factory requires either "
                "scratchpad_writer or scratchpad_writer_for_key"
            )
        # 共享单实例：所有会话共用同一 writer（向后兼容单测 / 单用户场景）。
        writer_factory = lambda _key: scratchpad_writer  # noqa: E731

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

        try:
            writer = writer_factory(session_key)
        except Exception:
            logger.exception(
                "memory extraction hook factory: scratchpad_writer_for_key failed for {}",
                session_key,
            )
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
            writer,
            runtime=runtime,
            memory_enabled_provider=memory_enabled_provider,
            idle_seconds=idle_seconds,
        )

    return _factory
