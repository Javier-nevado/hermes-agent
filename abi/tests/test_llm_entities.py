"""Unit tests for the LLM entity-extraction gate (dreamer nightly batch).

Covers ``_canonical_entity_type`` (the synonym → allowed-type map that protects
the ``abi_entities.type`` CHECK constraint) and ``parse_llm_entity_items`` (LLM
JSON validation). Pure-Python — no FastAPI / psycopg2 / ONNX:

    PYTHONPATH=. python3 -m pytest abi/tests/test_llm_entities.py -v
"""

from __future__ import annotations

import unittest

from abi.memory.entities import (
    Entity,
    LLM_ENTITY_TYPES,
    _canonical_entity_type,
    parse_llm_entity_items,
)


class CanonicalEntityTypeTest(unittest.TestCase):
    """The LLM is the noisy input; this is the deterministic gate before the DB."""

    def test_allowed_types_pass_through(self) -> None:
        for t in LLM_ENTITY_TYPES:
            self.assertEqual(_canonical_entity_type(t), t)
            self.assertEqual(_canonical_entity_type(t.capitalize()), t)
            self.assertEqual(_canonical_entity_type(t.upper()), t)

    def test_synonyms_fold_to_allowed(self) -> None:
        # The live ``metric`` → constraint-violation bug is the canonical case.
        cases = {
            "organization": "company", "organisation": "company", "org": "company",
            "team": "company", "group": "company", "department": "company",
            "metric": "other", "metrics": "other", "kpi": "other", "count": "other",
            "event": "other", "meeting": "other", "milestone": "other",
            "tool": "technology", "tools": "technology", "framework": "technology",
            "software": "technology", "platform": "technology",
            "service": "product", "feature": "product", "app": "product",
            "place": "location", "country": "location", "city": "location",
            "money": "amount", "currency": "amount", "revenue": "amount",
            "people": "person", "user": "person", "customer": "person",
            "initiative": "project", "task": "project",
            "title": "role", "position": "role",
            "time": "date", "period": "date", "deadline": "date",
        }
        for raw, expected in cases.items():
            self.assertEqual(
                _canonical_entity_type(raw), expected,
                f"synonym {raw!r} should map to {expected!r}",
            )

    def test_plural_forms(self) -> None:
        # Precomputed plurals: company→companies (y→ies), technology→technologies.
        self.assertEqual(_canonical_entity_type("companies"), "company")
        self.assertEqual(_canonical_entity_type("technologies"), "technology")
        self.assertEqual(_canonical_entity_type("products"), "product")
        self.assertEqual(_canonical_entity_type("roles"), "role")
        # Ad-hoc plurals of synonyms via the suffix-stripping fallback.
        self.assertEqual(_canonical_entity_type("frameworks"), "technology")
        self.assertEqual(_canonical_entity_type("positions"), "role")
        self.assertEqual(_canonical_entity_type("organizations"), "company")

    def test_junk_returns_none(self) -> None:
        for raw in ("", "   ", "xyzzy", "flibbertigibbet", None):
            self.assertIsNone(_canonical_entity_type(raw), f"{raw!r} should drop")


class ParseLlmEntityItemsTest(unittest.TestCase):
    def test_valid_items(self) -> None:
        data = {"items": [{"id": "mem-1", "entities": [
            {"name": "PostgreSQL", "type": "technology"},
            {"name": "€149/mo", "type": "amount"},
        ]}]}
        out = parse_llm_entity_items(data)
        self.assertEqual(set(out.keys()), {"mem-1"})
        ents = out["mem-1"]
        self.assertEqual(ents, [Entity("PostgreSQL", "technology"), Entity("€149/mo", "amount")])

    def test_synonym_type_folded(self) -> None:
        data = {"items": [{"id": "1", "entities": [
            {"name": "Acme Inc", "type": "organization"},
            {"name": "29KB", "type": "metric"},
        ]}]}
        out = parse_llm_entity_items(data)
        self.assertEqual(out["1"], [Entity("Acme Inc", "company"), Entity("29KB", "other")])

    def test_junk_filtered(self) -> None:
        data = {"items": [{"id": "1", "entities": [
            {"name": "A", "type": "person"},            # too short (<2)
            {"name": "x" * 81, "type": "person"},       # too long (>80)
            {"name": "12345", "type": "amount"},         # no letter
            {"name": "Good", "type": "xyzzy"},           # bad type → None
            {"name": "Kept", "type": "role"},            # survives
        ]}]}
        out = parse_llm_entity_items(data)
        self.assertEqual(out["1"], [Entity("Kept", "role")])

    def test_duplicate_within_memory_collapsed(self) -> None:
        data = {"items": [{"id": "1", "entities": [
            {"name": "Javier", "type": "person"},
            {"name": "javier", "type": "person"},  # case-differs but same key
        ]}]}
        out = parse_llm_entity_items(data)
        self.assertEqual(len(out["1"]), 1)

    def test_malformed_handled_gracefully(self) -> None:
        # Never raises — bad items just don't appear.
        self.assertEqual(parse_llm_entity_items(None), {})
        self.assertEqual(parse_llm_entity_items("not a dict"), {})
        self.assertEqual(parse_llm_entity_items({}), {})
        self.assertEqual(parse_llm_entity_items({"items": "notalist"}), {})
        out = parse_llm_entity_items({"items": ["notadict", {"id": "", "entities": []}]})
        self.assertEqual(out, {})
        # missing id / missing entities → skipped, no crash
        out = parse_llm_entity_items({"items": [{"entities": [{"name": "X", "type": "role"}]}]})
        self.assertEqual(out, {})

    def test_multiple_items(self) -> None:
        data = {"items": [
            {"id": "a", "entities": [{"name": "Atlas", "type": "technology"}]},
            {"id": "b", "entities": [{"name": "Malta", "type": "location"}]},
        ]}
        out = parse_llm_entity_items(data)
        self.assertEqual(set(out.keys()), {"a", "b"})
        self.assertEqual(out["a"], [Entity("Atlas", "technology")])
        self.assertEqual(out["b"], [Entity("Malta", "location")])

    def test_empty_entities_list_ok(self) -> None:
        out = parse_llm_entity_items({"items": [{"id": "1", "entities": []}]})
        self.assertEqual(out, {"1": []})


if __name__ == "__main__":
    unittest.main()
