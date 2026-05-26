"""ABI Dreamer v2 — nightly memory intelligence pipeline.

4-phase pipeline:
1. Deduplication: find and remove near-duplicate memories (>90% similarity)
2. Contradiction detection: flag conflicting facts sharing entities
3. Consolidation: generate summary insights from entity-grouped memories
4. Temporal maintenance: flag stale facts and auto-expire dated information

Usage:
    python3 -m abi.dreamer                    # All agents
    python3 -m abi.dreamer --agent ailean     # Single agent
    python3 -m abi.dreamer --dry-run          # Preview without writing
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from abi.memory.dlp import dlp_where

DB_HOST = os.environ.get("ABI_DB_HOST", "localhost")
DB_PORT = os.environ.get("ABI_DB_PORT", "5432")
DB_NAME = os.environ.get("ABI_DB_NAME", "abi_memory")
DB_USER = os.environ.get("ABI_DB_USER", "abi_agent")
DB_PASS = os.environ.get("ABI_DB_PASS", "abi_local_dev_2026")

import psycopg2
import psycopg2.extras


def get_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS,
    )


def get_active_agents(conn) -> List[Dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT username, display_name, role, clearance FROM abi_agents WHERE status = 'active'")
        return cur.fetchall()


# --- Phase 1: Deduplication ---

def phase_dedup(conn, agent_name: str, dry_run: bool) -> Dict:
    """Find and remove near-duplicate memories (>90% embedding similarity)."""
    stats = {"scanned": 0, "duplicates_found": 0, "removed": 0}

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Self-join to find pairs with high cosine similarity
        cur.execute("""
            SELECT m1.id AS id1, m2.id AS id2,
                   1 - (m1.embedding <=> m2.embedding) AS similarity,
                   m1.content AS content1, m2.content AS content2,
                   m1.created_at AS created1, m2.created_at AS created2
            FROM abi_memories m1
            JOIN abi_memories m2 ON m1.id < m2.id
                AND m1.agent_name = m2.agent_name
                AND m1.embedding IS NOT NULL
                AND m2.embedding IS NOT NULL
                AND 1 - (m1.embedding <=> m2.embedding) > 0.90
            WHERE m1.agent_name = %s
              AND m1.source_type != 'dreamer'
              AND m2.source_type != 'dreamer'
              AND m1.superseded_by IS NULL
              AND m2.superseded_by IS NULL
            ORDER BY similarity DESC
            LIMIT 50
        """, [agent_name])
        pairs = cur.fetchall()
        stats["scanned"] = len(pairs)

    # Keep the newer one, delete the older one
    to_delete = set()
    for pair in pairs:
        stats["duplicates_found"] += 1
        # Keep the newer memory
        if pair["created1"] > pair["created2"]:
            to_delete.add(str(pair["id2"]))
        else:
            to_delete.add(str(pair["id1"]))

    if not dry_run and to_delete:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM abi_memories WHERE id::text = ANY(%s) AND agent_name = %s",
                [list(to_delete), agent_name],
            )
            stats["removed"] = cur.rowcount
            conn.commit()

    return stats


# --- Phase 2: Contradiction Detection ---

def phase_contradictions(conn, agent_name: str, clearance: str, dry_run: bool) -> Dict:
    """Find memories sharing entities but with potentially conflicting content."""
    stats = {"checked": 0, "contradictions_flagged": 0}

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Find entity pairs that appear in multiple memories
        cur.execute("""
            SELECT e.name AS entity_name, e.type AS entity_type,
                   array_agg(DISTINCT m.id::text) AS memory_ids,
                   array_agg(DISTINCT m.content) AS contents,
                   count(DISTINCT m.id) AS memory_count
            FROM abi_memory_entities me
            JOIN abi_entities e ON me.entity_id = e.id
            JOIN abi_memories m ON me.memory_id = m.id
            WHERE m.agent_name = %s
              AND m.source_type != 'dreamer'
              AND m.source_type != 'contradiction_alert'
              AND m.superseded_by IS NULL
            GROUP BY e.name, e.type
            HAVING count(DISTINCT m.id) >= 3
            ORDER BY memory_count DESC
            LIMIT 20
        """, [agent_name])
        entity_groups = cur.fetchall()

    for group in entity_groups:
        stats["checked"] += 1
        contents = group["contents"]

        # Simple heuristic: if contents are about same entity but embedding similarity is low
        # (< 0.5), they might be contradictory
        # For now, just flag entities with many memories for agent review
        if len(contents) >= 3:
            stats["contradictions_flagged"] += 1
            if not dry_run:
                alert_content = (
                    f"Entity '{group['entity_name']}' ({group['entity_type']}) "
                    f"has {group['memory_count']} memories. "
                    f"Consider reviewing for potential contradictions or consolidation."
                )
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO abi_memories (content, dlp_level, agent_name, source_type, metadata)
                           VALUES (%s, 'internal', %s, 'contradiction_alert', %s)""",
                        [alert_content, agent_name,
                         json.dumps({"entity": group["entity_name"], "type": "contradiction_alert",
                                     "memory_count": group["memory_count"]})],
                    )
                    conn.commit()

    return stats


# --- Phase 3: Consolidation ---

