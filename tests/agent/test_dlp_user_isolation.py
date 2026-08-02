"""Per-user read isolation for dlp_where.

Verifies recall WHERE clauses scope to the requesting user in private (DM) scopes
and stay shared in group/channel scopes — closing the cross-user memory leak on
shared agents. Pure-function tests (no DB needed).

Context: recall used to filter only by (dlp_level, agent_name), so on a shared
agent (e.g. jen/atlas) user B could surface user A's private memories. dlp_where
now also accepts user_id + shared_scope; provider passes chat_type-derived
shared_scope (group/channel = shared team pool, DM = private per-user).
"""

from abi.memory.dlp import dlp_where


class TestDlpUserIsolation:
    def test_dm_scope_filters_to_own_user(self):
        clause, params = dlp_where("confidential", "jen", user_id="alice", shared_scope=False)
        assert "user_id = %s" in clause
        assert "user_id IS NULL" in clause  # ownerless/system memories stay visible
        assert "source_type = 'identity'" in clause  # team anchors stay visible
        assert "alice" in params

    def test_dm_scope_preserves_base_dlp(self):
        clause, params = dlp_where("external", "jen", user_id="alice", shared_scope=False)
        # base public-only clause still applies, ANDed with the user scope
        assert "dlp_level = 'public'" in clause
        assert clause.startswith("(")
        assert ") AND (user_id" in clause
        assert params == ["alice"]

    def test_group_scope_does_not_filter_by_user(self):
        clause, params = dlp_where("confidential", "jen", user_id="alice", shared_scope=True)
        assert "user_id =" not in clause
        assert "alice" not in params  # shared pool — no user restriction

    def test_no_user_id_is_backward_compatible(self):
        # System/cron callers (no user_id) must behave exactly as before the fix.
        clause, params = dlp_where("confidential", "jen")
        assert clause == (
            "((dlp_level = 'confidential' AND agent_name = %s) OR dlp_level IN ('internal', 'public'))"
        )
        assert params == ["jen"]

    def test_user_b_never_sees_user_a_filter(self):
        # The leak scenario: user B recalls on the same shared agent as user A.
        clause_b, params_b = dlp_where("confidential", "jen", user_id="bob", shared_scope=False)
        assert "bob" in params_b
        assert "alice" not in params_b
        assert "alice" not in clause_b

    def test_all_clearances_isolate_when_user_set(self):
        for clearance in ("internal", "admin", "external"):
            clause, params = dlp_where(clearance, "jen", user_id="alice", shared_scope=False)
            assert "user_id = %s" in clause, clearance
            assert "alice" in params, clearance
