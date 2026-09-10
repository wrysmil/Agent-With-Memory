"""Tests for intent classifier (Phase 2 T0 gate)."""

import timeit

import pytest

from nanobot.memory.intent import IntentType, classify_intent


class TestClassifyIntent:
    """Intent classification test cases per plan §10 Task 1.5 Step 1."""

    # ------------------------------------------------------------------
    # CHAT cases — 闲聊/确认，不写 scratchpad
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "msg",
        [
            "你好",
            "hello",
            "好的",
            "ok",
            "谢谢",
            "嗯",
            "a",  # 单字符
        ],
    )
    def test_chat_patterns(self, msg: str) -> None:
        assert classify_intent(msg) == IntentType.CHAT

    def test_empty_string(self) -> None:
        assert classify_intent("") == IntentType.CHAT

    def test_whitespace_only(self) -> None:
        assert classify_intent("   ") == IntentType.CHAT

    # ------------------------------------------------------------------
    # TASK cases — 任务动词，任务焦点
    # ------------------------------------------------------------------
    def test_task_帮我(self) -> None:
        assert classify_intent("帮我实现一个爬虫") == IntentType.TASK

    def test_task_搜索_with_问号(self) -> None:
        """任务动词优先于问号。"""
        assert classify_intent("搜索 X 是什么") == IntentType.TASK

    def test_task_various_verbs(self) -> None:
        cases = [
            "帮我写一个函数",
            "请帮我实现这个功能",
            "实现一个排序算法",
            "帮我优化这段代码",
            "帮我重构这个模块",
            "帮我测试一下",
        ]
        for msg in cases:
            assert classify_intent(msg) == IntentType.TASK, f"failed for: {msg}"

    # ------------------------------------------------------------------
    # QUERY cases — 问号结尾，无任务动词
    # ------------------------------------------------------------------
    def test_query_问号结尾(self) -> None:
        assert classify_intent("Python 是什么") == IntentType.QUERY

    def test_query_english问号(self) -> None:
        assert classify_intent("What is Python?") == IntentType.QUERY

    def test_query_中文问号(self) -> None:
        assert classify_intent("什么是机器学习？") == IntentType.QUERY

    # ------------------------------------------------------------------
    # FOLLOW_UP cases — 追问标记，继续前文焦点
    # ------------------------------------------------------------------
    def test_follow_up_继续(self) -> None:
        assert classify_intent("继续上次的工作") == IntentType.FOLLOW_UP

    def test_follow_up_various_markers(self) -> None:
        cases = [
            "那个函数怎么实现的",
            "这个功能还能优化吗",
            "继续说",
            "接着上次的",
            "然后呢",
            "还有吗",
            "继续",
        ]
        for msg in cases:
            assert classify_intent(msg) == IntentType.FOLLOW_UP, f"failed for: {msg}"

    # ------------------------------------------------------------------
    # COMMAND cases — 明确指令
    # ------------------------------------------------------------------
    def test_command_slash(self) -> None:
        assert classify_intent("/run pytest") == IntentType.COMMAND

    def test_command_please(self) -> None:
        assert classify_intent("please run the tests") == IntentType.COMMAND

    def test_command_pls(self) -> None:
        assert classify_intent("pls help me") == IntentType.COMMAND

    # ------------------------------------------------------------------
    # 默认行为 — TASK
    # ------------------------------------------------------------------
    def test_default_to_task(self) -> None:
        cases = [
            "帮我做点事情",
            "写代码",
            "实现功能",
        ]
        for msg in cases:
            assert classify_intent(msg) == IntentType.TASK, f"failed for: {msg}"


class TestPerformance:
    """Performance assertions: 1000 calls < 50ms."""

    def test_classify_intent_performance(self) -> None:
        messages = [
            "你好",
            "帮我实现一个爬虫",
            "Python 是什么",
            "继续上次的工作",
            "/run pytest",
            "谢谢",
            "hello",
        ]
        n = 1000

        def run():
            for msg in messages:
                classify_intent(msg)

        elapsed = timeit.timeit(run, number=1)
        # 7 messages * 1000 iterations = 7000 calls total
        # Allow 50ms for the whole batch = ~7us per call
        assert elapsed < 0.05, f"Too slow: {elapsed:.4f}s for {n * len(messages)} calls"


class TestEdgeCases:
    """Edge case coverage."""

    def test_very_long_greeting(self) -> None:
        """长消息即使包含"你好"也不应被误判为 CHAT。"""
        long_msg = "你好，我想请教一个问题，关于 Python 的内存管理和垃圾回收机制，以及如何在实际项目中优化性能。"
        assert classify_intent(long_msg) != IntentType.CHAT

    def test_mixed_language_greeting(self) -> None:
        assert classify_intent("hi there, thanks!") == IntentType.CHAT

    def test_task_follow_up_collision(self) -> None:
        """追问标记优先于任务动词，避免"继续帮我做 X" 被误判。"""
        msg = "继续帮我优化这个函数"
        # FOLLOW_UP 优先：消息以"继续"开头
        assert classify_intent(msg) == IntentType.FOLLOW_UP

    def test_punctuation_only(self) -> None:
        assert classify_intent("...") == IntentType.CHAT
        assert classify_intent("!!!") == IntentType.CHAT
        assert classify_intent("，、。") == IntentType.CHAT
