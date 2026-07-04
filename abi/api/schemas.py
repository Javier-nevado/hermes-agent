"""Pydantic request/response models for the ABI Memory API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
    # PR 3 — provenance + classification (auto-extraction / backfill). Optional;
    # omitted = today's behaviour (source_type 'api', no type/importance set).
    source_type: Optional[str] = Field(
        None, pattern="^(api|agent_tool|auto_extraction|session_mined|migration|identity)$"
    )
    memory_type: Optional[str] = Field(
        None, pattern="^(preference|decision|fact|event|identity|other)$"
    )
    importance: Optional[float] = Field(None, ge=0.0, le=1.0)


class TurnIngestRequest(BaseModel):
    """A completed conversation turn, posted by the abi_memory_api client.

    The server enqueues it for background extraction and returns immediately —
    extraction never blocks the agent's turn (sync_turn is on the request path).
    """
    agent_name: str
    user_content: str = ""
    assistant_content: str = ""
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    clearance: str = "internal"


class TurnIngestResponse(BaseModel):
    status: str  # "queued" | "disabled"
    queued: int


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
    encryption: str = "disabled"  # disabled | active
    tables: str = "disabled"  # disabled | enabled


class StatsResponse(BaseModel):
    memory_count: int
    entity_count: int
    edge_count: int


# ---------------------------------------------------------------------------
# Custom Tables
# ---------------------------------------------------------------------------

class ColumnDef(BaseModel):
    name: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,62}$")
    type: str = Field(
        ...,
        pattern=r"^(TEXT|INTEGER|BIGINT|NUMERIC|BOOLEAN|DATE|TIMESTAMPTZ|JSONB|UUID)$",
    )
    nullable: bool = True
    primary_key: bool = False


class TableCreateRequest(BaseModel):
    name: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,62}$")
    columns: List[ColumnDef] = Field(..., min_length=1, max_length=50)


class TableCreateResponse(BaseModel):
    status: str
    table_name: str
    columns: List[ColumnDef]


class TableInsertRequest(BaseModel):
    table: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,62}$")
    rows: List[Dict[str, Any]] = Field(..., min_length=1, max_length=100)


class TableInsertResponse(BaseModel):
    status: str
    table_name: str
    rows_inserted: int


class WhereCondition(BaseModel):
    column: str
    op: str = Field(..., pattern=r"^(=|!=|<|>|<=|>=|LIKE|ILIKE|IN|IS|IS NOT)$")
    value: Any = None


class TableUpdateRequest(BaseModel):
    table: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,62}$")
    values: Dict[str, Any]
    where: List[WhereCondition] = Field(..., min_length=1)


class TableUpdateResponse(BaseModel):
    status: str
    table_name: str
    rows_updated: int


class TableDeleteRequest(BaseModel):
    table: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,62}$")
    where: List[WhereCondition] = Field(..., min_length=1)


class TableDeleteResponse(BaseModel):
    status: str
    table_name: str
    rows_deleted: int


class TableInfo(BaseModel):
    name: str
    row_count: int = 0


class TableListResponse(BaseModel):
    tables: List[TableInfo]
    count: int


class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool
    is_primary_key: bool


class TableSchemaResponse(BaseModel):
    table_name: str
    columns: List[ColumnInfo]


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=2000)
    params: Optional[List[Any]] = None


class QueryResponse(BaseModel):
    rows: List[Dict[str, Any]]
    count: int