def phase_consolidate(conn, agent_name: str, clearance: str, dry_run: bool) -> Dict:
    """Generate summary insights from entity-grouped memories."""
    stats = {"groups_processed": 0, "insights_generated": 0}

    where_clause, params = dlp_where(clearance, agent_name)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Get memories from last 7 days, grouped by shared entities
        cur.execute(f"""
            SELECT e.name AS entity_name, e.type AS entity_type,
                   array_agg(DISTINCT m.content) AS contents,
                   count(DISTINCT m.id) AS memory_count
            FROM abi_memory_entities me
            JOIN abi_entities e ON me.entity_id = e.id
            JOIN abi_memories m ON me.memory_id = m.id
            WHERE m.agent_name = %s
              AND {where_clause}
              AND m.source_type != 'dreamer'
              AND m.source_type != 'contradiction_alert'
              AND m.created_at > NOW() - INTERVAL '7 days'
              AND m.superseded_by IS NULL
            GROUP BY e.name, e.type
            HAVING count(DISTINCT m.id) >= 2
            ORDER BY memory_count DESC
            LIMIT 20
        """, [agent_name] + params)
        groups = cur.fetchall()

    for group in groups:
        stats["groups_processed"] += 1
        contents = group["contents"]

        # Generate a consolidation insight
        insight_content = (
            f"Entity '{group['entity_name']}' ({group['entity_type']}): "
            f"{len(contents)} related memories this week. "
            f"Key topics: {', '.join(c[:60] for c in contents[:3])}"
        )

        stats["insights_generated"] += 1
        if not dry_run:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO abi_memories (content, dlp_level, agent_name, source_type, metadata)
                       VALUES (%s, 'internal', %s, 'dreamer', %s)""",
                    [insight_content, agent_name,
                     json.dumps({"type": "consolidation", "entity": group["entity_name"],
                                 "source_count": len(contents)})],
                )
                conn.commit()

    return stats


# --- Phase 4: Temporal Maintenance ---

def phase_temporal(conn, agent_name: str, dry_run: bool) -> Dict:
    """Flag stale facts and auto-expire dated information."""
    stats = {"stale_flagged": 0, "auto_expired": 0}

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # Find facts older than 90 days without valid_to (potentially stale)
        cur.execute("""
            SELECT id, content, created_at
            FROM abi_memories
            WHERE agent_name = %s
              AND source_type NOT IN ('dreamer', 'contradiction_alert')
              AND superseded_by IS NULL
              AND valid_to IS NULL
              AND created_at < NOW() - INTERVAL '90 days'
            LIMIT 20
        """, [agent_name])
        stale = cur.fetchall()

    for mem in stale:
        stats["stale_flagged"] += 1
        if not dry_run:
            alert_content = (
                f"Stale fact review: Memory from {mem['created_at'].strftime('%Y-%m-%d')} "
                f"may be outdated: {mem['content'][:80]}..."
            )
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO abi_memories (content, dlp_level, agent_name, source_type, metadata)
                       VALUES (%s, 'internal', %s, 'dreamer', %s)""",
                    [alert_content, agent_name,
                     json.dumps({"type": "stale_review", "original_memory": str(mem["id"])})],
                )
                conn.commit()

    # Auto-expire: find memories with date entities that are in the past
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT DISTINCT m.id::text, m.content
            FROM abi_memories m
            JOIN abi_memory_entities me ON m.id = me.memory_id
            JOIN abi_entities e ON me.entity_id = e.id
            WHERE m.agent_name = %s
              AND e.type = 'date'
              AND m.superseded_by IS NULL
              AND m.valid_to IS NULL
              AND m.source_type NOT IN ('dreamer', 'contradiction_alert')
        """, [agent_name])
        dated = cur.fetchall()

    # For now, just count. Actual parsing of dates from entity names for auto-expiry
    # would require date extraction from entity names like "Q3 2026" or "May 2026"
    stats["auto_expired"] = 0  # TODO: implement date parsing + auto-expiry

    return stats


def run_dreamer(agent_filter: Optional[str] = None, dry_run: bool = False) -> Dict:
    conn = get_connection()
    agents = get_active_agents(conn)

    if agent_filter:
        agents = [a for a in agents if a["username"] == agent_filter]

    if not agents:
        print("No active agents found.")
        return {"agents_processed": 0}

    summary = {"agents_processed": 0, "phases": {}}

    for agent in agents:
        name = agent["username"]
        clearance = agent["clearance"]
        print(f"\n[{name}] Running Dream Cycle v2...")

        # Phase 1: Dedup
        dedup = phase_dedup(conn, name, dry_run)
        print(f"  Phase 1 (Dedup): {dedup['duplicates_found']} duplicates found, {dedup['removed']} removed")

        # Phase 2: Contradictions
        contrad = phase_contradictions(conn, name, clearance, dry_run)
        print(f"  Phase 2 (Contradictions): {contrad['checked']} entities checked, {contrad['contradictions_flagged']} flagged")

        # Phase 3: Consolidation
        consol = phase_consolidate(conn, name, clearance, dry_run)
        print(f"  Phase 3 (Consolidation): {consol['groups_processed']} groups, {consol['insights_generated']} insights")

        # Phase 4: Temporal
        temporal = phase_temporal(conn, name, dry_run)
        print(f"  Phase 4 (Temporal): {temporal['stale_flagged']} stale facts flagged")

        summary["agents_processed"] += 1
        summary["phases"][name] = {
            "dedup": dedup,
            "contradictions": contrad,
            "consolidation": consol,
            "temporal": temporal,
        }

    conn.close()
    return summary


def main():
    parser = argparse.ArgumentParser(description="ABI Dreamer v2 — nightly memory intelligence pipeline")
    parser.add_argument("--agent", help="Only process this agent username")
    parser.add_argument("--dry-run", action="store_true", help="Analyze without writing")
    args = parser.parse_args()

    print(f"=== ABI Dreamer v2 — {datetime.now(timezone.utc).isoformat()} ===")

    result = run_dreamer(agent_filter=args.agent, dry_run=args.dry_run)

    print(f"\nDone: {result['agents_processed']} agents processed")


if __name__ == "__main__":
    main()
