"""DLP enforcement — builds SQL WHERE clauses for clearance levels.

DLP levels:
- public: visible to ALL agents
- internal: visible to admin + internal agents
- confidential: visible only to the owning agent

Clearance levels:
- admin: sees own confidential + all internal/public
- internal: sees all internal/public
- external: sees only public
"""

from typing import List, Tuple


def dlp_where(clearance: str, agent_name: str) -> Tuple[str, List[str]]:
    """Build a DLP-filtered WHERE clause for SQL queries.

    Returns:
        Tuple of (where_clause, params) where params are for %s placeholders.
    """
    if clearance == "admin":
        return (
            "((dlp_level = 'confidential' AND agent_name = %s) OR dlp_level IN ('internal', 'public'))",
            [agent_name],
        )
    elif clearance == "internal":
        return (
            "dlp_level IN ('internal', 'public')",
            [],
        )
    else:  # external
        return (
            "dlp_level = 'public'",
            [],
        )
