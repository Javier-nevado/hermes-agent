"""Pydantic request/response models for the ABI Memory API."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Memory operations
# ---------------------------------------------------------------------------

class RememberRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    dlp_level: Optional[str] = Field(None, pattern="^(public|internal|confidential)$")
    agent_name: Optional[str] = None
    user_id: Optional[str] = None
    clearance: str = "internal"


class RememberResponse(BaseModel):
    status: str
    memory_id: str
    dlp_level: str
    entity_count: int
    edge_count: int
    superseded_count: int


class RecallRequest(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(5, ge=1, le=20)
    agent_name: Optional[str] = None
    clearance: str = "internal"


class MemoryItem(BaseModel):
    id: str
    content: str
    dlp_level: str
    created_at: Optional[str] = None
    score: Optional[float] = None


class RecallResponse(BaseModel):
    memories: List[MemoryItem]
    count: int


class ForgetRequest(BaseModel):
    memory_id: str
    agent_name: Optional[str] = None


class ForgetResponse(BaseModel):
    status: str
    memory_id: Optional[str] = None
    error: Optional[str] = None


class RememberBatchRequest(BaseModel):
    items: List[RememberRequest] = Field(..., min_length=1, max_length=50)


class RememberBatchResponse(BaseModel):
    results: List[RememberResponse]
    count: int


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

class EntityExtractRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)


class EntityInfo(BaseModel):
    name: str
    type: str


class EdgeInfo(BaseModel):
    source: str
    target: str
    relation: str


class EntityExtractResponse(BaseModel):
    entities: List[EntityInfo]
    edges: List[EdgeInfo]
    entity_count: int
    edge_count: int


# ---------------------------------------------------------------------------
# Health / stats
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    db: str
    embeddings: str
    uptime_seconds: float
    license_status: str = "unknown"
    license_tier: Optional[str] = None
    license_expires: Optional[str] = None


class StatsResponse(BaseModel):
    memory_count: int
    entity_count: int
    edge_count: int
