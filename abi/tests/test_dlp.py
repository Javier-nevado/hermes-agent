"""Unit tests for DLP clearance → WHERE clause mapping.

Pure-Python — no DB. Pins the four clearance tiers, especially the
``confidential`` branch that previously fell through to ``else`` (external =
public-only), silently blinding a confidential-cleared agent.

    PYTHONPATH=. python3 -m pytest abi/tests/test_dlp.py -v
"""

from __future__ import annotations

import unittest

from abi.memory.dlp import dlp_where


class TestDlpWhere(unittest.TestCase):
    def test_admin_sees_own_confidential_plus_shared(self):
        clause, params = dlp_where("admin", "atlas")
        self.assertIn("dlp_level = 'confidential'", clause)
        self.assertIn("agent_name = %s", clause)
        self.assertIn("internal", clause)
        self.assertIn("public", clause)
        self.assertEqual(params, ["atlas"])

    def test_internal_sees_no_confidential(self):
        clause, params = dlp_where("internal", "iris")
        self.assertNotIn("confidential", clause)
        self.assertIn("internal", clause)
        self.assertIn("public", clause)
        self.assertEqual(params, [])

    def test_external_sees_only_public(self):
        clause, params = dlp_where("external", "guest")
        self.assertEqual(clause, "dlp_level = 'public'")
        self.assertEqual(params, [])

    def test_confidential_does_not_fall_through_to_public_only(self):
        # Regression: 'confidential' clearance used to hit the `else` branch and
        # get public-only (strictly less than an internal agent). It must now see
        # its own confidential + internal/public.
        clause, params = dlp_where("confidential", "vera")
        self.assertNotEqual(clause, "dlp_level = 'public'")
        self.assertIn("confidential", clause)
        self.assertEqual(params, ["vera"])

    def test_confidential_dlp_equivalent_to_admin(self):
        # Same DLP view as admin (own confidential + internal/public) — no
        # cross-agent confidential visibility is granted.
        c_clause, c_params = dlp_where("confidential", "vera")
        a_clause, a_params = dlp_where("admin", "vera")
        self.assertEqual(c_clause, a_clause)
        self.assertEqual(c_params, a_params)

    def test_unknown_clearance_treated_as_external(self):
        # An unmapped clearance must fail safe (public-only), not leak.
        clause, params = dlp_where("superuser", "x")
        self.assertEqual(clause, "dlp_level = 'public'")
        self.assertEqual(params, [])


if __name__ == "__main__":
    unittest.main()
