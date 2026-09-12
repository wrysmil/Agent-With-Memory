"""Phase 2 防污染过滤器：任务产物 / AI 自答 / 精确去重 / 相似度去重。

Public API:
    FilterResult
    is_task_artifact()
    is_ai_self_talk()
    compute_content_hash()
    ngram_similarity()
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 正则规则
# ---------------------------------------------------------------------------

# 任务指示器：用户正在请求 AI 执行操作，而非陈述事实
_TASK_INDICATORS: list[re.Pattern[str]] = [
    # 中文任务请求
    re.compile(r"帮我[做给查找]|帮我生成|帮我写|帮我创建|帮我搜索|帮我查找"),
    re.compile(r"请帮我|能不能帮我|可以帮我"),
    re.compile(r"帮我分析一下|帮我整理一下|帮我总结一下"),
    re.compile(r"帮我翻译|帮我解释|帮我推荐"),
    re.compile(r"帮我设计|帮我规划|帮我安排"),
    re.compile(r"帮我做|帮我执行|帮我完成"),
    # 中文任务关键词
    re.compile(r"搜索[^\s]{1,20}并|搜索[^\s]{1,20}后|搜索[^\s]{1,20}的"),
    re.compile(r"生成[^\s]{1,30}报告|生成[^\s]{1,30}列表|生成[^\s]{1,30}代码"),
    re.compile(r"写[^\s]{1,30}代码|写[^\s]{1,30}脚本|写[^\s]{1,30}文档"),
    re.compile(r"查找[^\s]{1,30}文件|查找[^\s]{1,30}信息"),
    # 英文常见任务请求
    re.compile(r"\b(search|find|look up|generate|create|write|build|make|do)\b.*\b(for me|for me,|please)\b", re.IGNORECASE),
    re.compile(r"\bcan you\b.*\b(search|find|generate|create|write)\b", re.IGNORECASE),
    re.compile(r"\bplease (search|find|generate|create|write|look up)\b", re.IGNORECASE),
    re.compile(r"\bi want you to\b", re.IGNORECASE),
]

# AI 自答指示器：内容来自 AI 自身视角，而非用户输入
_AI_SELF_TALK: list[re.Pattern[str]] = [
    # 中文 AI 自我引用
    re.compile(r"^作为AI|^作为一个人工智能|^作为语言模型"),
    re.compile(r"^我建议|^我推荐|^我认为|^我分析"),
    re.compile(r"^根据我的|^基于我的|^在我的分析中"),
    re.compile(r"^AI(语言模型|助手|系统)|^(大型)?语言模型"),
    re.compile(r"^我的建议是|^以下是|^下面"),
    re.compile(r"^根据搜索结果|^搜索显示|^查询结果显示"),
    # 英文 AI 自我引用
    re.compile(r"^as an? (ai|language model|assistant)", re.IGNORECASE),
    re.compile(r"^i (suggest|recommend|analyze|think|believe)\b", re.IGNORECASE),
    re.compile(r"^based on (my|the) (analysis|knowledge|understanding)", re.IGNORECASE),
    re.compile(r"^here(?:'s| is) (my|the) (suggestion|recommendation|advice)", re.IGNORECASE),
    re.compile(r"^according to (my|the) (analysis|knowledge)", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class FilterResult:
    """过滤器判定结果。"""

    keep: bool
    reason: str
    merged_with: str | None = None

    # 预定义 reason 常量
    REASON_OK = "ok"
    REASON_TASK_ARTIFACT = "task_artifact"
    REASON_AI_SELF_TALK = "ai_self_talk"
    REASON_EXACT_DUPLICATE = "exact_duplicate"
    REASON_HIGH_SIMILARITY = "high_similarity"


# ---------------------------------------------------------------------------
# 内容过滤器
# ---------------------------------------------------------------------------


def is_task_artifact(content: str) -> bool:
    """判断内容是否为任务产物（用户请求 AI 执行操作），任务产物不应该进入记忆中。

    任务产物是指用户在请求 AI 完成某项操作，而非陈述一个事实或偏好。
    这类内容不应存入记忆系统，因为它们是过程性而非陈述性的。

    Args:
        content: 待检测的文本内容。

    Returns:
        True 如果内容是任务请求/产物，False 如果是普通陈述。
    """
    if not content or not content.strip():
        return False

    stripped = content.strip()

    for pattern in _TASK_INDICATORS:
        if pattern.search(stripped):
            return True

    return False


def is_ai_self_talk(content: str) -> bool:
    """判断内容是否为 AI 自答（AI 视角的回应，而非用户输入）。

    AI 自答是指 AI 以第一人称生成的回应性内容，这类内容：
    1. 混淆了记忆的来源归属
    2. 可能是中间推理过程，不应固化为记忆
    3. 会造成记忆系统中的"镜像污染"

    Args:
        content: 待检测的文本内容。

    Returns:
        True 如果内容是 AI 自答，False 如果是用户输入。
    """
    if not content or not content.strip():
        return False

    stripped = content.strip()

    for pattern in _AI_SELF_TALK:
        if pattern.search(stripped):
            return True

    return False


# ---------------------------------------------------------------------------
# 哈希去重
# ---------------------------------------------------------------------------


def compute_content_hash(content: str, subject: str, predicate: str) -> str:
    """计算内容的 SHA-1 哈希值（用于精确去重）。

    哈希因子包含：
    - content: 记忆主体内容
    - subject: 主语（避免"用户喜欢 X"和"用户不喜欢 X"被错误去重）
    - predicate: 谓词（进一步区分语义）

    Args:
        content: 记忆内容。
        subject: 主语。
        predicate: 谓词。

    Returns:
        SHA-1 hex digest of ``content|subject|predicate`` (40 chars).
    """
    normalized = f"{content.strip()}|{subject.strip()}|{predicate.strip()}"
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# N-Gram 相似度
# ---------------------------------------------------------------------------


def _extract_ngrams(text: str, n: int = 3) -> set[str]:
    """提取字符级 n-gram 集合。

    Args:
        text: 输入文本。
        n: n-gram 大小，默认 3。

    Returns:
        n-gram 字符串集合。
    """
    text = text.strip()
    if len(text) < n:
        if text:
            return {text}
        return set()

    return {text[i:i + n] for i in range(len(text) - n + 1)}


def ngram_similarity(a: str, b: str, n: int = 3) -> float:
    """计算两个文本的字符级 n-gram Jaccard 相似度。

    Jaccard = |A ∩ B| / |A ∪ B|

    阈值约定：
    - > 0.8: high_similarity（高度相似，可能重复）
    - > 0.3 且 ≤ 0.8: candidate_merge（候选合并，Phase 2 仅标记）
    - ≤ 0.3: 无需处理

    Args:
        a: 第一个文本。
        b: 第二个文本。
        n: n-gram 大小，默认 3。

    Returns:
        0.0 到 1.0 之间的相似度分数。
    """
    if not a or not b:
        return 0.0

    set_a = _extract_ngrams(a, n)
    set_b = _extract_ngrams(b, n)

    if not set_a or not set_b:
        return 0.0

    intersection = len(set_a & set_b)
    union = len(set_a | set_b)

    if union == 0:
        return 0.0

    return intersection / union


# ---------------------------------------------------------------------------
# 给话题预筛复用的判定辅助（plan 2026-09-12 S5）
#
# 注：原计划复用模块内 _CHAT_FULL/_FOLLOW_UP_CJK/_FOLLOW_UP_EN 私有正则，
# 但 filters.py 经过一轮重构后这些私有变量已移除。为避免反向耦合，
# 这里改为弱判定（短消息或以特定追问词开头即视为预筛跳过），
# 实际预筛逻辑在 nanobot.memory.topic_prefilter 内独立实现。
# ---------------------------------------------------------------------------


def is_chat_only(content: str) -> bool:
    """整句仅由短寒暄/控制词构成（弱判定，预筛的辅助信号之一）。"""
    if not content or not content.strip():
        return False
    text = content.strip().lower()
    return len(text) <= 2 and text in {
        "好", "好呀", "嗯", "哦", "ok", "no", "hi", "yo", "嗨",
    }


def starts_with_follow_up(content: str) -> bool:
    """以追问/承接词开头（弱判定，仅作为预筛辅助信号）。"""
    if not content or not content.strip():
        return False
    s = content.strip()
    heads = (
        "那个", "这个", "它们", "它", "这些", "那些", "这", "那",
        "继续", "接着", "接下来", "然后", "上次", "之前", "刚才", "还有",
        "再问", "再说", "再来", "that", "this", "it", "these", "those",
        "continue", "again", "once more",
    )
    return any(s.startswith(h) for h in heads)
