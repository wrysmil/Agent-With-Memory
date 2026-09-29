"""WebUI settings 域 handler：Agent 档案管理。

照 ``identity_routes.py`` 的形状：与传输无关的 handler，``handle(action, request)``
对着注入进来的 operations 分发，返回 ``SettingsRouteResult``。路径 → action 的
映射、WebSocket / HTTP 的门禁都在 ``settings_routes.py``。

读动作（list / catalog）走普通 HTTP GET；写动作（save / delete / reset /
visibility）改变磁盘状态，走已认证的 WebSocket ``requestMutation`` 白名单。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from nanobot.webui.settings_contracts import (
    SettingsRequest,
    SettingsRouteResult,
    WebUISettingsError,
)


def _not_configured(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """catalog 未注入时的默认实现：响亮的 503，而不是 dispatch 深处的一次 AttributeError。"""
    raise WebUISettingsError("agent catalog is not configured", status=503)


@dataclass(frozen=True)
class AgentSettingsOperations:
    """注入 handler 的传输无关入口。

    每个字段都已绑好工作区依赖（gateway 用 ``partial`` 绑），所以
    ``dispatch`` 只传业务参数。保持成普通协议，测试才能塞内存替身而不碰磁盘。
    """

    list_profiles: Callable[..., dict[str, Any]]
    save_profile: Callable[..., dict[str, Any]]
    delete_profile: Callable[..., dict[str, Any]]
    reset_profile: Callable[..., dict[str, Any]]
    set_visibility: Callable[..., dict[str, Any]]
    list_catalog: Callable[..., dict[str, Any]] = _not_configured


# 本域认识的动作全集；settings router 用它把 action 路由进来。
AGENTS_ACTION_NAMES = frozenset({
    "agents-list",
    "agents-catalog",
    "agents-save",
    "agents-delete",
    "agents-reset",
    "agents-visibility",
})


class AgentSettingsHandler:
    """把单个 agents 域动作分发到注入进来的 operations 上。"""

    def __init__(self, operations: AgentSettingsOperations) -> None:
        self._ops = operations

    def handle(self, action: str, request: SettingsRequest) -> SettingsRouteResult:
        if action not in AGENTS_ACTION_NAMES:
            return SettingsRouteResult.failure(404, f"unknown agent action: {action}")
        try:
            payload = dispatch(self._ops, action, request)
        except WebUISettingsError as exc:
            return SettingsRouteResult.failure(exc.status, exc.message)
        return SettingsRouteResult.success(payload)


# ---- per-action dispatch ---------------------------------------------------


def dispatch(
    operations: AgentSettingsOperations,
    action: str,
    request: SettingsRequest,
) -> dict[str, Any]:
    payload = request.payload or {}

    if action == "agents-list":
        return operations.list_profiles()

    if action == "agents-catalog":
        return operations.list_catalog()

    if action == "agents-save":
        raw = payload.get("agent")
        if not isinstance(raw, dict):
            raise WebUISettingsError("agent must be an object", status=400)
        return operations.save_profile(raw)

    if action == "agents-delete":
        return operations.delete_profile(_require_id(payload))

    if action == "agents-reset":
        return operations.reset_profile(_require_id(payload))

    if action == "agents-visibility":
        agent_id = _require_id(payload)
        hidden = payload.get("hidden")
        if not isinstance(hidden, bool):
            raise WebUISettingsError("hidden must be a boolean", status=400)
        return operations.set_visibility(agent_id, hidden)

    # 已知动作不会走到这里；留着作为将来新增动作时的兜底。
    raise WebUISettingsError(f"unsupported agent action: {action}")


def _require_id(payload: dict[str, Any]) -> str:
    agent_id = payload.get("id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise WebUISettingsError("id is required", status=400)
    return agent_id.strip()
