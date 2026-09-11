"""Tests for agent identity.md template (T-15)."""

from __future__ import annotations

from pathlib import Path

import pytest

IDENTITY = Path("nanobot/templates/agent/identity.md")


class TestIdentityTemplate:
    def test_memory_search_tool_documented(self) -> None:
        text = IDENTITY.read_text(encoding="utf-8")
        assert "memory_search" in text

    def test_memory_search_has_description(self) -> None:
        text = IDENTITY.read_text(encoding="utf-8")
        # Must explain proactive vs passive retrieval
        assert "proactive" in text.lower() or "检索" in text

    def test_not_too_long(self) -> None:
        text = IDENTITY.read_text(encoding="utf-8")
        assert len(text) < 4000
