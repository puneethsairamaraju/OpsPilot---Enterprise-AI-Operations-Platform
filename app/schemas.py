from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    name: str
    role: str


class DocumentCreate(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    content: str = Field(min_length=20)
    source: str = Field(default="manual", max_length=120)
    classification: str = Field(default="internal", pattern="^(public|internal|confidential)$")


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    source: str
    classification: str
    created_at: datetime


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)


class QueryResponse(BaseModel):
    id: str
    answer: str
    confidence: float
    status: str
    citations: list[dict]
    latency_ms: float


class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    note: str | None = Field(default=None, max_length=1000)


class FeedbackCreate(BaseModel):
    query_run_id: str
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)


class ConnectionCreate(BaseModel):
    provider: str = Field(
        pattern="^(slack|google_drive|sharepoint|notion|jira|zendesk|confluence|email)$"
    )
    name: str = Field(min_length=2, max_length=120)
    config: dict = Field(default_factory=dict)


class SourceEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=255)
    operation: str = Field(pattern="^(upsert|delete)$")
    external_id: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    content: str | None = None
    source_url: str | None = None
    allowed_roles: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    checkpoint: str | None = None
