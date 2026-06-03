"""Custom Tables API — CRUD + query for agent-driven business tables.

All tables live in the `customer` PostgreSQL schema, isolated from core tables.
Write endpoints gated by `tables_enabled` in license JWT. Read endpoints always open.

Note: Custom table data is NOT encrypted (unlike memories). WHERE/LIKE/ORDER BY
on text columns must work directly — encryption would break these queries.
The API is the only access path, and PG port is not exposed.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_pool
from ..license import require_tables_license
from ..schemas import (
    ColumnDef,
    ColumnInfo,
    QueryRequest,
    QueryResponse,
    TableCreateRequest,
    TableCreateResponse,
    TableDeleteRequest,
    TableDeleteResponse,
    TableInfo,
    TableInsertRequest,
    TableInsertResponse,
    TableListResponse,
    TableSchemaResponse,
    TableUpdateRequest,
    TableUpdateResponse,
    WhereCondition,
)

logger = logging.getLogger(__name__)
router = APIRouter()

SCHEMA = "customer"
ALLOWED_TYPES = {"TEXT", "INTEGER", "BIGINT", "NUMERIC", "BOOLEAN", "DATE", "TIMESTAMPTZ", "JSONB", "UUID"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# SQL keywords that must NOT appear in /query
_BLOCKED_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|EXECUTE|EXEC|CALL|DO|VACUUM|REINDEX|CLUSTER)\b",
    re.IGNORECASE,
)

# Reject references to non-customer schemas
_SCHEMA_LEAK = re.compile(r'\b(public|abi)\s*\.', re.IGNORECASE)

# Reject semicolons (multi-statement injection) and comments
_INJECTION = re.compile(r';|--|/\*')


def _validate_table_name(name: str) -> str:
    if not NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid table name: {name}")
    return name


def _get_column_info(conn, table_name: str) -> List[ColumnInfo]:
    """Get column metadata for a table."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT c.column_name, c.data_type, c.is_nullable,
                      CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END AS is_pk
               FROM information_schema.columns c
               LEFT JOIN (
                   SELECT ku.column_name
                   FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage ku
                       ON tc.constraint_name = ku.constraint_name
                       AND tc.table_schema = ku.table_schema
                   WHERE tc.constraint_type = 'PRIMARY KEY'
                       AND tc.table_schema = %s
                       AND tc.table_name = %s
               ) pk ON c.column_name = pk.column_name
               WHERE c.table_schema = %s AND c.table_name = %s
               ORDER BY c.ordinal_position""",
            [SCHEMA, table_name, SCHEMA, table_name],
        )
        columns = []
        for row in cur.fetchall():
            pg_type = row[1]
            # Map PostgreSQL data_type back to our type names
            type_map = {
                "text": "TEXT", "integer": "INTEGER", "bigint": "BIGINT",
                "numeric": "NUMERIC", "boolean": "BOOLEAN", "date": "DATE",
                "timestamp with time zone": "TIMESTAMPTZ", "jsonb": "JSONB",
                "uuid": "UUID",
            }
            columns.append(ColumnInfo(
                name=row[0],
                type=type_map.get(pg_type, pg_type.upper()),
                nullable=row[2] == "YES",
                is_primary_key=row[3],
            ))
        return columns


def _table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            [SCHEMA, table_name],
        )
        return cur.fetchone() is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = %s AND table_name = %s AND column_name = %s",
            [SCHEMA, table_name, column_name],
        )
        return cur.fetchone() is not None


def _build_where_clause(conditions: List[WhereCondition], params: list) -> str:
    """Build WHERE clause from conditions. Appends values to params list."""
    allowed_ops = {"=", "!=", "<", ">", "<=", ">=", "LIKE", "ILIKE", "IN", "IS", "IS NOT"}
    parts = []
    for cond in conditions:
        if not NAME_RE.match(cond.column):
            raise HTTPException(status_code=400, detail=f"Invalid column name in WHERE: {cond.column}")
        if cond.op not in allowed_ops:
            raise HTTPException(status_code=400, detail=f"Unsupported operator: {cond.op}")

        if cond.op in ("IS", "IS NOT"):
            # IS NULL / IS NOT NULL — no parameter
            val = str(cond.value).upper() if cond.value else "NULL"
            if val not in ("NULL", "NOT NULL", "TRUE", "FALSE"):
                raise HTTPException(status_code=400, detail="IS/IS NOT only supports NULL, TRUE, FALSE")
            parts.append(f"{cond.column} {cond.op} {val}")
        elif cond.op == "IN":
            if not isinstance(cond.value, list):
                raise HTTPException(status_code=400, detail="IN operator requires a list value")
            placeholders = ", ".join(["%s"] * len(cond.value))
            parts.append(f"{cond.column} IN ({placeholders})")
            params.extend(cond.value)
        else:
            parts.append(f"{cond.column} {cond.op} %s")
            params.append(cond.value)

    return " AND ".join(parts)


# ---------------------------------------------------------------------------
# POST /tables/create
# ---------------------------------------------------------------------------

@router.post("/tables/create", response_model=TableCreateResponse, dependencies=[Depends(require_tables_license)])
def tables_create(req: TableCreateRequest):
    """Create a new custom table in the customer schema."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        if _table_exists(conn, req.name):
            raise HTTPException(status_code=409, detail=f"Table '{req.name}' already exists")

        # Validate column types
        for col in req.columns:
            if col.type not in ALLOWED_TYPES:
                raise HTTPException(status_code=400, detail=f"Unsupported column type: {col.type}")

        # Build DDL
        col_defs = ["id UUID PRIMARY KEY DEFAULT gen_random_uuid()"]
        pk_cols = [c.name for c in req.columns if c.primary_key]

        for col in req.columns:
            nullable = "" if col.nullable and col.name not in pk_cols else " NOT NULL"
            col_defs.append(f"{col.name} {col.type}{nullable}")

        col_defs.append("created_at TIMESTAMPTZ DEFAULT NOW()")
        col_defs.append("updated_at TIMESTAMPTZ DEFAULT NOW()")

        ddl = f"CREATE TABLE {SCHEMA}.{req.name} ({', '.join(col_defs)})"

        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

        return TableCreateResponse(
            status="created",
            table_name=req.name,
            columns=req.columns,
        )
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("Table create failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# POST /tables/insert
# ---------------------------------------------------------------------------

@router.post("/tables/insert", response_model=TableInsertResponse, dependencies=[Depends(require_tables_license)])
def tables_insert(req: TableInsertRequest):
    """Insert rows into a custom table."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        if not _table_exists(conn, req.table):
            raise HTTPException(status_code=404, detail=f"Table '{req.table}' not found")

        auto_columns = {"id", "created_at", "updated_at"}

        inserted = 0
        with conn.cursor() as cur:
            for row in req.rows:
                # Validate column names
                for col_name in row:
                    if col_name in auto_columns:
                        continue
                    if not NAME_RE.match(col_name):
                        raise HTTPException(status_code=400, detail=f"Invalid column name: {col_name}")

                values = dict(row)
                cols = list(values.keys())
                placeholders = ["%s"] * len(cols)
                sql = f"INSERT INTO {SCHEMA}.{req.table} ({', '.join(cols)}) VALUES ({', '.join(placeholders)})"
                cur.execute(sql, [values[c] for c in cols])
                inserted += 1

        conn.commit()
        return TableInsertResponse(status="inserted", table_name=req.table, rows_inserted=inserted)
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("Table insert failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# POST /tables/update
# ---------------------------------------------------------------------------

@router.post("/tables/update", response_model=TableUpdateResponse, dependencies=[Depends(require_tables_license)])
def tables_update(req: TableUpdateRequest):
    """Update rows in a custom table with WHERE conditions."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        if not _table_exists(conn, req.table):
            raise HTTPException(status_code=404, detail=f"Table '{req.table}' not found")

        # Validate column names in values
        params: list = []
        set_parts = []
        for col_name, value in req.values.items():
            if not NAME_RE.match(col_name):
                raise HTTPException(status_code=400, detail=f"Invalid column name: {col_name}")
            set_parts.append(f"{col_name} = %s")
            params.append(value)

        # Add updated_at = NOW() only if column exists
        if _column_exists(conn, req.table, "updated_at"):
            set_parts.append("updated_at = NOW()")

        where_sql = _build_where_clause(req.where, params)

        sql = f"UPDATE {SCHEMA}.{req.table} SET {', '.join(set_parts)} WHERE {where_sql}"
        with conn.cursor() as cur:
            cur.execute(sql, params)
            updated = cur.rowcount

        conn.commit()
        return TableUpdateResponse(status="updated", table_name=req.table, rows_updated=updated)
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("Table update failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# POST /tables/delete
# ---------------------------------------------------------------------------

@router.post("/tables/delete", response_model=TableDeleteResponse, dependencies=[Depends(require_tables_license)])
def tables_delete(req: TableDeleteRequest):
    """Delete rows from a custom table with WHERE conditions."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        if not _table_exists(conn, req.table):
            raise HTTPException(status_code=404, detail=f"Table '{req.table}' not found")

        params: list = []
        where_sql = _build_where_clause(req.where, params)

        sql = f"DELETE FROM {SCHEMA}.{req.table} WHERE {where_sql}"
        with conn.cursor() as cur:
            cur.execute(sql, params)
            deleted = cur.rowcount

        conn.commit()
        return TableDeleteResponse(status="deleted", table_name=req.table, rows_deleted=deleted)
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error("Table delete failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# GET /tables/list
# ---------------------------------------------------------------------------

@router.get("/tables/list", response_model=TableListResponse)
def tables_list():
    """List all custom tables in the customer schema."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT t.table_name, COALESCE(s.n_live_tup, 0) AS row_count
                   FROM information_schema.tables t
                   LEFT JOIN pg_stat_user_tables s
                       ON s.relname = t.table_name AND s.schemaname = t.table_schema
                   WHERE t.table_schema = %s AND t.table_type = 'BASE TABLE'
                   ORDER BY t.table_name""",
                [SCHEMA],
            )
            tables = [TableInfo(name=row[0], row_count=row[1]) for row in cur.fetchall()]

        return TableListResponse(tables=tables, count=len(tables))
    except Exception as e:
        logger.error("Table list failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# GET /tables/schema/{name}
# ---------------------------------------------------------------------------

@router.get("/tables/schema/{name}", response_model=TableSchemaResponse)
def tables_schema(name: str):
    """Get column definitions for a custom table."""
    pool = get_pool()
    conn = pool.getconn()
    try:
        _validate_table_name(name)
        if not _table_exists(conn, name):
            raise HTTPException(status_code=404, detail=f"Table '{name}' not found")

        columns = _get_column_info(conn, name)
        return TableSchemaResponse(table_name=name, columns=columns)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Table schema failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# POST /query
# ---------------------------------------------------------------------------

@router.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    """Run a SELECT query against customer schema tables.

    Only SELECT statements allowed. JOINs and subqueries supported.
    All table references must be in the customer schema.
    """
    pool = get_pool()
    conn = pool.getconn()
    try:
        sql = req.sql.strip()

        # Security checks
        if _INJECTION.search(sql):
            raise HTTPException(status_code=400, detail="Invalid SQL: semicolons and comments not allowed")

        if _BLOCKED_KEYWORDS.search(sql):
            raise HTTPException(status_code=400, detail="Only SELECT queries are allowed")

        if _SCHEMA_LEAK.search(sql):
            raise HTTPException(status_code=400, detail="Only customer schema tables are accessible")

        # Qualify unqualified table references with customer schema
        def qualify_table(match):
            prefix = match.group(1)
            table = match.group(2)
            if "." in table:
                return match.group(0)
            return f"{prefix}{SCHEMA}.{table}"

        qualified_sql = re.sub(
            r'(?i)(\bFROM\s+|\bJOIN\s+)("?[a-z][a-z0-9_]{0,62}"?)',
            qualify_table,
            sql,
        )

        params = req.params or []
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(qualified_sql, params)
            rows = cur.fetchall()

        return QueryResponse(rows=[dict(r) for r in rows], count=len(rows))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Query failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        pool.putconn(conn)
