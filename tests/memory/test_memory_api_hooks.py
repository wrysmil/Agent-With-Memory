"""Tests for WU-3: memory_api CRUD hooks trigger MEMORY.md derivation.

Verifies that create_memory / update_memory / delete_memory call
_refresh_memory_md_after_mutation after successful SQLite writes, and that
derivation failures do not propagate to the mutation callers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.webui.memory_api import (
    create_memory,
    delete_memory,
    update_memory,
)
from nanobot.webui.memory_services import MemoryServices

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_ws(tmp_path):
    """Temporary workspace path."""
    return tmp_path / "ws"


@pytest.fixture
def services(tmp_ws):
    """MemoryServices for the temp workspace."""
    tmp_ws.mkdir(parents=True)
    db = MemoryDatabase(tmp_ws)
    db.init_schema()
    return MemoryServices(workspace_id="test-ws", database=db)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_memory(services: MemoryServices, content: str = "test content") -> str:
    """Insert a memory directly via repository and return its id."""
    from nanobot.memory.repository import add_memory

    memory_id = str(uuid.uuid4())
    memory = Memory(
        id=memory_id,
        content=content,
        type=MemoryType.FACT,
        priority=MemoryPriority.LONG_TERM,
        importance_score=0.5,
        tags=[],
        subject="",
        predicate="",
        metadata={},
        source="manual",
        workspace_id=services.workspace_id,
        created_at=_iso_now(),
        updated_at=_iso_now(),
    )
    with services.database.connect() as conn:
        add_memory(conn, memory)
    return memory_id


# ---------------------------------------------------------------------------
# Mock helper
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_lifecycle():
    """Mock MemoryLifecycle.for_workspace and refresh_memory_md_sync.

    Patches at the source module because memory_api uses lazy import.
    """
    with patch(
        "nanobot.memory.lifecycle.MemoryLifecycle"
    ) as mock_cls:
        instance = MagicMock()
        mock_cls.for_workspace.return_value = instance
        yield instance


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateMemoryTriggersRefresh:
    def test_create_memory_triggers_refresh(
        self, services: MemoryServices, mock_lifecycle
    ):
        """create_memory 成功后调用 refresh_memory_md_sync."""
        result = create_memory(services, content="test memory content")

        assert result["memory"]["content"] == "test memory content"
        mock_lifecycle.refresh_memory_md_sync.assert_called_once_with(
            services.workspace_id
        )

    def test_create_memory_normalizes_content(self, services, mock_lifecycle):
        """create_memory strips whitespace and calls refresh once."""
        result = create_memory(services, content="  trimmed  ")
        assert result["memory"]["content"] == "trimmed"
        mock_lifecycle.refresh_memory_md_sync.assert_called_once()


class TestUpdateMemoryTriggersRefresh:
    def test_update_memory_triggers_refresh(
        self, services: MemoryServices, mock_lifecycle
    ):
        """update_memory 成功后调用 refresh_memory_md_sync."""
        memory_id = _seed_memory(services)

        result = update_memory(services, memory_id, content="updated content")

        assert result["memory"]["content"] == "updated content"
        mock_lifecycle.refresh_memory_md_sync.assert_called_once_with(
            services.workspace_id
        )

    def test_update_memory_not_found_raises(
        self, services: MemoryServices, mock_lifecycle
    ):
        """update_memory 404 时不触发 refresh。"""
        from nanobot.webui.memory_api import WebUIMemoryError

        with pytest.raises(WebUIMemoryError) as exc_info:
            update_memory(services, "non-existent-id", content="x")
        assert exc_info.value.status == 404
        mock_lifecycle.refresh_memory_md_sync.assert_not_called()


class TestDeleteMemoryTriggersRefresh:
    def test_delete_memory_triggers_refresh(
        self, services: MemoryServices, mock_lifecycle
    ):
        """delete_memory 成功后调用 refresh_memory_md_sync."""
        memory_id = _seed_memory(services)

        result = delete_memory(services, memory_id)

        assert result["ok"] is True
        mock_lifecycle.refresh_memory_md_sync.assert_called_once_with(
            services.workspace_id
        )

    def test_delete_memory_not_found_raises(
        self, services: MemoryServices, mock_lifecycle
    ):
        """delete_memory 404 时不触发 refresh。"""
        from nanobot.webui.memory_api import WebUIMemoryError

        with pytest.raises(WebUIMemoryError) as exc_info:
            delete_memory(services, "non-existent-id")
        assert exc_info.value.status == 404
        mock_lifecycle.refresh_memory_md_sync.assert_not_called()


class TestRefreshFailureDoesNotPropagate:
    def test_refresh_failure_does_not_propagate_create(
        self, services: MemoryServices, mock_lifecycle
    ):
        """refresh_memory_md_sync 抛异常时只记日志，不破坏 mutation 返回值。"""
        mock_lifecycle.refresh_memory_md_sync.side_effect = RuntimeError("boom")

        # mutation 仍应正常返回
        result = create_memory(services, content="test after refresh failure")
        assert result["memory"]["content"] == "test after refresh failure"

    def test_refresh_failure_does_not_propagate_update(
        self, services: MemoryServices, mock_lifecycle
    ):
        """update_memory 后 refresh 抛异常时 mutation 仍返回正确结果。"""
        memory_id = _seed_memory(services)
        mock_lifecycle.refresh_memory_md_sync.side_effect = RuntimeError("boom")

        result = update_memory(services, memory_id, content="updated after failure")
        assert result["memory"]["content"] == "updated after failure"

    def test_refresh_failure_does_not_propagate_delete(
        self, services: MemoryServices, mock_lifecycle
    ):
        """delete_memory 后 refresh 抛异常时 mutation 仍返回 ok。"""
        memory_id = _seed_memory(services)
        mock_lifecycle.refresh_memory_md_sync.side_effect = RuntimeError("boom")

        result = delete_memory(services, memory_id)
        assert result["ok"] is True


class TestRefreshOnlyOnSuccess:
    def test_create_memory_creates_record(self, services, mock_lifecycle):
        """确认 create_memory 确实写入了数据库。"""
        result = create_memory(services, content="persisted content")
        memory_id = result["memory"]["id"]

        # 回读确认写入
        from nanobot.memory.repository import get_memory

        with services.database.connect() as conn:
            row = get_memory(conn, memory_id)
        assert row is not None
        assert row.content == "persisted content"

    def test_refresh_called_after_db_write(self, services, mock_lifecycle):
        """mock 验证调用时机：在 return 之前。"""
        call_order: list[str] = []

        original_refresh = mock_lifecycle.refresh_memory_md_sync

        def tracking_refresh(ws_id: str):
            call_order.append("refresh")
            return original_refresh(ws_id)

        mock_lifecycle.refresh_memory_md_sync = tracking_refresh

        result = create_memory(services, content="order test")
        call_order.append("return")

        # refresh 必须在 return 之前被调用
        assert call_order == ["refresh", "return"]
        assert result["memory"]["content"] == "order test"
