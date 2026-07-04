"""Tests for intent-gated phantom file detection and existence-based resolution.

These pin the pure helpers in ``agent.tool_result_classification``:

* :func:`extract_claimed_deliverable_files` -- only flag filenames that appear
  in a write/creation context (intent-gated), not reads/references/URLs/code.
* :func:`resolve_claimed_file_to_path` -- method-agnostic existence check that
  delegates safety to the delivery pipeline's ``validate_media_delivery_path``.

The full conversation-loop preserve-and-retry path is an integration concern
covered by manual e2e; these unit tests cover the decision logic.
"""

from __future__ import annotations

import os
import time

import pytest

from agent.tool_result_classification import (
    extract_claimed_deliverable_files,
    resolve_claimed_file_to_path,
)


# ---------------------------------------------------------------------------
# Intent gating
# ---------------------------------------------------------------------------
class TestIntentGating:
    def test_claim_with_created(self):
        out = extract_claimed_deliverable_files("I have created report.pdf for you.")
        assert any("report.pdf" in c for c in out)

    @pytest.mark.parametrize(
        "verb",
        [
            "saved",
            "wrote",
            "written",
            "generated",
            "downloaded",
            "delivered",
            "exported",
            "produced",
            "attached",
            "uploaded",
        ],
    )
    def test_claim_verbs_bind(self, verb):
        out = extract_claimed_deliverable_files(f"I {verb} plan.html.")
        assert any("plan.html" in c for c in out), verb

    def test_here_is_your_binds(self):
        out = extract_claimed_deliverable_files("Here's your monthly-report.pdf.")
        assert any("monthly-report.pdf" in c for c in out)

    def test_saved_at_absolute_path(self):
        out = extract_claimed_deliverable_files(
            "The NDA is saved at /home/ailean/MGA-Opteia-NDA.docx."
        )
        assert any("MGA-Opteia-NDA.docx" in c for c in out)

    # --- the real-world false positives that MUST NOT bind -------------
    def test_read_reference_excluded(self):
        # "read" is not a claim verb.
        assert (
            extract_claimed_deliverable_files(
                "I read robots.txt and it is properly configured."
            )
            == []
        )

    def test_skill_load_excluded(self):
        # "load" is not a claim verb.
        assert (
            extract_claimed_deliverable_files("Load the skill at recall-ai.md now.")
            == []
        )

    def test_url_excluded(self):
        # "Downloaded" is a claim verb, but the path is inside a URL -> stripped.
        assert (
            extract_claimed_deliverable_files(
                "Downloaded from https://x.com/report.pdf already."
            )
            == []
        )

    def test_fenced_code_excluded(self):
        assert (
            extract_claimed_deliverable_files("```\nwrite_file('out.pdf', ...)\n```")
            == []
        )

    def test_inline_code_excluded(self):
        assert extract_claimed_deliverable_files("I wrote `report.pdf`.") == []

    def test_proximity_too_far_excluded(self):
        # Intent verb > 120 chars away from the filename -> no binding.
        text = "I created something. " + ("filler " * 40) + " Here is notes.txt."
        out = extract_claimed_deliverable_files(text)
        assert all("notes.txt" not in c for c in out)

    def test_no_extension_no_match(self):
        # A bare word with no deliverable extension is not a file claim.
        assert extract_claimed_deliverable_files("I created the report.") == []


# ---------------------------------------------------------------------------
# Existence resolution (method-agnostic)
# ---------------------------------------------------------------------------
class TestResolveClaimedFile:
    def test_url_returns_none(self):
        assert resolve_claimed_file_to_path("https://x/y.pdf") is None
        assert resolve_claimed_file_to_path("ftp://h/f.txt") is None

    def test_absolute_existing_file(self, tmp_path, monkeypatch):
        f = tmp_path / "real.pdf"
        f.write_text("x")
        monkeypatch.setenv("HERMES_MEDIA_DELIVERY_STRICT", "0")  # non-strict (default)
        out = resolve_claimed_file_to_path(str(f))
        assert out is not None
        assert os.path.realpath(str(f)) == os.path.realpath(out)

    def test_absolute_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_MEDIA_DELIVERY_STRICT", "0")
        assert resolve_claimed_file_to_path(str(tmp_path / "nope_zzz.pdf")) is None

    def test_basename_in_allowed_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_MEDIA_ALLOW_DIRS", str(tmp_path))
        monkeypatch.setenv("HERMES_MEDIA_DELIVERY_STRICT", "0")
        f = tmp_path / "found.csv"
        f.write_text("x")
        out = resolve_claimed_file_to_path("found.csv")
        assert out is not None
        assert out.endswith("found.csv")

    def test_basename_not_found_returns_none(self, tmp_path, monkeypatch):
        # Point allow-dirs at an empty dir; unique name that won't exist anywhere.
        monkeypatch.setenv("HERMES_MEDIA_ALLOW_DIRS", str(tmp_path))
        monkeypatch.setenv("HERMES_MEDIA_DELIVERY_STRICT", "0")
        assert resolve_claimed_file_to_path("__definitely_missing_zzz__.pdf") is None

    def test_denylisted_path_returns_none(self, monkeypatch):
        # /etc is on the delivery denylist even in non-strict mode.
        monkeypatch.setenv("HERMES_MEDIA_DELIVERY_STRICT", "0")
        assert resolve_claimed_file_to_path("/etc/passwd") is None


# ---------------------------------------------------------------------------
# Preserve-and-retry stash/restore attrs (lightweight contract check)
# ---------------------------------------------------------------------------
class TestPreserveRestoreAttrs:
    def _bare_agent(self):
        # Mirror the fixture pattern in test_file_mutation_verifier.py.
        from run_agent import AIAgent

        a = object.__new__(AIAgent)
        a._phantom_preserved_response = None
        a._phantom_preserved_claimed = []
        return a

    def test_stash_then_restore_roundtrip(self):
        a = self._bare_agent()
        assert a._phantom_preserved_response is None
        # Stash.
        a._phantom_preserved_response = "ORIGINAL ANALYSIS"
        a._phantom_preserved_claimed = ["report.pdf"]
        # Restore.
        assert a._phantom_preserved_response == "ORIGINAL ANALYSIS"
        assert a._phantom_preserved_claimed == ["report.pdf"]
        # Clear.
        a._phantom_preserved_response = None
        a._phantom_preserved_claimed = []
        assert a._phantom_preserved_response is None
