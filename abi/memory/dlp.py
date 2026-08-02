"""DLP enforcement — builds SQL WHERE clauses for clearance levels.

DLP levels:
- public: visible to ALL agents
- internal: visible to admin + internal agents
- confidential: visible only to the owning agent

Clearance levels:
- admin: sees own confidential + all internal/public
- confidential: sees own confidential + all internal/public (DLP-equivalent to
  admin — a confidential-cleared agent is trusted with confidential-tier data;
  no cross-agent confidential visibility is granted)
- internal: sees all internal/public (no confidential)
- external: sees only public
"""

from typing import List, Tuple


def dlp_where(
    clearance: str,
    agent_name: str,
    user_id: str = None,
    shared_scope: bool = False,
) -> Tuple[str, List[str]]:
    """Build a DLP-filtered WHERE clause for SQL queries.

    Per-user read isolation: when ``user_id`` is supplied and ``shared_scope`` is
    False (the default for 1:1 DMs), the clause is additionally narrowed to that
    user's own memories OR ownerless shared/system memories (``user_id IS NULL``,
    plus ``source_type = 'identity'`` team anchors) — so private context never
    leaks across users sharing the same agent. When ``shared_scope`` is True
    (group/channel), no user filter is applied and the team shares one pool.
    When ``user_id`` is None (system/cron callers), behaviour is unchanged.

    Returns:
        Tuple of (where_clause, params) where params are for %s placeholders.
    """
    if clearance in ("admin", "confidential"):
        # A confidential-cleared agent sees its own confidential memories plus
        # the shared internal/public pool — same DLP view as admin. Previously
        # 'confidential' fell through to the external branch (public-only),
        # silently blinding it to internal+own-confidential memories.
        base, params = (
            "((dlp_level = 'confidential' AND agent_name = %s) OR dlp_level IN ('internal', 'public'))",
            [agent_name],
        )
    elif clearance == "internal":
        base, params = ("dlp_level IN ('internal', 'public')", [])
    else:  # external
        base, params = ("dlp_level = 'public'", [])

    # Per-user read isolation for private (non-shared) scopes. Ownerless
    # memories (NULL user_id) and identity/team anchors stay visible to all.
    if user_id and not shared_scope:
        base = (
            "(" + base + ")"
            + " AND (user_id = %s OR user_id IS NULL OR source_type = 'identity')"
        )
        params = params + [user_id]

    return base, params
