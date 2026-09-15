"""检索前置守卫：跳过空/控制词/超短；反注入清洗。

符合 docs/记忆系统/记忆检索.md Layer 4 Active Retrieval 入口 Gate 要求。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 控制词白名单（不含 ? ！吗 呢 吧 等短提示词字符）
_CONTROL_ONLY = frozenset({
    "好", "嗯", "哦", "啊", "呃", "嘿", "哎",
    "可以", "行", "继续", "收到", "好的", "ok", "okay",
    "yes", "no", "yep", "nope", "stop", "halt",
    "thanks", "thank", "thx", "ty",
})

# 含这些字符的短查询不触发 too_short 跳过（如 "天气?"）
_KEEP_SHORT_HINTS = ("?", "？", "吗", "呢", "吧", "啥")

# 记忆意图关键词：**仅**用于 ≤3 字的极短文本。极短文本本身歧义太大
# （「记忆」2 字、「偏好」2 字），命中这些锚点才放行；否则按 ``too_short`` 跳过。
#
# 2026-09-15 代码审查 I-1 收窄：原白名单含「我的」「个人」「习惯」「历史」「过去」
# 等**所有格/口语**词，会把「我的天哪」「个人觉得」「习惯就好」判为记忆意图；而
# 新写入的记忆因 reranker 的冷启动豁免（recency ≥ 0.99 时绕过最小阈值）会被真的
# 注入 system prompt。收窄为**强锚点**，并只作用于 ≤3 字路径。
_MEMORY_INTENT_HINTS = (
    "记忆", "记得", "回忆", "我说过", "提到过", "偏好",
)

# 反注入正则黑名单（section header 段落用 re.split 方式精确处理）
_INJECTION_BLOCK_PATTERNS = (
    r"(?is)<vault-context>.*?</vault-context>",
    r"(?is)<memory>.*?</memory>",
    r"(?is)<long-term-memory>.*?</long-term-memory>",
)
# Section-header 段落专用：split + 保留尾部内容
_SECTION_SPLITTER = re.compile(r"(?im)^##\s*(?:相关记忆|核心记忆|长期记忆)\s*$")


@dataclass(frozen=True)
class PreparedQuery:
    """预处理结果容器。"""
    skip: bool
    reason: str
    cleaned_query: str


class MemoryQueryPreprocessor:
    """检索前置守卫：决定是否跳过 + 清洗历史 Prompt 注入。"""

    # 判断用户的这次查询是否值得走记忆检索

   # should_skip_retrieval 是一个检索前置过滤方法。它在调用向量检索之前，用纯规则判断查询是否值得检索：
    # 空文本、控制词（"ok"/"hi" 等）、极短文本或缺乏上文支撑的短查询会被直接跳过，
    # 返回 (True, reason)；其余情况返回 (False, "")，继续走后续的检索流程。
    @classmethod
    def should_skip_retrieval(cls, query: str | None, recent_messages: list) -> tuple[bool, str]:
        text = (query or "").strip()
        if not text:
            return True, "empty"
        lowered = text.lower()
        if lowered in cls._CONTROL_ONLY:
            return True, "control_only"
        has_short_hint = any(h in text for h in _KEEP_SHORT_HINTS)
        has_memory_intent = any(h in text for h in _MEMORY_INTENT_HINTS)
        # 仅剩一道长度闸：≤3 字且无任何提示字符 / 记忆锚点 → 跳过。
        # 「记忆」「偏好」这类 2 字查询由 ``_MEMORY_INTENT_HINTS`` 放行，
        # 保证 ``memory_search`` 工具由 LLM 传入的短词仍能检索。
        if len(text) <= 3 and not has_short_hint and not has_memory_intent:
            return True, "too_short"
        # 2026-09-15 代码审查 I-1：原 ``short_without_context`` 分支
        #（``len(text) <= 12 and not recent_messages``）**已删除**。
        #
        # 删它的理由不是「调参没调好」，而是该分支的前提**恒真**：
        # ``loop.py`` 每轮传 ``list(ctx.history)``，而**会话首轮**的 history
        # 必为空 → 该分支在每条会话的第一条消息上无条件生效，把「项目进度如何」
        # 「推荐几个选题方向」这类正常短消息整类拦掉（RCA 根因 2，即用户报告的
        # 症状）。首轮无历史是**必然**而非「缺乏上下文」，用它当跳过依据在逻辑上
        # 就是错的。Layer 4 的设计本就是「每轮自动注入」，短消息的取舍应交给
        # 下游 reranker 阈值与 formatter token 预算，而不是在这里按字数一刀切。
        #
        # ``recent_messages`` 参数保留在签名中（调用方与既有测试均传它），
        # 但已不参与跳过判定。
        return False, ""

    @classmethod
    def clean_query(cls, query: str) -> str:
        # 1. 先处理自闭合标签块
        out = query
        for pat in _INJECTION_BLOCK_PATTERNS:
            out = re.sub(pat, "", out)
        # 2. 用 split 方式移除 section header 段落，保留 header 后的尾部内容
        parts = _SECTION_SPLITTER.split(out)
        # sections[0] 是 header 之前的内容；后续每个 part 前的 header 已被移除
        # 但每个 part 的开头可能含 header 行的尾部空白，我们strip每段
        out = "".join(p.strip() for p in parts).strip()
        return out

    @classmethod
    def prepare(cls, query: str, recent_messages: list) -> PreparedQuery:
        cleaned = cls.clean_query(query or "")
        skip, reason = cls.should_skip_retrieval(cleaned, recent_messages)
        return PreparedQuery(skip=skip, reason=reason, cleaned_query=cleaned)

    _CONTROL_ONLY = _CONTROL_ONLY  # 暴露供测试遍历
