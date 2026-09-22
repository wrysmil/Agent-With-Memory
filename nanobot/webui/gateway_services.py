"""Composition helpers for the embedded WebUI gateway."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger as default_logger

from nanobot.config.loader import get_config_path
from nanobot.memory.lifecycle import MemoryLifecycle
from nanobot.webui import identity_api, memory_api
from nanobot.webui.gateway_endpoint import WebUIGatewayEndpoint
from nanobot.webui.gateway_tokens import GatewayTokenStore
from nanobot.webui.identity_routes import IdentitySettingsOperations
from nanobot.webui.ingress_policy import DEFAULT_WEBUI_INGRESS_POLICY, WebUIIngressPolicy
from nanobot.webui.media_gateway import WebUIMediaGateway
from nanobot.webui.memory_routes import MemorySettingsOperations
from nanobot.webui.memory_services import MemoryServices
from nanobot.webui.session_projection import WebUISessionProjection
from nanobot.webui.settings_services import WebUISettingsServices
from nanobot.webui.temporary_chats import WebUITemporaryChats
from nanobot.webui.transcript import WebUITranscriptRecorder
from nanobot.webui.workspaces import WebUIWorkspaceController
from nanobot.webui.ws_http import GatewayHTTPHandler

if TYPE_CHECKING:
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.websocket.runtime import WebSocketConfig
    from nanobot.cron.service import CronService
    from nanobot.session.manager import SessionManager
    from nanobot.triggers.local_store import LocalTriggerStore


@dataclass(frozen=True)
class GatewayServices:
    """Explicit dependencies shared by WebSocket transport and HTTP routes."""

    http: GatewayHTTPHandler
    endpoint: WebUIGatewayEndpoint
    settings: WebUISettingsServices
    tokens: GatewayTokenStore
    media: WebUIMediaGateway
    ingress: WebUIIngressPolicy
    transcripts: WebUITranscriptRecorder
    workspaces: WebUIWorkspaceController
    temporary_chats: WebUITemporaryChats
    session_projection: WebUISessionProjection
    session_manager: SessionManager | None
    cron_service: CronService | None
    local_trigger_store: LocalTriggerStore | None
    cron_pending_job_ids: Callable[[str], set[str]] | None
    local_trigger_pending_ids: Callable[[str], set[str]] | None


def build_memory_operations(
    *,
    workspace_id: str,
    workspace_path: Path,
) -> MemorySettingsOperations:
    """Wire the real ``memory_api`` actions to a workspace-scoped database."""
    services = MemoryServices.for_workspace(workspace_id, workspace_path)
    return MemorySettingsOperations(
        list_memories=partial(memory_api.list_memories_payload, services),
        search_memories=partial(memory_api.search_memories_payload, services),
        fetch_memory=partial(memory_api.fetch_memory_payload, services),
        list_episodes=partial(memory_api.list_episodes_payload, services),
        fetch_episode=partial(memory_api.fetch_episode_payload, services),
        fetch_scratchpad=partial(memory_api.scratchpad_payload, services),
        fetch_stats=partial(memory_api.stats_payload, services),
        create_memory=partial(memory_api.create_memory, services),
        update_memory=partial(memory_api.update_memory, services),
        delete_memory=partial(memory_api.delete_memory, services),
        update_episode=partial(memory_api.update_episode, services),
        delete_episode=partial(memory_api.delete_episode, services),
        save_scratchpad=partial(memory_api.save_scratchpad, services),
        # reindex/sync 不注入 indexer/vector_runtime：由 memory_api 内部 fallback
        # 到进程级单例（get_active_indexer / get_active_store），与写路径钩子同构，
        # 规避双 VectorStore 撞 ChromaDB 内部 sqlite 锁。
        reindex_vector=partial(memory_api.reindex_vector, services),
        sync_vector=partial(memory_api.sync_vector, services),
        refresh_memory_md=partial(memory_api.refresh_memory_md, services),  # 🆕 WU-4
        get_memory_md_content=partial(memory_api.get_memory_md_content, services),  # 🆕 WU-6
    )


def build_identity_operations(
    *,
    workspace_id: str,
    workspace_path: Path,
) -> IdentitySettingsOperations:
    """Wire the real ``identity_api`` actions to a workspace-scoped store.

    Every callable arrives pre-bound: the handler passes business arguments
    (name/content/mode) only. ``MEMORY.md`` writes and reloads go through the
    workspace's ``MemoryLifecycle`` singleton, matching the write-path hook and
    avoiding a second ``VectorStore``.
    """
    services = MemoryServices.for_workspace(workspace_id, workspace_path)
    lifecycle = MemoryLifecycle.for_workspace(workspace_id, services)
    workspace = Path(workspace_path)
    return IdentitySettingsOperations(
        list_files=partial(identity_api.identity_list_files, workspace),
        read_file=partial(identity_api.identity_read_file, workspace),
        write_file=partial(
            identity_api.identity_write_file, workspace, lifecycle=lifecycle
        ),
        reload=partial(
            identity_api.identity_reload,
            refresh_memory_md=partial(memory_api.refresh_memory_md, services),
        ),
        compile=partial(identity_api.identity_compile, workspace),
        list_presets=identity_api.identity_list_presets,
    )


def build_gateway_services(
    *,
    config: WebSocketConfig,
    bus: MessageBus,
    session_manager: SessionManager | None,
    static_dist_path: Path | None,
    workspace_path: Path,
    default_restrict_to_workspace: bool,
    config_path: Path | None = None,
    runtime_model_name: Callable[[], str | None] | None,
    refresh_runtime_config: Callable[[], None] | None = None,
    runtime_surface: str,
    runtime_capabilities_overrides: dict[str, Any] | None,
    disabled_skills: set[str] | None = None,
    cron_service: CronService | None = None,
    local_trigger_store: LocalTriggerStore | None = None,
    cron_pending_job_ids: Callable[[str], set[str]] | None = None,
    local_trigger_pending_ids: Callable[[str], set[str]] | None = None,
    channel_feature_action: Callable[..., Any] | None = None,
    channel_runtime_status: Callable[[], dict[str, Any]] | None = None,
    mcp_runtime_status: Callable[[], Mapping[str, str]] | None = None,
    mcp_reload: Callable[[], Awaitable[dict[str, Any]]] | None = None,
    skill_state_action: Callable[[set[str]], None] | None = None,
    recovery_action: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    logger: Any = default_logger,
) -> GatewayServices:
    settings = WebUISettingsServices.create(
        config_path or get_config_path(),
        rename_model_preset=(
            session_manager.rename_model_preset
            if session_manager is not None
            else None
        ),
        refresh_runtime_config=refresh_runtime_config,
    )
    tokens = GatewayTokenStore()
    ingress = DEFAULT_WEBUI_INGRESS_POLICY
    minimum_frame_bytes = ingress.minimum_full_policy_frame_bytes()
    if config.max_message_bytes < minimum_frame_bytes:
        logger.warning(
            "WebSocket maxMessageBytes={} is below the WebUI ingress policy capacity={}; "
            "policy-valid messages may still hit the transport frame guard",
            config.max_message_bytes,
            minimum_frame_bytes,
        )
    media = WebUIMediaGateway(
        workspace_path=workspace_path,
        logger=logger,
        attachment_limits=ingress.attachments,
    )
    transcripts = WebUITranscriptRecorder(log=logger)
    workspaces = WebUIWorkspaceController(
        session_manager=session_manager,
        default_workspace=workspace_path,
        default_restrict_to_workspace=default_restrict_to_workspace,
    )
    temporary_chats = WebUITemporaryChats(
        bus=bus,
        session_manager=session_manager,
        workspaces=workspaces,
        logger=logger,
    )
    session_projection = WebUISessionProjection(session_manager, log=logger)
    http = GatewayHTTPHandler(
        config=config,
        session_manager=session_manager,
        static_dist_path=static_dist_path,
        runtime_model_name=runtime_model_name,
        runtime_surface=runtime_surface,
        runtime_capabilities_overrides=runtime_capabilities_overrides,
        bus=bus,
        tokens=tokens,
        media=media,
        ingress=ingress,
        workspaces=workspaces,
        settings=settings,
        skills_workspace_path=workspace_path,
        disabled_skills=disabled_skills,
        memory_operations=build_memory_operations(
            workspace_id="default",
            workspace_path=workspace_path,
        ),
        identity_operations=build_identity_operations(
            workspace_id="default",
            workspace_path=workspace_path,
        ),
        cron_service=cron_service,
        local_trigger_store=local_trigger_store,
        cron_pending_job_ids=cron_pending_job_ids,
        local_trigger_pending_ids=local_trigger_pending_ids,
        channel_feature_action=channel_feature_action,
        channel_runtime_status=channel_runtime_status,
        mcp_runtime_status=mcp_runtime_status,
        mcp_reload=mcp_reload,
        skill_state_action=skill_state_action,
        recovery_action=recovery_action,
        log=logger,
    )
    endpoint = WebUIGatewayEndpoint(config=config, http=http, tokens=tokens)
    return GatewayServices(
        http=http,
        endpoint=endpoint,
        settings=settings,
        tokens=tokens,
        media=media,
        ingress=ingress,
        transcripts=transcripts,
        workspaces=workspaces,
        temporary_chats=temporary_chats,
        session_projection=session_projection,
        session_manager=session_manager,
        cron_service=cron_service,
        local_trigger_store=local_trigger_store,
        cron_pending_job_ids=cron_pending_job_ids,
        local_trigger_pending_ids=local_trigger_pending_ids,
    )
