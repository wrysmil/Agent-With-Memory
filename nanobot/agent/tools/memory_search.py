"""LLM-callable tool for proactive long-term memory retrieval.

This is the "active recall" path (vs. Layer 4 automatic injection):
- Layer 4: passive, every turn, capped by token budget
- Tool: proactive, LLM-decided, no token budget cap, good for "search 3 weeks ago"
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, ClassVar

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.context import ToolContext, current_request_session_key
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from nanobot.memory.retrieval import RetrievalEngine


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema(
            "记忆检索关键词（可包含主题、工具名、项目名等）",
            min_length=1,
            max_length=200,
        ),
        max_tokens=IntegerSchema(
            description="最大输出 token 数（默认 500）",
            minimum=50,
            maximum=2000,
        ),
        required=["query"],
    )
)
class MemorySearchTool(Tool):
    """Proactive memory search tool callable by the LLM.

    Provide a ``retrieval_engine_provider`` that returns the current
    :class:`RetrievalEngine` instance, or ``None`` when retrieval is
    disabled.  The provider is called at every ``execute()`` so that the
    engine is always fresh (e.g. when the loop re-creates it after config
    changes).
    """

    name: ClassVar[str] = "memory_search"
    description: ClassVar[str] = (
        "从长期记忆中主动检索相关内容。当用户明确要求查找过去的信息、"
        "特定项目细节、历史决策或工具使用经验时调用此工具。"
        "此工具与 Layer 4 自动注入互补：自动注入每轮无感覆盖，"
        "此工具供 LLM 在多步推理中显式补查，不受 token 预算限制。"
        "返回格式化的 markdown 记忆列表。"
    )

    def __init__(
        self,
        retrieval_engine_provider: Callable[[], "RetrievalEngine | None"],
    ) -> None:
        self._get_engine = retrieval_engine_provider

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:

        def provider() -> "RetrievalEngine | None":  # noqa: F401
            # RetrievalEngine type needed for annotation; imported inside to avoid circular
            attrs = getattr(ctx, "attributes", {})
            return attrs.get("retrieval_engine")

        return cls(retrieval_engine_provider=provider)

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        attrs = getattr(ctx, "attributes", {})
        return attrs.get("retrieval_engine") is not None

    async def execute(self, *, query: str, max_tokens: int = 500, **kwargs: Any) -> str:
        engine = self._get_engine()
        if engine is None:
            return ""

        recent: list[dict[str, Any]] = []
        session_key = current_request_session_key()
        if session_key and hasattr(self, "_sessions"):
            session = self._sessions.get_cached(session_key)
            if session is not None:
                recent = session.messages[-10:]  # last 10 messages as context

        try:
            result = await engine.retrieve(
                query=query,
                recent_messages=recent,
                max_tokens=max_tokens,
            )
            return result if result else ""
        except Exception:
            return ""

    @property
    def read_only(self) -> bool:
        return True
