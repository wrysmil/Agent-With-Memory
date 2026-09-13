"""API request/response schemas."""

from pydantic import BaseModel, ConfigDict


class SessionEndRequest(BaseModel):
    """Request body for POST /api/sessions/{session_key}/end."""

    reason: str = "user_close"
    model_config = ConfigDict(extra="ignore")


class SessionEndResponse(BaseModel):
    """Response body for POST /api/sessions/{session_key}/end."""

    status: str = "ok"
    session_key: str
