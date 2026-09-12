"""话题预筛 + 间隔节流单测（S5）。"""
from __future__ import annotations

import time

import pytest

from nanobot.memory.intent import IntentType
from nanobot.memory.topic_prefilter import (
    PrefilterResult,
    TopicChangeGate,
    compute_topic_hash,
)


def test_prefilter_short_message_returns_skip():
    gate = TopicChangeGate()
    assert gate.prefilter(message="", recent=[]) == PrefilterResult.SKIP
    assert gate.prefilter(message="ok", recent=[]) == PrefilterResult.SKIP


def test_prefilter_chat_intent_returns_skip():
    gate = TopicChangeGate(intent_classifier=lambda m: IntentType.CHAT)
    assert gate.prefilter(message="今天天气真好", recent=["今天下雨"]) == PrefilterResult.SKIP


def test_prefilter_follow_up_returns_skip():
    gate = TopicChangeGate()
    assert gate.prefilter(message="继续帮我做完", recent=["我们刚才在配置 PATH"]) == PrefilterResult.SKIP


def test_prefilter_length_jump_small_returns_skip():
    gate = TopicChangeGate()
    recent = ["x" * 200]
    assert gate.prefilter(message="y" * 50, recent=recent) == PrefilterResult.SKIP


def test_prefilter_passes_through_for_real_topic_change():
    gate = TopicChangeGate()
    # recent 最后一条长度足够，避免长度突变规则误拦
    assert gate.prefilter(
        message="帮我把数据库从 MySQL 迁到 Postgres",
        recent=[
            "我们刚刚在讨论 React 组件设计与渲染性能优化的方案",
            "接下来要实现一个懒加载机制以减少初始渲染的卡顿",
        ],
    ) == PrefilterResult.PASS


def test_interval_blocks_within_60s_same_session():
    gate = TopicChangeGate(interval_seconds=60)
    assert gate.allow_fire(session_key="s1", topic_hash="h1") is True
    assert gate.allow_fire(session_key="s1", topic_hash="h1") is False


def test_interval_allows_different_session():
    gate = TopicChangeGate(interval_seconds=60)
    assert gate.allow_fire(session_key="s1", topic_hash="h1") is True
    assert gate.allow_fire(session_key="s2", topic_hash="h1") is True


def test_interval_allows_after_window_expires():
    gate = TopicChangeGate(interval_seconds=1)
    assert gate.allow_fire(session_key="s1", topic_hash="h1") is True
    time.sleep(1.1)
    assert gate.allow_fire(session_key="s1", topic_hash="h1") is True


def test_topic_hash_uses_first_message_prefix():
    # 完全相同的字符串 → hash 必相同
    h1 = compute_topic_hash("帮我配置一下 nginx 反向代理的具体设置可以吗？")
    h2 = compute_topic_hash("帮我配置一下 nginx 反向代理的具体设置可以吗？")
    assert h1 == h2