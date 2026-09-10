"""Phase 2 轻量级意图识别器（T0 前置门）。

分类逻辑（优先级从高到低）：
1. 空/极短消息 → CHAT
2. 纯标点 → CHAT
3. 闲聊/确认/寒暄（整句仅由寒暄词构成）→ CHAT（不写 scratchpad）
4. 追问标记 → FOLLOW_UP
5. 明确指令 → COMMAND
6. 任务动词 → TASK
7. 疑问句（问号结尾 / 疑问词）→ QUERY
8. 默认 → TASK（最保守，避免漏写）
"""

from __future__ import annotations

import re
from enum import Enum


class IntentType(str, Enum):
    """意图类型枚举。T0 阶段仅使用 CHAT/TASK/COMMAND/QUERY/FOLLOW_UP。"""

    CHAT = "chat"
    QUERY = "query"
    TASK = "task"
    FOLLOW_UP = "follow_up"
    COMMAND = "command"


# ----------------------------------------------------------------------
# 正则模式（预编译，模块加载时仅执行一次）
# ----------------------------------------------------------------------

# 纯标点（中英文标点与空白）
_PUNCT_ONLY = re.compile(
    r"^[\s,，。、.!！?？；;：:\-—~～'\"“”‘’「」『』()（）\[\]{}<>《》…·*#@/\\|+=_]+$"
)

# 寒暄/确认词：整句仅由这些词 + 分隔符构成时判定为 CHAT
_CHAT_WORDS = (
    r"你好|您好|嗨|hi|hello|hey|yo|哈喽|早上好|中午好|下午好|晚上好|"
    r"好呀|好的|好啊|okay|ok|嗯|哦|噢|啊|呀|哈|呵|哈哈|笑死|"
    r"谢谢|感谢|谢啦|谢了|thx|thanks|辛苦了|打扰了|告辞|再见|拜拜|"
    r"收到|了解|明白|好嘞|可以|没问题|随便|无所谓|"
    r"sure|yeah|yep|nope|nah|there|you|all|much|very|so|and|too|as|well"
)
_CHAT_FULL = re.compile(
    rf"^(?:(?:{_CHAT_WORDS})[\s,.，。！!?？、~～'\"“”‘’…\-]*)+$",
    re.IGNORECASE,
)

# 任务动词：触发 TASK 类型（任一命中即可）
_TASK_VERBS = re.compile(
    r"帮我|请帮我|麻烦|"
    r"实现|写|创建|新建|生成|制作|开发|调试|修复|重构|优化|测试|部署|配置|"
    r"搜索|查找|查询|找|搜|打开|关闭|启动|停止|运行|执行|跑|调用|"
    r"删除|移动|重命名|复制|粘贴|下载|上传|保存|导出|"
    r"整理|清理|分析|总结|翻译|转换|处理|规划|设计|检查|验证|计算|估算|"
    r"推荐|建议|解释|回答|证明|审查|"
    r"\b(build|create|write|implement|run|execute|test|fix|optimize|refactor|"
    r"review|analyze|search|find|open|close|delete|download|upload|generate|"
    r"deploy|configure|install|debug)\b",
    re.IGNORECASE,
)

# 明确指令：slash command 或礼貌请求开头
_COMMAND_PATTERNS = re.compile(
    r"^(?:/\S+|please\b|pls\b|plz\b)",
    re.IGNORECASE,
)

# 追问标记：依赖上下文的引用词（前缀匹配）
_FOLLOW_UP_CJK = re.compile(
    r"^(?:那个|这个|它们|它|这些|那些|这|那|继续|接着|接下来|然后|"
    r"上次|之前|刚才|还有|再问|再说|再来)"
)
_FOLLOW_UP_EN = re.compile(
    r"^(?:that|this|it|these|those|continue|go on|keep going|and then|"
    r"what about|what else|anything else|again|once more|one more)\b",
    re.IGNORECASE,
)

# 疑问词 / 疑问句尾：触发 QUERY（仅在无任务动词时生效）
_QUERY_MARKERS = re.compile(
    r"是什么|什么|为什么|怎么|如何|多少|哪里|哪个|哪种|哪些|谁|"
    r"什么时候|吗|呢|是不是|能不能|可不可以|有没有"
)


def classify_intent(message: str) -> IntentType:
    """Lightweight heuristic intent classifier.

    执行耗时约 5µs/次（1000 次调用 < 50ms），无需 LLM 调用。
    适用于 T0 阶段快速判断是否写入 scratchpad。

    规则优先级：
        1. 空/极短消息 → CHAT
        2. 纯标点 → CHAT
        3. 寒暄/确认词整句 → CHAT
        4. 追问标记 → FOLLOW_UP
        5. 明确指令 → COMMAND
        6. 任务动词 → TASK
        7. 疑问句（问号结尾 / 疑问词）→ QUERY
        8. 默认 → TASK（最保守）

    Args:
        message: 用户输入的原始消息。

    Returns:
        IntentType 枚举值。
    """
    if not message or not message.strip():
        return IntentType.CHAT

    msg = message.strip()

    # 1. 极短消息（单字符）
    if len(msg) < 2:
        return IntentType.CHAT

    # 2. 纯标点
    if _PUNCT_ONLY.match(msg):
        return IntentType.CHAT

    # 3. 寒暄/确认（整句仅由寒暄词构成，长句不误判）
    if _CHAT_FULL.match(msg):
        return IntentType.CHAT

    # 4. 追问标记（优先于任务动词，避免"继续帮我做 X"被误判）
    if _FOLLOW_UP_CJK.match(msg) or _FOLLOW_UP_EN.match(msg):
        return IntentType.FOLLOW_UP

    # 5. 明确指令
    if _COMMAND_PATTERNS.match(msg):
        return IntentType.COMMAND

    # 6. 任务动词
    if _TASK_VERBS.search(msg):
        return IntentType.TASK

    # 7. 疑问句（无任务动词时）
    if msg.endswith(("?", "？")) or _QUERY_MARKERS.search(msg):
        return IntentType.QUERY

    # 8. 默认：最保守策略，视为 TASK
    return IntentType.TASK
