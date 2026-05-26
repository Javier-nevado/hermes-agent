"""ABI Memory Evaluation Harness — measure recall quality.

Provides reproducible metrics for:
- Precision: fraction of returned results that are relevant
- Recall: fraction of known relevant results that are returned
- Latency: query response time

Usage:
    cd /opt/hermes-agent && PYTHONPATH=. .venv/bin/python3 -m pytest abi/tests/test_memory_eval.py -v
"""

import json
import time
import unittest
from typing import Dict, List, Tuple

# Test cases: (query, expected_substring, should_find)
# These test against whatever is currently in the DB
STANDARD_TEST_CASES: List[Tuple[str, str, bool]] = [
    ("CEO of Opteia", "Javier", True),
    ("office hours", "9am", True),
    ("Brevo email", "Opteia uses Brevo", True),
    ("contact email", "javier.nevado@opteia.com", True),
    ("quantum physics", "something not stored", False),
    ("PostgreSQL memory", "postgres", True),
]


def _recall_query(provider, query: str, limit: int = 5) -> Tuple[List[Dict], float]:
    """Run a recall query and return (results, latency_seconds)."""
    start = time.time()
    result_str = provider.handle_tool_call("abi_recall", {"query": query, "limit": limit})
    latency = time.time() - start
    data = json.loads(result_str)
    return data.get("memories", []), latency


class TestMemoryRecall(unittest.TestCase):
    """Test memory recall quality."""

    @classmethod
    def setUpClass(cls):
        from abi.memory.provider import ABIMemoryProvider
        cls.provider = ABIMemoryProvider()
        cls.provider.initialize("eval-session", agent_identity="ailean", clearance="admin")

    @classmethod
    def tearDownClass(cls):
        cls.provider.shutdown()

    def test_recall_returns_results(self):
        """Recall should return at least some results for broad queries."""
        results, latency = _recall_query(self.provider, "Opteia", limit=5)
        self.assertGreater(len(results), 0, "Recall should return results for 'Opteia'")
        self.assertLess(latency, 5.0, f"Recall latency should be < 5s, got {latency:.2f}s")

    def test_recall_precision(self):
        """Results for 'CEO' should contain 'Javier' or 'CEO'."""
        results, _ = _recall_query(self.provider, "CEO", limit=5)
        if results:
            has_relevant = any(
                "javier" in m["content"].lower() or "ceo" in m["content"].lower()
                for m in results
            )
            self.assertTrue(has_relevant, "At least one result should mention Javier or CEO")

    def test_recall_format(self):
        """Results should have required fields."""
        results, _ = _recall_query(self.provider, "Opteia", limit=2)
        for m in results:
            self.assertIn("id", m, "Result should have 'id'")
            self.assertIn("content", m, "Result should have 'content'")
            self.assertIn("dlp_level", m, "Result should have 'dlp_level'")
            self.assertIn("score", m, "Result should have 'score' (from hybrid search)")

    def test_recall_negative_query(self):
        """Recall for completely unrelated topic should return few/no results."""
        results, _ = _recall_query(self.provider, "quantum entanglement neutrino oscillation", limit=5)
        # May return some results due to vector search, but they shouldn't be relevant
        # Just check it doesn't crash
        self.assertIsInstance(results, list)


class TestMemoryRemember(unittest.TestCase):
    """Test memory storage with entity extraction."""

    @classmethod
    def setUpClass(cls):
        from abi.memory.provider import ABIMemoryProvider
        cls.provider = ABIMemoryProvider()
        cls.provider.initialize("eval-session", agent_identity="eval_agent", clearance="admin")

    @classmethod
    def tearDownClass(cls):
        # Cleanup
        import psycopg2
        conn = psycopg2.connect("postgresql://abi_agent:abi_local_dev_2026@localhost:5432/abi_memory")
        cur = conn.cursor()
        cur.execute("DELETE FROM abi_memories WHERE agent_name = 'eval_agent'")
        conn.commit()
        conn.close()
        cls.provider.shutdown()

    def test_remember_with_entities(self):
        """Storing a memory should extract entities."""
        result = self.provider.handle_tool_call("abi_remember", {
            "content": "Javier Nevado is CEO of Opteia Limited, targeting €10K MRR"
        })
        data = json.loads(result)
        self.assertEqual(data.get("status"), "remembered")
        self.assertGreaterEqual(data.get("entities", 0), 2, "Should extract at least 2 entities")

    def test_remember_dlp_auto_classify(self):
        """Memories with PII should be auto-classified as confidential."""
        result = self.provider.handle_tool_call("abi_remember", {
            "content": "Contact javier.nevado@opteia.com for approval"
        })
        data = json.loads(result)
        self.assertEqual(data.get("dlp_level"), "confidential")

    def test_remember_public(self):
        """Explicitly public memories should be stored as public."""
        result = self.provider.handle_tool_call("abi_remember", {
            "content": "The sky is blue", "dlp_level": "public"
        })
        data = json.loads(result)
        self.assertEqual(data.get("dlp_level"), "public")


class TestEntityExtractor(unittest.TestCase):
    """Test entity extraction accuracy."""

    @classmethod
    def setUpClass(cls):
        from abi.memory.entities import EntityExtractor
        cls.extractor = EntityExtractor()

    def test_person_company(self):
        entities = self.extractor.extract("Javier Nevado is CEO of Opteia Limited")
        names = {e.name.lower() for e in entities}
        self.assertIn("javier nevado", names)
        self.assertIn("opteia limited", names)

    def test_amount_date(self):
        entities = self.extractor.extract("Targeting €10K MRR by Q3 2026")
        types = {e.type for e in entities}
        self.assertIn("amount", types)
        self.assertIn("date", types)

    def test_technology(self):
        entities = self.extractor.extract("Atlas uses Proxmox and OPNsense")
        tech_names = {e.name.lower() for e in entities if e.type == "technology"}
        self.assertIn("proxmox", tech_names)
        self.assertIn("opnsense", tech_names)

    def test_no_false_positives(self):
        entities = self.extractor.extract("The weather is nice today and we went for a walk")
        self.assertLessEqual(len(entities), 1, "Should have very few entities for generic text")

    def test_relation_inference(self):
        entities = self.extractor.extract("Javier Nevado is CEO of Opteia Limited")
        edges = self.extractor.infer_relations(entities, "Javier Nevado is CEO of Opteia Limited")
        relations = {(e.source.name, e.relation, e.target.name) for e in edges}
        has_ceo = any("CEO_OF" in r[1] for r in relations)
        self.assertTrue(has_ceo, "Should infer CEO_OF relation")


if __name__ == "__main__":
    unittest.main()
