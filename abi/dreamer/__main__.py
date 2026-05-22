"""ABI Dreamer — nightly memory insight generator.

Runs as a cron job (or systemd timer). For each active agent:
1. Reads recent memories (respecting DLP clearance)
2. Groups memories by topic/pattern
3. Generates insights stored as new memories at internal/public DLP level
4. Logs summary to stdout

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

# DB connection
DB_HOST = os.environ.get("ABI_DB_HOST", "localhost")
DB_PORT = os.environ.get("ABI_DB_PORT", "5432")
DB_NAME = os.environ.get("ABI_DB_NAME", "abi_memory")
DB_USER = os.environ.get("ABI_DB_USER", "abi_agent")
DB_PASS = os.environ.get("ABI_DB_PASS", "abi_local_dev_2026")

import psycopg2


def get_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS,
    )


def get_active_agents(conn) -> List[Dict]:
    """Get all active agents from the registry."""
    with conn.cursor() as cur:
        cur.execute("SELECT username, display_name, role, clearance FROM abi_agents WHERE status = 'active'")
        cols = ["username", "display_name", "role", "clearance"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_recent_memories(conn, agent_name: str, clearance: str, days: int = 7) -> List[Dict]:
    """Fetch recent memories for an agent, filtered by DLP clearance."""
    where_clause, params = dlp_where(clearance, agent_name)

    query = f"""
        SELECT id, content, dlp_level, agent_name, user_id, source_type, created_at, metadata
        FROM abi_memories
        WHERE {where_clause}
          AND created_at > NOW() - INTERVAL '%s days'
          AND source_type != 'dreamer'
        ORDER BY created_at DESC
        LIMIT 200
    """
    params.append(days)

    with conn.cursor() as cur:
        cur.execute(query, params)
        cols = ["id", "content", "dlp_level", "agent_name", "user_id", "source_type", "created_at", "metadata"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def analyze_patterns(memories: List[Dict]) -> List[Dict]:
    """Analyze memories and identify patterns/topics.

    Simple approach: group by keywords, detect frequency patterns,
    identify recurring themes. No LLM needed for the MVP.
    """
    if not memories:
        return []

    # Group by source type
    by_source = {}
    for m in memories:
        src = m.get("source_type", "unknown")
        by_source.setdefault(src, []).append(m)

    insights = []

    # Insight: conversation volume
    conv_memories = by_source.get("conversation", by_source.get("agent_tool", []))
    if len(conv_memories) >= 3:
        insights.append({
            "type": "activity_summary",
            "content": f"Agent had {len(conv_memories)} memory entries in the last 7 days. "
                       f"Topics covered: {', '.join(set(m['content'][:50] for m in conv_memories[:10]))}",
            "dlp_level": "internal",
        })

    # Insight: user interactions
    user_ids = set(m.get("user_id") for m in memories if m.get("user_id"))
    if len(user_ids) >= 2:
        insights.append({
            "type": "multi_user_activity",
            "content": f"Interacted with {len(user_ids)} different users this week: {', '.join(user_ids)}",
            "dlp_level": "internal",
        })

    # Insight: tool usage patterns
    tool_memories = [m for m in memories if "tool" in m.get("source_type", "")]
    if len(tool_memories) >= 3:
        insights.append({
            "type": "tool_usage",
            "content": f"Used {len(tool_memories)} tool-related operations. "
                       f"Most active source: {max(by_source, key=lambda k: len(by_source[k]))}",
            "dlp_level": "internal",
        })

    # Insight: topic clustering (simple keyword extraction)
    all_content = " ".join(m["content"] for m in memories[:50]).lower()
    keywords = {}
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
                  "have", "has", "had", "do", "does", "did", "will", "would", "could",
                  "should", "may", "might", "can", "shall", "for", "to", "of", "in",
                  "on", "at", "by", "from", "with", "and", "or", "but", "not", "this",
                  "that", "it", "its", "as", "if", "so", "no", "up", "out", "about"}
    for word in all_content.split():
        word = word.strip(".,!?;:\"'()[]{}").strip()
        if len(word) > 3 and word not in stop_words:
            keywords[word] = keywords.get(word, 0) + 1

    top_topics = sorted(keywords.items(), key=lambda x: -x[1])[:5]
    if top_topics:
        topic_str = ", ".join(f"{word} ({count})" for word, count in top_topics)
        insights.append({
            "type": "topic_summary",
            "content": f"Top topics this week: {topic_str}",
            "dlp_level": "public",
        })

    return insights


def store_insight(conn, agent_name: str, insight: Dict) -> None:
    """Store a generated insight as a new memory."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO abi_memories (content, dlp_level, agent_name, source_type, metadata)
               VALUES (%s, %s, %s, 'dreamer', %s)""",
            (
                insight["content"],
                insight["dlp_level"],
                agent_name,
                json.dumps({"type": insight["type"], "generated_by": "dreamer"}),
            ),
        )
    conn.commit()


def run_dreamer(agent_filter: Optional[str] = None, dry_run: bool = False) -> Dict:
    """Run the Dreamer for all or a single agent.

    Args:
        agent_filter: Only process this agent username.
        dry_run: Analyze but don't write insights.

    Returns:
        Summary dict.
    """
    conn = get_connection()
    agents = get_active_agents(conn)

    if agent_filter:
        agents = [a for a in agents if a["username"] == agent_filter]

    if not agents:
        print("No active agents found.")
        return {"agents_processed": 0}

    summary = {"agents_processed": 0, "total_insights": 0, "details": {}}

    for agent in agents:
        name = agent["username"]
        clearance = agent["clearance"]

        memories = get_recent_memories(conn, name, clearance)
        if not memories:
            print(f"[{name}] No recent memories, skipping.")
            continue

        insights = analyze_patterns(memories)

        print(f"[{name}] {len(memories)} memories analyzed → {len(insights)} insights")

        if not dry_run:
            for insight in insights:
                store_insight(conn, name, insight)
                print(f"  → [{insight['dlp_level']}] {insight['content'][:80]}...")
        else:
            print(f"  (dry run, {len(insights)} insights would be generated)")
            for insight in insights:
                print(f"  → [{insight['dlp_level']}] {insight['content'][:80]}...")

        summary["agents_processed"] += 1
        summary["total_insights"] += len(insights)
        summary["details"][name] = {
            "memories_analyzed": len(memories),
            "insights_generated": len(insights),
        }

    conn.close()
    return summary


def main():
    parser = argparse.ArgumentParser(description="ABI Dreamer — nightly insight generator")
    parser.add_argument("--agent", help="Only process this agent username")
    parser.add_argument("--dry-run", action="store_true", help="Analyze without writing")
    args = parser.parse_args()

    print(f"=== ABI Dreamer — {datetime.now(timezone.utc).isoformat()} ===\n")

    result = run_dreamer(agent_filter=args.agent, dry_run=args.dry_run)

    print(f"\nDone: {result['agents_processed']} agents, {result['total_insights']} insights")


if __name__ == "__main__":
    main()
